"""Load provision.toml and project it into the files Terraform and Flux read.

provision.toml is the single source of truth. Two things are generated from it:

    terraform/terraform.tfvars.json    infrastructure config (gitignored)
    kubernetes/apps/kustomization.yaml which apps Flux deploys (committed)

Neither generated file should be edited by hand.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .console import fail
from .tf import repo_root

CONFIG_NAME = "provision.toml"

# Node keys copied straight through to the terraform `nodes` map.
_NODE_KEYS = (
    "vm_id", "ip", "mac_address", "machine_type", "cpu_cores", "cpu_type",
    "memory_dedicated", "memory_floating", "disk_size", "datastore_id",
    "install_disk", "interface", "bridge", "start_on_boot",
)

# (toml table, toml key) -> terraform variable
_SCALARS = {
    ("cluster", "name"): "cluster_name",
    ("cluster", "talos_version"): "talos_version",
    ("cluster", "kubernetes_version"): "kubernetes_version",
    ("cluster", "tailnet_domain"): "tailnet_domain",
    ("cluster", "endpoint"): "cluster_endpoint",
    ("proxmox", "endpoint"): "proxmox_endpoint",
    ("proxmox", "node_name"): "proxmox_node_name",
    ("proxmox", "ssh_username"): "proxmox_ssh_username",
    ("proxmox", "iso_datastore_id"): "proxmox_iso_datastore_id",
    ("proxmox", "insecure"): "proxmox_insecure",
    ("network", "gateway"): "gateway",
    ("network", "subnet_prefix"): "subnet_prefix",
    ("network", "nameservers"): "nameservers",
    ("network", "lan_subnet"): "lan_subnet",
    ("tailscale", "enabled"): "tailscale_enabled",
    ("tailscale", "tailnet"): "tailscale_tailnet",
    ("tailscale", "tags"): "tailscale_tags",
    ("unifi", "enabled"): "unifi_enabled",
    ("unifi", "api_url"): "unifi_api_url",
    ("unifi", "site"): "unifi_site",
    ("unifi", "insecure"): "unifi_insecure",
    ("unifi", "network_id"): "unifi_network_id",
    ("onepassword", "vault"): "op_vault",
}

PLACEHOLDER = re.compile(r"<[a-z0-9-]+>")


@dataclass(frozen=True)
class App:
    name: str
    enabled: bool


def config_path() -> Path:
    return repo_root() / CONFIG_NAME


def apps_dir() -> Path:
    return repo_root() / "kubernetes" / "apps"


def kustomization_path() -> Path:
    return apps_dir() / "kustomization.yaml"


def tfvars_json_path() -> Path:
    return repo_root() / "terraform" / "terraform.tfvars.json"


def load() -> dict:
    path = config_path()
    if not path.exists():
        fail(f"{path} not found")
    try:
        return tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        fail(f"{path}: {exc}")


def apps() -> list[App]:
    """Apps in file order."""
    table = load().get("apps") or {}
    if not isinstance(table, dict):
        fail("provision.toml: [apps] must be a table of name = true/false")

    result = []
    for name, enabled in table.items():
        if not isinstance(enabled, bool):
            fail(f"provision.toml: apps.{name} must be true or false")
        result.append(App(name=str(name), enabled=enabled))
    return result


def get_app(name: str) -> App:
    for app in apps():
        if app.name == name:
            return app
    fail(f"unknown app '{name}'. Known: {', '.join(a.name for a in apps())}")


def on_disk() -> set[str]:
    directory = apps_dir()
    if not directory.is_dir():
        return set()
    return {p.name for p in directory.iterdir() if p.is_dir()}


def tfvars() -> dict:
    """Project provision.toml onto the terraform variables."""
    data = load()
    out: dict = {}

    for (table, key), var in _SCALARS.items():
        section = data.get(table) or {}
        if key in section:
            out[var] = section[key]

    nodes = data.get("nodes") or {}
    if not nodes:
        fail("provision.toml: at least one [nodes.<hostname>] table is required")

    out["nodes"] = {
        name: {k: v for k, v in body.items() if k in _NODE_KEYS}
        for name, body in nodes.items()
    }

    for name, body in nodes.items():
        unknown = set(body) - set(_NODE_KEYS)
        if unknown:
            fail(f"provision.toml: [nodes.{name}] has unknown keys: {', '.join(sorted(unknown))}")

    return out


def unfilled() -> list[str]:
    """Find `<placeholder>` values still left in the config."""
    found: list[str] = []

    def walk(value, path: str) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                walk(item, f"{path}.{key}" if path else key)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")
        elif isinstance(value, str) and PLACEHOLDER.search(value):
            found.append(f"{path} = {value!r}")

    data = load()
    for table in ("cluster", "proxmox", "network", "unifi", "nodes"):
        if table in data:
            walk(data[table], table)
    return found


def write_tfvars() -> bool:
    """Write terraform.tfvars.json. Returns True when it changed."""
    content = json.dumps(tfvars(), indent=2, sort_keys=True) + "\n"
    path = tfvars_json_path()

    if path.exists() and path.read_text() == content:
        return False

    path.write_text(content)
    return True


def set_enabled(name: str, enabled: bool) -> bool:
    """Flip one app's value in the [apps] table, preserving comments.

    Edits the single line rather than round-tripping the TOML, so the inline
    comments in provision.toml survive.
    """
    app = get_app(name)
    if app.enabled == enabled:
        return False

    path = config_path()
    text = path.read_text()
    trailing = text.endswith("\n")
    lines = text.splitlines()

    in_apps = False
    pattern = re.compile(
        rf"^(\s*{re.escape(name)}\s*=\s*)(true|false)([ \t]*(?:#.*)?)$"
    )

    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("["):
            in_apps = stripped == "[apps]"
            continue
        if not in_apps:
            continue

        match = pattern.match(line)
        if match:
            lines[index] = f"{match.group(1)}{'true' if enabled else 'false'}{match.group(3)}"
            path.write_text("\n".join(lines) + ("\n" if trailing else ""))
            return True

    fail(f"could not find '{name}' in the [apps] table of {path}")
