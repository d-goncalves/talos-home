locals {
  controlplane_nodes = {
    for name, node in var.nodes : name => node
    if node.machine_type == "controlplane"
  }

  # Single node: derive the endpoint from the one controlplane. Multi node:
  # set var.cluster_endpoint to a VIP explicitly.
  cluster_endpoint = coalesce(
    var.cluster_endpoint,
    "https://${values(local.controlplane_nodes)[0].ip}:6443",
  )

  # talosctl expects endpoints/nodes as plain IPs.
  node_ips         = [for _, node in var.nodes : node.ip]
  controlplane_ips = [for _, node in local.controlplane_nodes : node.ip]

  # Rendered once, shared by every node.
  controlplane_patch = templatefile("${path.module}/templates/controlplane.yaml.tftpl", {
    tailnet_domain = var.tailnet_domain
  })

  tailscale_patch = var.tailscale_enabled ? templatefile("${path.module}/templates/tailscale-ext.yaml.tftpl", {
    authkey    = tailscale_tailnet_key.node[0].key
    lan_subnet = var.lan_subnet
  }) : null

  # Per-node patch list, ordered: node-specific first, then role, then extensions.
  node_patches = {
    for name, node in var.nodes : name => compact([
      templatefile("${path.module}/templates/node.yaml.tftpl", {
        hostname        = name
        interface       = node.interface
        ip              = node.ip
        subnet_prefix   = var.subnet_prefix
        gateway         = var.gateway
        nameservers     = var.nameservers
        install_disk    = node.install_disk
        installer_image = data.talos_image_factory_urls.this.urls.installer
      }),
      node.machine_type == "controlplane" ? local.controlplane_patch : "",
      local.tailscale_patch == null ? "" : local.tailscale_patch,
    ])
  }
}
