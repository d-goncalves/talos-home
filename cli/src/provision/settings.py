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


@dataclass(frozen=True)
class Sandbox:
    """The [sandbox] table.

    Deliberately not projected into tfvars: the sandbox is driven straight
    through the Proxmox API, so none of this reaches Terraform.
    """

    vm_id: int
    template_id: int
    ip: str
    memory: int
    cpu_cores: int
    disk_size: int
    datastore_id: str
    bridge: str
    username: str
    image_url: str
    ssh_keys: str
    gateway: str
    subnet_prefix: int
    nameservers: list[str]


_SANDBOX_DEFAULTS = {
    "vm_id": 120,
    "template_id": 9000,
    "memory": 2048,
    "cpu_cores": 2,
    "disk_size": 20,
    "datastore_id": "local-lvm",
    "bridge": "vmbr0",
    "username": "lab",
    "image_url": (
        "https://cloud-images.ubuntu.com/noble/current/"
        "noble-server-cloudimg-amd64.img"
    ),
    # "auto" takes every public key out of the ssh-agent, falling back to
    # ~/.ssh/*.pub. Public keys are not secrets, and without one the sandbox
    # has a user you cannot log in as.
    "ssh_keys": "auto",
}


def config_path() -> Path:
    return repo_root() / CONFIG_NAME


# ── Project layout ───────────────────────────────────────────────────────────
#
# The repo is split by project: homelab/, sandbox/ and pihole/ each own their own
# infrastructure. Everything that needs a path goes through here, so moving a
# project is one edit rather than a grep across the CLI.

def homelab_dir() -> Path:
    return repo_root() / "homelab"


def kubernetes_dir() -> Path:
    return homelab_dir() / "kubernetes"


def terraform_dir() -> Path:
    return homelab_dir() / "terraform"


def scripts_dir() -> Path:
    return homelab_dir() / "scripts"


def pihole_dir() -> Path:
    return repo_root() / "pihole"


def pihole_terraform_dir() -> Path:
    return pihole_dir() / "terraform"


def ansible_dir() -> Path:
    return pihole_dir() / "ansible"


def apps_dir() -> Path:
    return kubernetes_dir() / "apps"


def kustomization_path() -> Path:
    return apps_dir() / "kustomization.yaml"


def tfvars_json_path() -> Path:
    return terraform_dir() / "terraform.tfvars.json"


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


def sandbox() -> Sandbox:
    """Read [sandbox], inheriting the LAN settings from [network].

    Gateway and nameservers are not repeated in [sandbox]: the sandbox sits on
    the same LAN as everything else, and two copies of the gateway is two
    places to fix when it changes.
    """
    data = load()
    table = data.get("sandbox")
    if not isinstance(table, dict):
        fail(
            "provision.toml has no [sandbox] table. "
            "Add one, or run the homelab commands instead."
        )

    unknown = set(table) - set(_SANDBOX_DEFAULTS) - {"ip"}
    if unknown:
        fail(f"provision.toml: [sandbox] has unknown keys: {', '.join(sorted(unknown))}")

    network = data.get("network") or {}
    merged = {**_SANDBOX_DEFAULTS, **table}

    ip = merged.get("ip")
    if not ip:
        fail("provision.toml: [sandbox] ip is required")

    for key in ("vm_id", "template_id", "memory", "cpu_cores", "disk_size"):
        if not isinstance(merged[key], int):
            fail(f"provision.toml: [sandbox] {key} must be an integer")

    if merged["vm_id"] == merged["template_id"]:
        fail("provision.toml: [sandbox] vm_id and template_id must differ")

    # A sandbox that reuses a cluster node's ID or address would have the CLI
    # cheerfully deleting the cluster.
    for name, body in (data.get("nodes") or {}).items():
        if body.get("vm_id") == merged["vm_id"]:
            fail(f"provision.toml: [sandbox] vm_id {merged['vm_id']} collides with node '{name}'")
        if body.get("ip") == ip:
            fail(f"provision.toml: [sandbox] ip {ip} collides with node '{name}'")

    return Sandbox(
        vm_id=merged["vm_id"],
        template_id=merged["template_id"],
        ip=str(ip),
        memory=merged["memory"],
        cpu_cores=merged["cpu_cores"],
        disk_size=merged["disk_size"],
        datastore_id=str(merged["datastore_id"]),
        bridge=str(merged["bridge"]),
        username=str(merged["username"]),
        image_url=str(merged["image_url"]),
        ssh_keys=str(merged["ssh_keys"]),
        gateway=str(network.get("gateway", "")),
        subnet_prefix=int(network.get("subnet_prefix", 24)),
        nameservers=list(network.get("nameservers") or []),
    )


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
    for table in ("cluster", "proxmox", "network", "unifi", "nodes", "sandbox"):
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


