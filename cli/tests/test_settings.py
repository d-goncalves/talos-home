"""Tests for provision.toml parsing and in-place editing."""

from __future__ import annotations

import textwrap

import pytest

from provision import settings

SAMPLE = textwrap.dedent(
    """\
    # a leading comment that must survive
    [cluster]
    name = "talos-home"
    talos_version = "v1.13.2"
    tailnet_domain = "example.ts.net"

    [proxmox]
    endpoint = "https://10.0.0.10:8006/"
    node_name = "pve"

    [network]
    gateway = "10.0.0.1"
    subnet_prefix = 24
    nameservers = ["10.0.0.1"]
    lan_subnet = "10.0.0.0/24"

    [tailscale]
    enabled = true
    tags = ["tag:k8s"]

    [unifi]
    enabled = false

    [nodes.cp-01]
    vm_id = 110
    ip = "10.0.0.21"
    mac_address = "AA:BB:CC:DD:EE:FF"
    memory_dedicated = 8192
    memory_floating = 0

    # ── Apps ──
    [apps]
    gitea        = true   # trailing comment must survive
    immich       = true
    adventurelog = false
    """
)


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    """A minimal repo whose app dirs match the [apps] table."""
    (tmp_path / "provision.toml").write_text(SAMPLE)
    apps = tmp_path / "homelab" / "kubernetes" / "apps"
    for name in ("gitea", "immich", "adventurelog"):
        (apps / name).mkdir(parents=True)
    (tmp_path / "homelab" / "terraform").mkdir(parents=True)
    monkeypatch.setattr(settings, "repo_root", lambda: tmp_path)
    return tmp_path


# ── Parsing ──────────────────────────────────────────────────────────────────

def test_apps_preserve_file_order_and_flags(repo):
    apps = settings.apps()
    assert [a.name for a in apps] == ["gitea", "immich", "adventurelog"]
    assert [a.enabled for a in apps] == [True, True, False]


def test_unknown_app_is_rejected(repo):
    with pytest.raises(SystemExit):
        settings.get_app("nope")


def test_on_disk_reads_app_directories(repo):
    assert settings.on_disk() == {"gitea", "immich", "adventurelog"}


# ── Editing ──────────────────────────────────────────────────────────────────

def test_set_enabled_round_trip_is_byte_identical(repo):
    original = (repo / "provision.toml").read_text()

    assert settings.set_enabled("gitea", False) is True
    assert settings.get_app("gitea").enabled is False

    assert settings.set_enabled("gitea", True) is True
    assert (repo / "provision.toml").read_text() == original


def test_set_enabled_preserves_comments(repo):
    settings.set_enabled("gitea", False)
    text = (repo / "provision.toml").read_text()
    assert "# a leading comment that must survive" in text
    assert "# trailing comment must survive" in text
    assert "# ── Apps ──" in text


def test_set_enabled_does_not_insert_blank_lines(repo):
    """Regression: a greedy trailing group once swallowed the newline, so the
    rewrite added a blank line after every edited key."""
    before = len((repo / "provision.toml").read_text().splitlines())
    settings.set_enabled("immich", False)
    after = len((repo / "provision.toml").read_text().splitlines())
    assert before == after


def test_set_enabled_is_a_noop_when_already_correct(repo):
    assert settings.set_enabled("adventurelog", False) is False


def test_set_enabled_only_touches_the_apps_table(repo):
    """`enabled` also appears under [tailscale]; it must not be hit."""
    settings.set_enabled("adventurelog", True)
    data = settings.load()
    assert data["tailscale"]["enabled"] is True
    assert data["unifi"]["enabled"] is False


# ── Projection onto terraform ────────────────────────────────────────────────

def test_tfvars_maps_scalars_and_nodes(repo):
    tf = settings.tfvars()
    assert tf["cluster_name"] == "talos-home"
    assert tf["proxmox_endpoint"] == "https://10.0.0.10:8006/"
    assert tf["gateway"] == "10.0.0.1"
    assert tf["nameservers"] == ["10.0.0.1"]
    assert tf["tailscale_enabled"] is True
    assert tf["unifi_enabled"] is False
    assert tf["nodes"]["cp-01"]["vm_id"] == 110
    assert tf["nodes"]["cp-01"]["memory_floating"] == 0


def test_tfvars_excludes_the_apps_table(repo):
    assert "apps" not in settings.tfvars()


def test_unknown_node_key_is_rejected(repo):
    """A typo in a node key must fail loudly, not be silently dropped."""
    path = repo / "provision.toml"
    path.write_text(
        path.read_text().replace(
            'mac_address = "AA:BB:CC:DD:EE:FF"',
            'mac_address = "AA:BB:CC:DD:EE:FF"\nmemroy_dedicated = 4096',
        )
    )
    with pytest.raises(SystemExit):
        settings.tfvars()


def test_non_boolean_app_value_is_rejected(repo):
    path = repo / "provision.toml"
    path.write_text(path.read_text().replace("immich       = true", 'immich = "yes"'))
    with pytest.raises(SystemExit):
        settings.apps()


def test_write_tfvars_is_idempotent(repo):
    assert settings.write_tfvars() is True
    assert settings.write_tfvars() is False


# ── Placeholders ─────────────────────────────────────────────────────────────

def test_unfilled_detects_placeholders(repo):
    path = repo / "provision.toml"
    path.write_text(path.read_text().replace("10.0.0.21", "<node-ip>"))
    found = settings.unfilled()
    assert any("node-ip" in f for f in found)


def test_unfilled_is_empty_for_a_complete_config(repo):
    assert settings.unfilled() == []
