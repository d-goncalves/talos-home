# ── Talos Image Factory ───────────────────────────────────────────────────────
#
# System extensions are baked into the image; they cannot be installed onto a
# running node. That is why Tailscale-on-the-node has to be decided here rather
# than anywhere in the Kubernetes layer.
#
# qemu-guest-agent gives Proxmox a clean shutdown path and IP reporting, which
# matters because a Talos node that cannot be shut down gracefully has to be
# hard-reset from the host.

data "talos_image_factory_extensions_versions" "this" {
  talos_version = var.talos_version

  filters = {
    names = compact([
      var.tailscale_enabled ? "tailscale" : "",
      "qemu-guest-agent",
    ])
  }
}

# One schematic per node, because each carries that node's static address.
#
# A node booted from the ISO is in maintenance mode and has not read its machine
# config yet, so it falls back to DHCP — the static address in the config only
# takes effect after the config is applied. Without a DHCP reservation the node
# comes up on an unpredictable address and `talos_machine_configuration_apply`
# cannot find it: "dial tcp <ip>:50000: connect: host is down".
#
# The kernel `ip=` argument fixes the address from the first boot, so
# provisioning needs no DHCP server and no reservation.
resource "talos_image_factory_schematic" "this" {
  for_each = var.nodes

  schematic = yamlencode({
    customization = {
      systemExtensions = {
        officialExtensions = data.talos_image_factory_extensions_versions.this.extensions_info[*].name
      }
      extraKernelArgs = [
        # ip=<client>::<gateway>:<netmask>:<hostname>:<iface>:<autoconf>
        #
        # The hostname lives here and NOT in machine.network.hostname: Talos
        # rejects a config that sets it in both places.
        join(":", [
          "ip=${each.value.ip}",
          "",
          var.gateway,
          cidrnetmask("0.0.0.0/${var.subnet_prefix}"),
          each.key,
          each.value.interface,
          "off",
        ]),
      ]
    }
  })
}

data "talos_image_factory_urls" "this" {
  for_each = var.nodes

  talos_version = var.talos_version
  schematic_id  = talos_image_factory_schematic.this[each.key].id
  platform      = "metal"
  architecture  = "amd64"
}

# ── ISO on the Proxmox host ───────────────────────────────────────────────────
#
# The VM boots this ISO into maintenance mode; the config apply below then
# installs to disk using the factory *installer* image (see node.yaml.tftpl).
# After the first install the VM boots from disk and the ISO is inert.
resource "proxmox_download_file" "talos_iso" {
  for_each = var.nodes

  content_type = "iso"
  datastore_id = var.proxmox_iso_datastore_id
  node_name    = var.proxmox_node_name
  url          = data.talos_image_factory_urls.this[each.key].urls.iso
  file_name    = "talos-${var.talos_version}-${each.key}-${substr(talos_image_factory_schematic.this[each.key].id, 0, 8)}.iso"

  # The factory rebuilds images; do not fight it over an existing file.
  overwrite = false
}
