"""Disposable Ubuntu VM for testing packages you do not trust.

The intended sequence is:

    provision sandbox template          # once, builds the golden image
    provision sandbox new               # clone, boot, cloud-init
    ... install the thing you want to look at ...
    provision sandbox offline           # unplug the virtual cable
    provision sandbox console           # the only way in once offline
    provision sandbox reset             # back to the clean snapshot
    provision sandbox destroy

`offline` disconnects the NIC rather than deleting it, which is the moral
equivalent of unplugging the cable: the VM keeps its config and can be
reconnected without a reboot.

A caveat worth stating plainly: cutting the link afterwards does not undo
anything that ran during install. If a package is actively malicious, the
install is the dangerous moment, and it happens while the VM is on your LAN.
Use `new --offline` plus `payload` when that matters.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import urllib.parse
from pathlib import Path

import click

from .. import settings
from ..console import fail, info, rule, success, warn
from ..proxmox import Proxmox, ProxmoxError, connect

# Snapshot taken once the VM is up, so `reset` is a rollback rather than a
# fresh clone. Rolling back is seconds; cloning is a disk copy.
CLEAN_SNAPSHOT = "clean"

# SCSI rather than IDE: an IDE CD-ROM added to a running VM does not appear
# until the next boot, and the cloud image has no guest agent so an ACPI reboot
# is not reliable either. On the virtio-scsi controller the drive hotplugs, so
# the payload shows up as /dev/sr1 within seconds and no restart is needed.
# ide2 is cloud-init's own drive and is left alone.
PAYLOAD_SLOT = "scsi1"
PAYLOAD_ISO = "sandbox-payload.iso"


def _cfg() -> tuple[Proxmox, settings.Sandbox]:
    return connect(), settings.sandbox()


def _require_vm(api: Proxmox, box: settings.Sandbox) -> None:
    if not api.exists(box.vm_id):
        fail(f"no sandbox VM {box.vm_id}. Run `provision sandbox new` first.")


def _import_filename(url: str) -> str:
    """Local name for the downloaded cloud image.

    Proxmox validates the extension on import-content uploads and rejects
    `.img`, which is exactly what Ubuntu names its cloud images even though
    they are qcow2 underneath. Store it under the name that matches what it
    actually is.
    """
    name = url.rsplit("/", 1)[-1]
    if name.endswith(".img"):
        return name[: -len(".img")] + ".qcow2"
    return name


def _ssh_keys(box: settings.Sandbox) -> str | None:
    """Public keys to authorise for the sandbox user.

    Without one, cloud-init creates the account with no password and no key,
    and the only way in is the console — which is not much of a sandbox. These
    are public keys, so collecting them from the agent leaks nothing.
    """
    value = (box.ssh_keys or "").strip()
    if value and value != "auto":
        candidate = Path(value).expanduser()
        return candidate.read_text() if candidate.is_file() else value

    result = subprocess.run(["ssh-add", "-L"], capture_output=True, text=True)
    keys = [
        line for line in result.stdout.splitlines()
        if line.startswith(("ssh-", "ecdsa-", "sk-"))
    ]
    if not keys:
        keys = [
            p.read_text().strip()
            for p in sorted(Path.home().joinpath(".ssh").glob("*.pub"))
        ]

    if not keys:
        warn("no SSH public key found — the sandbox will only be reachable by console")
        return None

    # PVE wants this parameter percent-encoded in its own right, on top of the
    # form encoding the request already applies — pass it raw and the API
    # rejects it with "invalid urlencoded string".
    return urllib.parse.quote("\n".join(keys) + "\n", safe="")


def _net_string(box: settings.Sandbox, disconnected: bool) -> str:
    parts = [f"virtio,bridge={box.bridge}"]
    if disconnected:
        parts.append("link_down=1")
    return ",".join(parts)


@click.group()
def sandbox() -> None:
    """Throwaway VM for testing untrusted packages.

    \b
    Build the image once, then:
      ./provision sandbox new
      ./provision sandbox offline
      ./provision sandbox destroy
    """


# ── template ────────────────────────────────────────────────────────────────


@sandbox.command("template")
@click.option("--force", is_flag=True, help="Rebuild even if the template exists.")
def sandbox_template(force: bool) -> None:
    """Build the Ubuntu cloud-init template the sandbox clones from."""
    api, box = _cfg()
    rule("Sandbox template")

    if api.exists(box.template_id):
        if not force:
            success(f"template {box.template_id} already exists")
            return
        warn(f"removing existing template {box.template_id}")
        api.wait(api.delete(f"/nodes/{api.node}/qemu/{box.template_id}"), what="delete")

    filename = _import_filename(box.image_url)
    volid = f"local:import/{filename}"

    if api.has_volume(volid):
        info(f"cloud image already downloaded: {volid}")
    else:
        info(f"downloading {box.image_url}")
        # content=import is what PVE 8.3+ calls a disk image you intend to
        # attach; it is the only content type that import-from accepts.
        api.download_url("local", box.image_url, filename, content="import")
        success("cloud image downloaded")

    info(f"creating VM {box.template_id}")
    api.wait(
        api.post(
            f"/nodes/{api.node}/qemu",
            vmid=box.template_id,
            name="sandbox-template",
            memory=box.memory,
            cores=box.cpu_cores,
            cpu="host",
            net0=_net_string(box, disconnected=False),
            # import-from converts the qcow2 cloud image into a real disk on
            # local-lvm. Without it the image would have to be copied by hand
            # over SSH, which is the one thing this design avoids.
            scsi0=f"{box.datastore_id}:0,import-from={volid}",
            scsihw="virtio-scsi-single",
            ide2=f"{box.datastore_id}:cloudinit",
            boot="order=scsi0",
            serial0="socket",
            vga="serial0",
            agent=1,
            ostype="l26",
            description="Sandbox golden image — managed by ./provision sandbox",
        ),
        timeout=1800,
        what="template creation",
    )

    api.wait(
        api.post(f"/nodes/{api.node}/qemu/{box.template_id}/template"),
        what="template conversion",
    )
    success(f"template {box.template_id} ready")


# ── lifecycle ───────────────────────────────────────────────────────────────


@sandbox.command("new")
@click.option("--offline", is_flag=True, help="Boot with the NIC already disconnected.")
@click.option("--replace", is_flag=True, help="Destroy an existing sandbox first.")
def sandbox_new(offline: bool, replace: bool) -> None:
    """Clone the template into a fresh sandbox and boot it."""
    api, box = _cfg()
    rule("Sandbox")

    if not api.exists(box.template_id):
        fail(
            f"template {box.template_id} does not exist. "
            "Run `provision sandbox template` first."
        )

    if api.exists(box.vm_id):
        if not replace:
            fail(
                f"sandbox VM {box.vm_id} already exists. "
                "Use --replace, or `provision sandbox destroy`."
            )
        _destroy(api, box)

    info(f"cloning template {box.template_id} → {box.vm_id}")
    api.wait(
        api.post(
            f"/nodes/{api.node}/qemu/{box.template_id}/clone",
            newid=box.vm_id, name="sandbox", full=1, target=api.node,
        ),
        timeout=1800,
        what="clone",
    )

    nameservers = " ".join(box.nameservers) if box.nameservers else None
    api.put(
        f"/nodes/{api.node}/qemu/{box.vm_id}/config",
        memory=box.memory,
        cores=box.cpu_cores,
        ciuser=box.username,
        sshkeys=_ssh_keys(box),
        ipconfig0=f"ip={box.ip}/{box.subnet_prefix},gw={box.gateway}",
        nameserver=nameservers,
        net0=_net_string(box, disconnected=offline),
    )

    if box.disk_size:
        try:
            api.put(
                f"/nodes/{api.node}/qemu/{box.vm_id}/resize",
                disk="scsi0", size=f"{box.disk_size}G",
            )
        except ProxmoxError as exc:
            # Shrinking is refused; that is fine, the image default is usable.
            warn(f"could not resize disk to {box.disk_size}G: {exc.message.splitlines()[0]}")

    info("starting")
    api.wait(api.post(f"/nodes/{api.node}/qemu/{box.vm_id}/status/start"), what="start")

    # Best-effort: the snapshot only powers `sandbox reset`, so a token without
    # VM.Snapshot should still get a working sandbox rather than a half-built
    # one plus a stack trace.
    try:
        api.wait(
            api.post(
                f"/nodes/{api.node}/qemu/{box.vm_id}/snapshot",
                snapname=CLEAN_SNAPSHOT,
                description="Clean state, taken right after first boot.",
                vmstate=0,
            ),
            what="snapshot",
        )
    except ProxmoxError as exc:
        if exc.status == 403:
            warn("no clean snapshot: the API token lacks VM.Snapshot")
            warn("`sandbox reset` will not work — add VM.Snapshot and "
                 "VM.Snapshot.Rollback to the token's role to enable it")
        else:
            raise

    success(f"sandbox {box.vm_id} running at {box.ip}" + (" (offline)" if offline else ""))
    if offline:
        info("NIC is disconnected — reach it with `provision sandbox console`")
    else:
        info(f"ssh {box.username}@{box.ip}   (cloud-init may take ~30s)")


@sandbox.command("destroy")
@click.option("--yes", is_flag=True, help="Skip the confirmation.")
def sandbox_destroy(yes: bool) -> None:
    """Stop and delete the sandbox VM."""
    api, box = _cfg()
    if not api.exists(box.vm_id):
        success(f"no sandbox VM {box.vm_id} — nothing to destroy")
        return

    if not yes:
        click.confirm(f"Destroy sandbox VM {box.vm_id}?", abort=True)
    _destroy(api, box)
    success(f"sandbox {box.vm_id} destroyed")


def _destroy(api: Proxmox, box: settings.Sandbox) -> None:
    if api.status(box.vm_id) == "running":
        info("stopping")
        api.wait(api.post(f"/nodes/{api.node}/qemu/{box.vm_id}/status/stop"), what="stop")
    info(f"deleting VM {box.vm_id}")
    api.wait(
        api.delete(f"/nodes/{api.node}/qemu/{box.vm_id}?destroy-unreferenced-disks=1&purge=1"),
        what="delete",
    )


@sandbox.command("reset")
def sandbox_reset() -> None:
    """Roll back to the clean snapshot taken at first boot."""
    api, box = _cfg()
    _require_vm(api, box)

    snapshots = api.get(f"/nodes/{api.node}/qemu/{box.vm_id}/snapshot") or []
    if not any(s.get("name") == CLEAN_SNAPSHOT for s in snapshots):
        fail(
            f"no '{CLEAN_SNAPSHOT}' snapshot on VM {box.vm_id}.\n"
            "  If the token lacks VM.Snapshot, add it (with VM.Snapshot.Rollback)\n"
            "  to its role in Proxmox. Otherwise rebuild with\n"
            "  `provision sandbox new --replace`."
        )

    info(f"rolling back to '{CLEAN_SNAPSHOT}'")
    api.wait(
        api.post(f"/nodes/{api.node}/qemu/{box.vm_id}/snapshot/{CLEAN_SNAPSHOT}/rollback"),
        timeout=600,
        what="rollback",
    )
    if api.status(box.vm_id) != "running":
        api.wait(api.post(f"/nodes/{api.node}/qemu/{box.vm_id}/status/start"), what="start")
    success("rolled back to clean state")


# ── network ─────────────────────────────────────────────────────────────────


@sandbox.command("offline")
def sandbox_offline() -> None:
    """Disconnect the sandbox from the network."""
    api, box = _cfg()
    _require_vm(api, box)
    api.put(f"/nodes/{api.node}/qemu/{box.vm_id}/config", net0=_net_string(box, True))
    success("network disconnected")
    info("the VM is now only reachable via `provision sandbox console`")


@sandbox.command("online")
@click.option("--reboot", is_flag=True, help="Reboot so the guest reconfigures its NIC.")
def sandbox_online(reboot: bool) -> None:
    """Reconnect the sandbox to the network.

    Reconnecting the cable is not enough on its own. Ubuntu configures the
    static address through systemd-networkd, which flushes it when the carrier
    drops and does not put it back when the carrier returns — so the link comes
    up and the guest still has no address. --reboot is the deterministic fix;
    otherwise run `sudo netplan apply` on the console.
    """
    api, box = _cfg()
    _require_vm(api, box)
    api.put(f"/nodes/{api.node}/qemu/{box.vm_id}/config", net0=_net_string(box, False))
    success("link reconnected")

    if api.status(box.vm_id) != "running":
        info(f"start it to come back at {box.ip}")
        return

    if reboot:
        info("rebooting so the guest reconfigures")
        api.wait(api.post(f"/nodes/{api.node}/qemu/{box.vm_id}/status/reboot"), what="reboot")
        success(f"rebooted — {box.ip} should answer shortly")
    else:
        warn(f"the guest will not have an address until it reconfigures {box.ip}")
        warn("run `sudo netplan apply` on the console, or re-run with --reboot")


# ── payload ─────────────────────────────────────────────────────────────────


@sandbox.command("payload")
@click.argument("directory", type=click.Path(exists=True, file_okay=False, path_type=Path))
def sandbox_payload(directory: Path) -> None:
    """Build DIRECTORY into an ISO and attach it to the sandbox.

    Read-only by design: whatever you are testing cannot write back to the
    payload, and you never mount an untrusted filesystem on the Proxmox host.
    """
    api, box = _cfg()
    _require_vm(api, box)

    maker = _iso_tool()
    with tempfile.TemporaryDirectory() as tmp:
        iso = Path(tmp) / PAYLOAD_ISO
        info(f"building {iso.name} from {directory}")
        maker(directory, iso)
        size_mb = iso.stat().st_size / 1024 / 1024
        info(f"uploading {size_mb:.1f} MB to local")
        api.upload("local", iso, content="iso")

    api.put(
        f"/nodes/{api.node}/qemu/{box.vm_id}/config",
        **{PAYLOAD_SLOT: f"local:iso/{PAYLOAD_ISO},media=cdrom"},
    )
    success("payload attached")
    info("inside the VM:  sudo mount /dev/sr1 /mnt   (read-only)")


def _iso_tool():
    """Pick an ISO builder that is already installed.

    macOS ships hdiutil, so the common case needs nothing extra; Linux hosts
    generally have xorriso or genisoimage.
    """
    if shutil.which("hdiutil"):
        def build(src: Path, out: Path) -> None:
            subprocess.run(
                ["hdiutil", "makehybrid", "-iso", "-joliet", "-o", str(out), str(src)],
                check=True, capture_output=True,
            )
        return build

    for tool in ("xorrisofs", "genisoimage", "mkisofs"):
        if shutil.which(tool):
            def build(src: Path, out: Path, _tool=tool) -> None:
                subprocess.run(
                    [_tool, "-quiet", "-J", "-r", "-o", str(out), str(src)],
                    check=True, capture_output=True,
                )
            return build

    fail("no ISO builder found. Install xorriso (Linux); macOS has hdiutil built in.")


# ── status ──────────────────────────────────────────────────────────────────


@sandbox.command("status")
def sandbox_status() -> None:
    """Show whether the sandbox exists, is running and is connected."""
    api, box = _cfg()
    rule("Sandbox")

    template = "present" if api.exists(box.template_id) else "MISSING — run `sandbox template`"
    click.echo(f"  template {box.template_id}   {template}")

    if not api.exists(box.vm_id):
        click.echo(f"  vm {box.vm_id}         absent")
        return

    config = api.config(box.vm_id)
    net = config.get("net0", "")
    linked = "disconnected" if "link_down=1" in net else "connected"
    snapshots = [
        s.get("name") for s in (api.get(f"/nodes/{api.node}/qemu/{box.vm_id}/snapshot") or [])
        if s.get("name") != "current"
    ]

    click.echo(f"  vm {box.vm_id}         {api.status(box.vm_id)}")
    click.echo(f"  network        {linked}  ({box.ip})")
    click.echo(f"  memory/cores   {config.get('memory')} MB / {config.get('cores')}")
    click.echo(f"  snapshots      {', '.join(snapshots) or 'none'}")
    click.echo(f"  payload        {'attached' if config.get(PAYLOAD_SLOT) else 'none'}")


# ── console ─────────────────────────────────────────────────────────────────


@sandbox.command("console")
def sandbox_console() -> None:
    """Explain how to reach the serial console.

    Proxmox's serial console needs a shell on the host, which this CLI
    deliberately does not have — everything else here goes over the API. So
    this prints the one command to run rather than pretending otherwise.
    """
    api, box = _cfg()
    _require_vm(api, box)
    pve = settings.load().get("proxmox") or {}
    host = (pve.get("endpoint") or "").split("//")[-1].split(":")[0]
    user = pve.get("ssh_username", "root")

    rule("Console")
    click.echo("  Serial console (works with the network disconnected):")
    click.echo(f"    ssh {user}@{host} -t 'qm terminal {box.vm_id}'")
    click.echo("    (exit with Ctrl-O)")
    click.echo()
    click.echo("  Or the web console:")
    click.echo(f"    {pve.get('endpoint', '').rstrip('/')}/?console=kvm&novnc=1&vmid={box.vm_id}&node={api.node}")
