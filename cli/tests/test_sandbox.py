"""Tests for the [sandbox] table and the Proxmox client's encoding.

The commands themselves talk to a live Proxmox and are not covered here. What
is covered is the part that can silently do the wrong thing: config validation
that stops a sandbox from colliding with a cluster node, and the two request
encodings that Proxmox rejects when they are subtly wrong.
"""

from __future__ import annotations

import textwrap

import pytest

from provision import proxmox, settings

BASE = """\
[proxmox]
endpoint = "https://10.0.0.10:8006/"
node_name = "pve"

[network]
gateway = "10.0.0.1"
subnet_prefix = 24
nameservers = ["10.0.0.1"]

[nodes.cp-01]
vm_id = 110
ip = "10.0.0.21"

[apps]
gitea = true
"""


def write(tmp_path, monkeypatch, sandbox_table: str):
    (tmp_path / "provision.toml").write_text(BASE + textwrap.dedent(sandbox_table))
    (tmp_path / "terraform").mkdir(exist_ok=True)
    monkeypatch.setattr(settings, "repo_root", lambda: tmp_path)
    return tmp_path


# ── config ───────────────────────────────────────────────────────────────────

def test_sandbox_inherits_network_from_the_network_table(tmp_path, monkeypatch):
    write(tmp_path, monkeypatch, """
        [sandbox]
        ip = "10.0.0.22"
    """)
    box = settings.sandbox()
    assert box.gateway == "10.0.0.1"
    assert box.nameservers == ["10.0.0.1"]
    assert box.subnet_prefix == 24
    # Defaults fill in everything not stated.
    assert box.vm_id == 120
    assert box.memory == 2048


def test_sandbox_vm_id_colliding_with_a_node_is_rejected(tmp_path, monkeypatch):
    write(tmp_path, monkeypatch, """
        [sandbox]
        ip = "10.0.0.22"
        vm_id = 110
    """)
    with pytest.raises(SystemExit):
        settings.sandbox()


def test_sandbox_ip_colliding_with_a_node_is_rejected(tmp_path, monkeypatch):
    write(tmp_path, monkeypatch, """
        [sandbox]
        ip = "10.0.0.21"
    """)
    with pytest.raises(SystemExit):
        settings.sandbox()


def test_sandbox_rejects_unknown_keys(tmp_path, monkeypatch):
    write(tmp_path, monkeypatch, """
        [sandbox]
        ip = "10.0.0.22"
        colour = "blue"
    """)
    with pytest.raises(SystemExit):
        settings.sandbox()


def test_missing_sandbox_table_fails_clearly(tmp_path, monkeypatch):
    write(tmp_path, monkeypatch, "")
    with pytest.raises(SystemExit):
        settings.sandbox()


def test_sandbox_never_reaches_terraform(tmp_path, monkeypatch):
    """The sandbox is API-driven; leaking it into tfvars would couple the two."""
    write(tmp_path, monkeypatch, """
        [sandbox]
        ip = "10.0.0.22"
    """)
    assert "sandbox" not in settings.tfvars()


# ── client encoding ──────────────────────────────────────────────────────────

def test_booleans_are_sent_as_ints(monkeypatch):
    """PVE rejects Python's True/False with 'not a number'."""
    seen = {}

    class FakeResponse:
        def read(self):
            return b'{"data":null}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, **kwargs):
        seen["body"] = req.data.decode()
        return FakeResponse()

    monkeypatch.setattr(proxmox.urllib.request, "urlopen", fake_urlopen)
    api = proxmox.Proxmox("https://x:8006", "pve", "t=v")
    api.post("/thing", full=True, purge=False, keep=None)

    assert "full=1" in seen["body"]
    assert "purge=0" in seen["body"]
    # None means "not set" and must not be sent at all.
    assert "keep" not in seen["body"]


def test_import_filename_rewrites_img_to_qcow2():
    """PVE validates the extension and rejects .img for import content."""
    from provision.commands.sandbox import _import_filename

    assert _import_filename(
        "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img"
    ) == "noble-server-cloudimg-amd64.qcow2"
    assert _import_filename("https://x/y/thing.qcow2") == "thing.qcow2"


def test_user_data_carries_the_user_and_keys():
    """`cicustom user=` replaces Proxmox's generated user-data.

    So the snippet has to create the account and install the keys itself. If it
    ever stops doing that, the sandbox becomes unreachable by SSH *and* by
    console, which is a bad way to find out.
    """
    yaml = pytest.importorskip("yaml")
    from provision.commands.sandbox import USER_DATA

    body = USER_DATA.format(
        username="lab",
        keys="\n".join(f"      - {k}" for k in ["ssh-ed25519 AAAAkey1", "ssh-ed25519 AAAAkey2"]),
    )
    parsed = yaml.safe_load(body)

    user = parsed["users"][0]
    assert user["name"] == "lab"
    assert user["ssh_authorized_keys"] == ["ssh-ed25519 AAAAkey1", "ssh-ed25519 AAAAkey2"]
    assert user["lock_passwd"] is True
    assert "NOPASSWD" in user["sudo"]
    # Proxmox supplies the hostname in the user-data this file replaces.
    assert parsed["hostname"] == "sandbox"
    # The console has to log in on its own: there is no password to type.
    autologin = parsed["write_files"][0]
    assert "serial-getty@ttyS0" in autologin["path"]
    assert "--autologin lab" in autologin["content"]


def test_net_string_marks_the_link_down():
    from provision.commands.sandbox import _net_string

    box = settings.Sandbox(
        vm_id=120, template_id=9000, ip="10.0.0.22", memory=2048, cpu_cores=2,
        disk_size=20, datastore_id="local-lvm", bridge="vmbr0", username="lab",
        image_url="", ssh_keys="auto", gateway="10.0.0.1", subnet_prefix=24,
        nameservers=[],
    )
    assert _net_string(box, disconnected=False) == "virtio,bridge=vmbr0"
    assert _net_string(box, disconnected=True) == "virtio,bridge=vmbr0,link_down=1"
