"""Minimal Proxmox VE API client.

Written against urllib rather than requests so the CLI keeps its single
dependency: this is a disaster-recovery tool that pip-installs on first run,
and every extra package is another thing that can fail to resolve when you most
need it to work.

Only the calls the sandbox needs are here. The homelab goes through Terraform
instead — the cluster is long-lived infrastructure worth having state for,
whereas a sandbox VM is created and thrown away several times an hour. Keeping
the sandbox out of Terraform also means it shares no state with the cluster, so
a sandbox mistake can never plan a change against the homelab.
"""

from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from .console import fail
from . import onepassword as op

# The API token lives in 1Password, same item Terraform reads.
OP_ITEM = "Proxmox API - talos-home"


class ProxmoxError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class Proxmox:
    def __init__(self, endpoint: str, node: str, token: str, insecure: bool = True):
        self.base = endpoint.rstrip("/") + "/api2/json"
        self.node = node
        self.token = token
        self._ctx = ssl._create_unverified_context() if insecure else None

    # ── plumbing ────────────────────────────────────────────────────────────

    def _request(self, method: str, path: str, data: dict | None = None) -> dict:
        url = f"{self.base}{path}"
        body = None
        headers = {"Authorization": f"PVEAPIToken={self.token}"}

        if data is not None:
            # PVE takes form encoding, not JSON, on write endpoints. Booleans
            # have to go over as 1/0 or the API rejects them as "not a number".
            flat = {
                k: (1 if v is True else 0 if v is False else v)
                for k, v in data.items()
                if v is not None
            }
            body = urllib.parse.urlencode(flat).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"

        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, context=self._ctx, timeout=60) as resp:
                raw = resp.read().decode()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:400]
            raise ProxmoxError(exc.code, f"{method} {path} → {exc.code}: {detail}")
        except urllib.error.URLError as exc:
            raise ProxmoxError(0, f"{method} {path} → cannot reach Proxmox: {exc.reason}")

        return json.loads(raw) if raw.strip() else {}

    def get(self, path: str) -> dict:
        return self._request("GET", path).get("data")

    def post(self, path: str, **data) -> dict:
        return self._request("POST", path, data).get("data")

    def put(self, path: str, **data) -> dict:
        return self._request("PUT", path, data).get("data")

    def delete(self, path: str) -> dict:
        return self._request("DELETE", path).get("data")

    # ── tasks ───────────────────────────────────────────────────────────────

    def wait(self, upid: str, timeout: int = 900, what: str = "task") -> None:
        """Block until an async node task finishes.

        Most write calls return a UPID immediately and do the work in the
        background, so without this a clone looks like it succeeded while the
        disk is still copying and the next call fails on a locked VM.
        """
        if not isinstance(upid, str) or not upid.startswith("UPID"):
            return  # synchronous call, nothing to wait for

        deadline = time.time() + timeout
        quoted = urllib.parse.quote(upid, safe="")
        while time.time() < deadline:
            status = self.get(f"/nodes/{self.node}/tasks/{quoted}/status") or {}
            if status.get("status") == "stopped":
                exit_status = status.get("exitstatus", "")
                if exit_status != "OK":
                    fail(f"{what} failed: {exit_status}")
                return
            time.sleep(2)
        fail(f"{what} did not finish within {timeout}s")

    # ── VMs ─────────────────────────────────────────────────────────────────

    def vms(self) -> list[dict]:
        return self.get(f"/nodes/{self.node}/qemu") or []

    def vm(self, vmid: int) -> dict | None:
        for entry in self.vms():
            if int(entry["vmid"]) == int(vmid):
                return entry
        return None

    def exists(self, vmid: int) -> bool:
        return self.vm(vmid) is not None

    def config(self, vmid: int) -> dict:
        return self.get(f"/nodes/{self.node}/qemu/{vmid}/config") or {}

    def status(self, vmid: int) -> str:
        entry = self.vm(vmid)
        return entry.get("status", "unknown") if entry else "absent"

    def next_id(self) -> int:
        return int(self.get("/cluster/nextid"))

    # ── storage ─────────────────────────────────────────────────────────────

    def content(self, storage: str) -> list[dict]:
        return self.get(f"/nodes/{self.node}/storage/{storage}/content") or []

    def has_volume(self, volid: str) -> bool:
        storage = volid.split(":", 1)[0]
        try:
            return any(c.get("volid") == volid for c in self.content(storage))
        except ProxmoxError:
            return False

    def download_url(self, storage: str, url: str, filename: str, content: str) -> None:
        upid = self.post(
            f"/nodes/{self.node}/storage/{storage}/download-url",
            content=content, filename=filename, url=url,
        )
        self.wait(upid, timeout=1800, what=f"download of {filename}")

    def upload(self, storage: str, path: Path, content: str = "iso") -> None:
        """Upload a local file via multipart/form-data.

        Hand-rolled because urllib has no multipart encoder and adding one
        would mean a new dependency. The body is built in memory, which is fine
        for the payload ISOs this handles (megabytes, not gigabytes).
        """
        boundary = f"----provision{uuid.uuid4().hex}"
        payload = path.read_bytes()

        # `filename` appears exactly once, as the file part. Sending it also as
        # a plain text field makes PVE drop the connection mid-upload, which
        # surfaces as an unhelpful "broken pipe" rather than a 400.
        body = b"".join([
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="content"\r\n\r\n'
            f"{content}\r\n".encode(),
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="filename"; filename="{path.name}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n".encode(),
            payload,
            f"\r\n--{boundary}--\r\n".encode(),
        ])

        req = urllib.request.Request(
            f"{self.base}/nodes/{self.node}/storage/{storage}/upload",
            data=body,
            headers={
                "Authorization": f"PVEAPIToken={self.token}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(len(body)),
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, context=self._ctx, timeout=900) as resp:
                data = json.loads(resp.read().decode() or "{}").get("data")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:400]
            raise ProxmoxError(exc.code, f"upload of {path.name} → {exc.code}: {detail}")
        except urllib.error.URLError as exc:
            raise ProxmoxError(0, f"upload of {path.name} failed: {exc.reason}")

        self.wait(data, timeout=900, what=f"upload of {path.name}")


def token() -> str:
    """Read the API token from 1Password.

    Same item Terraform uses, so there is one credential to rotate rather than
    two that can drift apart.
    """
    value = op.field(OP_ITEM, "password")
    if not value:
        fail(
            f"could not read the Proxmox API token from 1Password item '{OP_ITEM}'. "
            "Check `op whoami` and that the item has a 'password' field."
        )
    return value.strip()


def connect() -> Proxmox:
    """Build a client from provision.toml plus the 1Password token."""
    from . import settings

    data = settings.load()
    pve = data.get("proxmox") or {}
    endpoint = pve.get("endpoint")
    if not endpoint:
        fail("provision.toml: [proxmox] endpoint is required")

    return Proxmox(
        endpoint=endpoint,
        node=pve.get("node_name", "pve"),
        token=token(),
        insecure=bool(pve.get("insecure", True)),
    )
