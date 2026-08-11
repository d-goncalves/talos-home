"""Tests for the Pi-hole config projection and the value comparison.

The comparison is the load-bearing part: `pihole-FTL --config` prints values in
one format and accepts another, so a naive compare reports drift forever and
the playbook stops being a drift check.
"""

from __future__ import annotations

import importlib.util
import textwrap
from pathlib import Path

import pytest

from provision import settings

REPO = Path(__file__).resolve().parents[2]


def _filter_module():
    """Load the Ansible filter plugin directly — it is not on sys.path."""
    path = REPO / "ansible" / "filter_plugins" / "pihole.py"
    spec = importlib.util.spec_from_file_location("pihole_filters", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


matches = _filter_module().pihole_matches


@pytest.mark.parametrize(
    "current,desired",
    [
        ("[ 127.0.0.1#5335, 1.0.0.1 ]", ["127.0.0.1#5335", "1.0.0.1"]),
        ("[]", []),
        ("false", False),
        ("true", True),
        ("ALL", "ALL"),
        # FTL pads the brackets; the desired value never does.
        ("[ a ]", ["a"]),
    ],
)
def test_equal_values_report_no_change(current, desired):
    assert matches(current, desired)


@pytest.mark.parametrize(
    "current,desired",
    [
        ("[]", ["192.168.1.10 pve"]),
        ("[ 1.1.1.1 ]", ["1.0.0.1"]),
        # Order matters to FTL, so it has to matter here.
        ("[ a, b ]", ["b", "a"]),
        ("false", True),
        ("ALL", "LOCAL"),
    ],
)
def test_differing_values_report_a_change(current, desired):
    assert not matches(current, desired)


def test_the_bug_this_was_written_for():
    """An empty list must not look equal to a populated one."""
    assert not matches("[]", ["192.168.1.10 pve", "192.168.1.12 nas"])


# ── provision.toml projection ────────────────────────────────────────────────

CONFIG = """
    [pihole]
    enabled = true

    [[pihole.hosts]]
    name = "pihole"
    vmid = 101
    ansible_host = "pihole.example.ts.net"
    tailscale_ip = "100.64.0.1"
    ip = "192.168.1.23"
    memory = 512
    swap = 512
    cores = 1
"""


def _config(tmp_path, body, monkeypatch):
    (tmp_path / "provision.toml").write_text(textwrap.dedent(body).lstrip())
    monkeypatch.setattr(settings, "repo_root", lambda: tmp_path)
    return tmp_path


def test_hosts_are_read(tmp_path, monkeypatch):
    _config(tmp_path, CONFIG, monkeypatch)
    (host,) = settings.pihole_hosts()
    assert host.name == "pihole"
    assert host.tailscale_ip == "100.64.0.1"


def test_disabled_yields_no_hosts(tmp_path, monkeypatch):
    _config(tmp_path, CONFIG.replace("enabled = true", "enabled = false"), monkeypatch)
    assert settings.pihole_hosts() == []


def test_a_missing_key_is_rejected(tmp_path, monkeypatch):
    _config(tmp_path, CONFIG.replace('    tailscale_ip = "100.64.0.1"\n', ""), monkeypatch)
    with pytest.raises(SystemExit):
        settings.pihole_hosts()


def test_an_unknown_key_is_rejected(tmp_path, monkeypatch):
    """A typo should fail loudly rather than be silently ignored."""
    _config(tmp_path, CONFIG + '    colour = "red"\n', monkeypatch)
    with pytest.raises(SystemExit):
        settings.pihole_hosts()


def test_inventory_groups_the_hosts(tmp_path, monkeypatch):
    _config(tmp_path, CONFIG, monkeypatch)
    rendered = settings.render_inventory()
    # The group must not share a name with the host, or Ansible warns and the
    # play targets ambiguously.
    assert "piholes:" in rendered
    assert "    pihole:" in rendered
    assert "pihole.example.ts.net" in rendered
