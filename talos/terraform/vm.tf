resource "proxmox_virtual_environment_vm" "node" {
  for_each = var.nodes

  name        = each.key
  node_name   = var.proxmox_node_name
  vm_id       = each.value.vm_id
  description = "Talos ${each.value.machine_type} — managed by Terraform"
  tags        = ["terraform", "talos", each.value.machine_type]

  on_boot         = each.value.start_on_boot
  stop_on_destroy = true

  # Provided by the qemu-guest-agent system extension baked into the image.
  agent {
    enabled = true
  }

  # i440fx, not q35: q35 has a history of silently failing to boot on this
  # host (blank serial console, 99% CPU spin, no ARP).
  machine = "pc"
  bios    = "seabios"

  cpu {
    cores = each.value.cpu_cores
    type  = each.value.cpu_type
  }

  memory {
    dedicated = each.value.memory_dedicated

    # floating == dedicated enables ballooning. Left at 0 by default: a
    # ballooned Talos node reports more memory than it can actually use, and
    # the guest thrashes under memory pressure instead of reclaiming.
    floating = each.value.memory_floating
  }

  disk {
    datastore_id = each.value.datastore_id
    interface    = "scsi0"
    size         = each.value.disk_size
    file_format  = "raw"
    ssd          = true
    discard      = "on"
  }

  cdrom {
    file_id = proxmox_download_file.talos_iso[each.key].id
  }

  network_device {
    bridge = each.value.bridge

    # Pinned rather than generated, so the UniFi DHCP reservation and the
    # Talos static address describe the same machine across rebuilds.
    mac_address = each.value.mac_address
  }

  operating_system {
    type = "l26"
  }

  # Talos writes install and diagnostic output here; without it a failed
  # install is invisible from the host.
  serial_device {}

  boot_order = ["scsi0", "ide3"]

  # Deliberately NOT ignoring changes to cdrom. That was here to stop a factory
  # rebuild churning the VM, but Terraform deletes the superseded ISO while the
  # VM still references it — leaving a machine that boots from a file which no
  # longer exists, and reports only "host is down".
}
