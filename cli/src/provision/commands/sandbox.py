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

import os
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

# Cloud-init user-data, uploaded as a Proxmox snippet.
#
# The sandbox authenticates over SSH with a key and has no password, which is
# what you want over the network — but it left the serial console asking for a
# password nobody has, and the console is the *only* way in once the NIC is
# disconnected. So the console logs straight in.
#
# That is not the security hole it looks like: reaching this console at all
# requires root on the Proxmox host, and anyone with that can already do
# anything to the VM. A password here would be a credential to manage, leak and
# rotate, in a repo that is public, in exchange for no real boundary.
# Note this must also create the user and install the keys: `cicustom user=`
# replaces the user-data Proxmox would otherwise generate, so ciuser and
# sshkeys stop having any effect once it is set. Getting that wrong produces a
# VM you can reach by neither SSH nor console.
SNIPPET_NAME = "sandbox-user-data.yaml"
USER_DATA = """\
#cloud-config
# Managed by ./provision sandbox — overwritten on the next `new`.
# The hostname is set here because Proxmox normally supplies it in the
# user-data this file replaces.
hostname: sandbox
users:
  - name: {username}
    groups: [adm, sudo]
    shell: /bin/bash
    sudo: 'ALL=(ALL) NOPASSWD:ALL'
    # No password anywhere: SSH uses the keys below, and the console logs in
    # on its own.
    lock_passwd: true
    ssh_authorized_keys:
{keys}
write_files:
  - path: /etc/systemd/system/serial-getty@ttyS0.service.d/autologin.conf
    permissions: '0644'
    content: |
      [Service]
      ExecStart=
      ExecStart=-/sbin/agetty --autologin {username} --noclear --keep-baud 115200,38400,9600 %I $TERM
  # Own the network outright rather than patching what cloud-init generates.
  #
  # Proxmox writes its network-config against the interface name `eth0`, but
  # this image names the NIC `ens18`. The rename that would reconcile them
  # lives in a .link file, and .link files only apply when udev first sees the
  # device -- by the time cloud-init writes one, that moment has passed. So
  # netplan's [Match] carries Name=eth0, matches nothing, and the interface
  # ends up *unmanaged*: fine at first boot because cloud-init configures it
  # directly, but nothing reconfigures it after a carrier event.
  #
  # Matching en* sidesteps the naming entirely, and 05- sorts ahead of
  # netplan's 10- so this wins if both ever match. IgnoreCarrierLoss keeps the
  # address across `sandbox offline`, so `online` needs no reboot.
  - path: /etc/systemd/network/05-sandbox.network
    permissions: '0644'
    content: |
      [Match]
      Name=en*
      [Network]
      Address={ip}/{prefix}
      Gateway={gateway}
      DNS={dns}
      IgnoreCarrierLoss=yes
runcmd:
  - [systemctl, daemon-reload]
  - [systemctl, restart, 'serial-getty@ttyS0.service']
  - [systemctl, enable, --now, systemd-networkd]
  - [systemctl, restart, systemd-networkd]
"""


def _cfg() -> tuple[Proxmox, settings.Sandbox]:
    return connect(), settings.sandbox()


def _require_vm(api: Proxmox, box: settings.Sandbox) -> None:
    if not api.exists(box.vm_id):
        fail(f"no sandbox VM {box.vm_id}. Run `provision sandbox new` first.")


def _pve_ssh_target() -> tuple[str, str]:
    pve = settings.load().get("proxmox") or {}
    host = (pve.get("endpoint") or "").split("//")[-1].split(":")[0]
    return pve.get("ssh_username", "root"), host


def _upload_snippet(box: settings.Sandbox) -> str | None:
    """Put the cloud-init user-data on the host, returning its volume id.

    Snippets are the one thing the Proxmox API will not upload — its upload
    endpoint only accepts iso, vztmpl and import — so this goes over SSH. If
    that is not available the sandbox still works; it just gets no console
    autologin, so say so rather than failing the whole command.
    """
    user, host = _pve_ssh_target()
    if not host:
        return None

    keys = _ssh_key_list(box)
    if not keys:
        # Without keys the snippet would lock the account out of SSH entirely,
        # and autologin alone is worse than what Proxmox generates by default.
        return None

    body = USER_DATA.format(
        username=box.username,
        keys="\n".join(f"      - {key}" for key in keys),
        ip=box.ip,
        prefix=box.subnet_prefix,
        gateway=box.gateway,
        dns=" ".join(box.nameservers) if box.nameservers else box.gateway,
    )
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", f"{user}@{host}",
         f"mkdir -p /var/lib/vz/snippets && cat > /var/lib/vz/snippets/{SNIPPET_NAME}"],
        input=body, text=True, capture_output=True,
    )
    if result.returncode != 0:
        warn(f"could not upload cloud-init snippet over SSH to {host}")
        warn("the console will ask for a password nobody has — use SSH, or fix host access")
        return None
    return f"local:snippets/{SNIPPET_NAME}"


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


