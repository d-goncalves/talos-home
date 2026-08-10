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
  node                 = local.first_controlplane.ip
  endpoint             = local.first_controlplane.ip

  depends_on = [talos_machine_configuration_apply.this]

  lifecycle {
    # With several controlplanes, deriving the endpoint from one of them points
    # every node at a single member: lose it and the cluster is unreachable
    # even though the others are healthy. Fail loudly instead of quietly
    # building something that only looks highly available.
    precondition {
      condition     = length(local.controlplane_nodes) == 1 || var.cluster_endpoint != null
      error_message = "More than one controlplane node requires cluster.endpoint to be set to a VIP in provision.toml."
    }
  }

  timeouts = {
    create = "10m"
  }
}

resource "talos_cluster_kubeconfig" "this" {
  client_configuration = talos_machine_secrets.this.client_configuration
  node                 = local.first_controlplane.ip
  endpoint             = local.first_controlplane.ip

  depends_on = [talos_machine_bootstrap.this]

  timeouts = {
    create = "10m"
  }
}

# NOTE: there is deliberately no talos_cluster_health data source here.
# It gated the outputs on a healthy cluster, which reads well until you try to
# tear a broken one down: the data source is evaluated during destroy planning
# too, so `terraform destroy` failed with "cluster health check failed" on
# exactly the cluster you most want to remove. Health is checked by
# `./provision health` and `./provision status` instead, where failing is
# informative rather than blocking.