# ── Pi-hole ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PiholeHost:
    name: str
    vmid: int
    ansible_host: str
    tailscale_ip: str
    ip: str
    memory: int
    swap: int
    cores: int


def pihole_hosts() -> list[PiholeHost]:
    """Read [[pihole.hosts]]. Empty when Pi-hole is disabled."""
    table = load().get("pihole")
    if not isinstance(table, dict) or not table.get("enabled", False):
        return []

    hosts = table.get("hosts")
    if not isinstance(hosts, list) or not hosts:
        fail("provision.toml: [pihole] is enabled but declares no [[pihole.hosts]]")

    required = (
        "name", "vmid", "ansible_host", "tailscale_ip", "ip",
        "memory", "swap", "cores",
    )
    out = []
    for entry in hosts:
        missing = [k for k in required if k not in entry]
        if missing:
            fail(
                f"provision.toml: [[pihole.hosts]] entry "
                f"{entry.get('name', '?')} is missing: {', '.join(missing)}"
            )
        unknown = set(entry) - set(required)
        if unknown:
            fail(
                f"provision.toml: [[pihole.hosts]] entry {entry['name']} has "
                f"unknown keys: {', '.join(sorted(unknown))}"
            )
        out.append(PiholeHost(**{k: entry[k] for k in required}))

    names = [h.name for h in out]
    if len(set(names)) != len(names):
        fail("provision.toml: [[pihole.hosts]] has duplicate names")
    return out


def pihole_tfvars_path() -> Path:
    return pihole_terraform_dir() / "terraform.tfvars.json"


def pihole_tfvars() -> dict:
    """Terraform variables for the Pi-hole root.

    Its own state, so its own tfvars. Devices resolve against the tailnet
    addresses, so this list is what decides whether DNS works at all; order is
    preference order.
    """
    hosts = pihole_hosts()
    if not hosts:
        return {}
    return {"tailnet_nameservers": [h.tailscale_ip for h in hosts]}


def write_pihole_tfvars() -> bool:
    """Write pihole/terraform/terraform.tfvars.json. True when it changed."""
    values = pihole_tfvars()
    if not values:
        return False

    content = json.dumps(values, indent=2, sort_keys=True) + "\n"
    path = pihole_tfvars_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists() and path.read_text() == content:
        return False

    path.write_text(content)
    return True


def inventory_path() -> Path:
    return ansible_dir() / "inventory.yml"


def render_inventory() -> str:
    """Ansible inventory for the pihole group, projected from provision.toml.

    Generated rather than hand-written so the host list, addresses and
    container sizing have exactly one source. Adding a second Pi-hole is a
    second [[pihole.hosts]] entry and a re-run of `sync`.
    """
    data = load()
    network = data.get("network") or {}
    nas = data.get("nas") or {}
    proxmox = data.get("proxmox") or {}

    lines = [
        "# Generated by `provision homelab sync` from provision.toml.",
        "# Do not edit — your changes will be overwritten.",
        "---",
        "piholes:",
        "  vars:",
        f"    lan_gateway: \"{network.get('gateway', '')}\"",
        f"    lan_subnet: \"{network.get('lan_subnet', '')}\"",
        f"    nas_ip: \"{nas.get('ip', '')}\"",
        f"    proxmox_ip: \"{_host_of(proxmox.get('endpoint', ''))}\"",
        "  hosts:",
    ]
    for host in pihole_hosts():
        lines += [
            f"    {host.name}:",
            f"      ansible_host: \"{host.ansible_host}\"",
            "      ansible_user: root",
            f"      pihole_lan_ip: \"{host.ip}\"",
            f"      pihole_vmid: {host.vmid}",
        ]
    return "\n".join(lines) + "\n"


def _host_of(endpoint: str) -> str:
    """Bare host out of a URL like https://192.168.1.10:8006/."""
    match = re.search(r"//([^:/]+)", endpoint or "")
    return match.group(1) if match else ""


def write_inventory() -> bool:
    """Write ansible/inventory.yml. Returns True when it changed."""
    if not pihole_hosts():
        return False

    content = render_inventory()
    path = inventory_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists() and path.read_text() == content:
        return False

    path.write_text(content)
    return True