def _ssh_key_list(box: settings.Sandbox) -> list[str]:
    """Public keys to authorise for the sandbox user.

    Without one the account has no password and no key, and the only way in is
    the console. These are public keys, so taking them from the agent leaks
    nothing.
    """
    value = (box.ssh_keys or "").strip()
    if value and value != "auto":
        candidate = Path(value).expanduser()
        text = candidate.read_text() if candidate.is_file() else value
        return [line for line in text.splitlines() if line.strip()]

    result = subprocess.run(["ssh-add", "-L"], capture_output=True, text=True)
    keys = [
        line for line in result.stdout.splitlines()
        if line.startswith(("ssh-", "ecdsa-", "sk-"))
    ]
    if not keys:
        keys = [
            p.read_text().strip()
            for p in sorted(Path.home().joinpath(".ssh").glob("*.pub"))
            if p.read_text().strip()
        ]

    if not keys:
        warn("no SSH public key found — the sandbox will only be reachable by console")
    return keys


def _ssh_keys(box: settings.Sandbox) -> str | None:
    """The same keys, encoded for the API's `sshkeys` parameter.

    Only used when there is no cloud-init snippet: setting `cicustom user=`
    makes Proxmox ignore this entirely.
    """
    keys = _ssh_key_list(box)
    if not keys:
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
    snippet = _upload_snippet(box)
    api.put(
        f"/nodes/{api.node}/qemu/{box.vm_id}/config",
        memory=box.memory,
        cores=box.cpu_cores,
        ciuser=box.username,
        sshkeys=_ssh_keys(box),
        cicustom=f"user={snippet}" if snippet else None,
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
@click.option("--reboot", is_flag=True, help="Reboot as well, if the guest does not recover.")
def sandbox_online(reboot: bool) -> None:
    """Reconnect the sandbox to the network.

    The guest keeps its address across the carrier drop (IgnoreCarrierLoss in
    the cloud-init snippet), so it answers again a few seconds after the link
    returns. --reboot is there for a guest that has been reconfigured by hand
    into some state this cannot recover from.
    """
    api, box = _cfg()
    _require_vm(api, box)
    api.put(f"/nodes/{api.node}/qemu/{box.vm_id}/config", net0=_net_string(box, False))
    success("link reconnected")

    if api.status(box.vm_id) != "running":
        info(f"start it to come back at {box.ip}")
        return

    if reboot:
        info("rebooting")
        api.wait(api.post(f"/nodes/{api.node}/qemu/{box.vm_id}/status/reboot"), what="reboot")

    info(f"{box.ip} should answer within a few seconds")


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
@click.option("--serial", is_flag=True, help="Attach to the serial console now.")
def sandbox_console(serial: bool) -> None:
    """Show how to reach the console, or attach to it with --serial.

    The serial console is the one way in once the NIC is disconnected, so it is
    what you want after `sandbox offline`. It needs a shell on the Proxmox host
    — everything else in this group goes over the API — which is why attaching
    is opt-in rather than the default.
    """
    api, box = _cfg()
    _require_vm(api, box)
    pve = settings.load().get("proxmox") or {}
    host = (pve.get("endpoint") or "").split("//")[-1].split(":")[0]
    user = pve.get("ssh_username", "root")

    if serial:
        if api.status(box.vm_id) != "running":
            fail(f"sandbox {box.vm_id} is not running — start it first")
        if not host:
            fail("provision.toml: [proxmox] endpoint is required to reach the console")

        info(f"attaching to the serial console of VM {box.vm_id} — exit with Ctrl-O")
        argv = ["ssh", "-t", f"{user}@{host}", f"qm terminal {box.vm_id}"]
        # execvp rather than run(): a console needs the real terminal, and
        # handing the process over avoids a wrapper sitting between the user
        # and the tty swallowing control characters.
        try:
            os.execvp("ssh", argv)
        except OSError as exc:
            fail(f"could not start ssh: {exc}")

    rule("Console")
    click.echo("  Serial console (works with the network disconnected):")
    click.echo("    ./provision sandbox console --serial")
    click.echo(f"    ssh {user}@{host} -t 'qm terminal {box.vm_id}'")
    click.echo("    (exit with Ctrl-O)")
    click.echo()
    click.echo("  Or the web console:")
    click.echo(f"    {pve.get('endpoint', '').rstrip('/')}/?console=kvm&novnc=1&vmid={box.vm_id}&node={api.node}")
