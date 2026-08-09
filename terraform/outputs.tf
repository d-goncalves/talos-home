output "cluster_name" {
  value = var.cluster_name
}

output "cluster_endpoint" {
  value = local.cluster_endpoint
}

output "node_ips" {
  description = "Map of hostname to IP for every node in the cluster"
  value       = { for name, node in var.nodes : name => node.ip }
}

# Consumed by `provision bootstrap secrets` to populate the flux-system
# cluster-vars Secret that Flux postBuild substitution reads.
output "tailnet_domain" {
  value = var.tailnet_domain
}

output "lan_subnet" {
  value = var.lan_subnet
}

output "controlplane_ip" {
  description = "First controlplane IP, used as the talosctl endpoint"
  value       = local.first_controlplane.ip
}

output "schematic_id" {
  description = "Image Factory schematic ID. Pin this when upgrading Talos so the extensions come along."
  value       = talos_image_factory_schematic.this.id
}

output "installer_image" {
  description = "Factory installer image used by machine.install.image"
  value       = data.talos_image_factory_urls.this.urls.installer
}

output "iso_url" {
  description = "Factory ISO URL, for booting a replacement VM by hand"
  value       = data.talos_image_factory_urls.this.urls.iso
}

# Consumed by `provision config write`, which write these to
# ~/.kube/config and ~/.talos/config.
output "kubeconfig" {
  value     = talos_cluster_kubeconfig.this.kubeconfig_raw
  sensitive = true
}

output "talosconfig" {
  value     = data.talos_client_configuration.this.talos_config
  sensitive = true
}
