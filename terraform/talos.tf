# ── Cluster PKI ───────────────────────────────────────────────────────────────
#
# This resource generates the cluster CA, etcd CA, bootstrap tokens and the
# secretbox encryption key. All of it lands in terraform.tfstate in plaintext.
# It is the same material that currently sits in the gitignored
# talos/machine-config/controlplane.yaml.
resource "talos_machine_secrets" "this" {
  talos_version = var.talos_version
}

data "talos_machine_configuration" "this" {
  for_each = var.nodes

  cluster_name       = var.cluster_name
  cluster_endpoint   = local.cluster_endpoint
  machine_type       = each.value.machine_type
  machine_secrets    = talos_machine_secrets.this.machine_secrets
  talos_version      = var.talos_version
  kubernetes_version = var.kubernetes_version
}

data "talos_client_configuration" "this" {
  cluster_name         = var.cluster_name
  client_configuration = talos_machine_secrets.this.client_configuration
  nodes                = local.node_ips
  endpoints            = local.controlplane_ips
}

# ── Apply ─────────────────────────────────────────────────────────────────────
#
# The node has to be reachable in maintenance mode (booted from the ISO) for
# this to succeed. On a cold provision that means waiting for the VM to come up
# after Terraform creates it; `provision apply` handles the wait.
resource "talos_machine_configuration_apply" "this" {
  for_each = var.nodes

  client_configuration        = talos_machine_secrets.this.client_configuration
  machine_configuration_input = data.talos_machine_configuration.this[each.key].machine_configuration
  node                        = each.value.ip
  endpoint                    = each.value.ip
  config_patches              = local.node_patches[each.key]

  depends_on = [proxmox_virtual_environment_vm.node]

  timeouts = {
    create = "10m"
  }
}

resource "talos_machine_bootstrap" "this" {
  client_configuration = talos_machine_secrets.this.client_configuration
  node                 = values(local.controlplane_nodes)[0].ip
  endpoint             = values(local.controlplane_nodes)[0].ip

  depends_on = [talos_machine_configuration_apply.this]

  timeouts = {
    create = "10m"
  }
}

resource "talos_cluster_kubeconfig" "this" {
  client_configuration = talos_machine_secrets.this.client_configuration
  node                 = values(local.controlplane_nodes)[0].ip
  endpoint             = values(local.controlplane_nodes)[0].ip

  depends_on = [talos_machine_bootstrap.this]

  timeouts = {
    create = "10m"
  }
}

# Gate the outputs on the cluster actually being healthy, so a successful apply
# means something. Kubernetes checks are included: a node that boots but never
# gets a working kubelet is not a provisioned cluster.
data "talos_cluster_health" "this" {
  client_configuration = talos_machine_secrets.this.client_configuration
  control_plane_nodes  = local.controlplane_ips
  worker_nodes         = [for _, n in var.nodes : n.ip if n.machine_type == "worker"]
  endpoints            = local.controlplane_ips

  depends_on = [talos_cluster_kubeconfig.this]

  timeouts = {
    read = "15m"
  }
}
