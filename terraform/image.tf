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

resource "talos_image_factory_schematic" "this" {
  schematic = yamlencode({
    customization = {
      systemExtensions = {
        officialExtensions = data.talos_image_factory_extensions_versions.this.extensions_info[*].name
      }
    }
  })
}

data "talos_image_factory_urls" "this" {
  talos_version = var.talos_version
  schematic_id  = talos_image_factory_schematic.this.id
  platform      = "metal"
  architecture  = "amd64"
}

# ── ISO on the Proxmox host ───────────────────────────────────────────────────
#
# The VM boots this ISO into maintenance mode; the config apply below then
# installs to disk using the factory *installer* image (see node.yaml.tftpl).
# After the first install the VM boots from disk and the ISO is inert.
#
# Alternative: skip the ISO and import the factory `disk_image` straight to the
# boot disk. Fewer steps, but it needs the `import` content type enabled on the
# datastore, so the ISO path is the lower-friction default.
resource "proxmox_download_file" "talos_iso" {
  content_type = "iso"
  datastore_id = var.proxmox_iso_datastore_id
  node_name    = var.proxmox_node_name
  url          = data.talos_image_factory_urls.this.urls.iso
  file_name    = "talos-${var.talos_version}-${substr(talos_image_factory_schematic.this.id, 0, 12)}.iso"

  # The factory rebuilds images; do not fight it over an existing file.
  overwrite = false
}
