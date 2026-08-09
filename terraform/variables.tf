# ── 1Password ─────────────────────────────────────────────────────────────────
#
# Item titles only. No credential appears in this repo; Terraform reads the
# values at plan time (see secrets.tf).

variable "op_vault" {
  description = "1Password vault holding the infrastructure credentials"
  type        = string
  default     = "Server Infrastructure"
}

variable "op_item_proxmox" {
  description = "1Password item whose password is the Proxmox API token"
  type        = string
  default     = "Proxmox API - talos-home"
}

variable "op_item_tailscale" {
  description = "1Password item with the Tailscale OAuth client (username = client ID, password = tskey-client-… secret)"
  type        = string
  default     = "Tailscale Talos"
}

variable "op_item_unifi" {
  description = "1Password item with a controller-local UniFi account"
  type        = string
  default     = "UniFi Local Admin - talos-home"
}

# ── Proxmox ───────────────────────────────────────────────────────────────────

variable "proxmox_endpoint" {
  description = "Proxmox VE API endpoint, e.g. https://192.168.1.10:8006/"
  type        = string
}

variable "proxmox_node_name" {
  description = "Name of the Proxmox node that hosts the VMs"
  type        = string
  default     = "pve"
}

variable "proxmox_ssh_username" {
  description = "SSH user on the Proxmox host, used for operations the REST API cannot do"
  type        = string
  default     = "root"
}

variable "proxmox_insecure" {
  description = "Skip TLS verification against the Proxmox API (self-signed cert)"
  type        = bool
  default     = true
}

variable "proxmox_iso_datastore_id" {
  description = "Datastore that holds the downloaded Talos ISO"
  type        = string
  default     = "local"
}

# ── Cluster ───────────────────────────────────────────────────────────────────

variable "cluster_name" {
  description = "Talos cluster name"
  type        = string
  default     = "talos-home"
}

variable "talos_version" {
  description = "Talos version, e.g. v1.13.2. Drives both the Image Factory schematic and the config version contract."
  type        = string
  default     = "v1.13.2"
}

variable "kubernetes_version" {
  description = "Kubernetes version to bootstrap, e.g. v1.36.0"
  type        = string
  default     = "v1.36.0"
}

variable "cluster_endpoint" {
  description = <<-EOT
    Kubernetes API endpoint. Leave null for a single-node cluster and it is
    derived from the first controlplane node's IP. Set explicitly to a VIP
    once there is more than one controlplane.
  EOT
  type        = string
  default     = null
}

variable "nodes" {
  description = <<-EOT
    Cluster nodes, keyed by hostname. Written for N nodes but expected to be
    applied with one: the Proxmox host has 15 GiB and 4 cores in total, which
    is not enough for a three-node controlplane.
  EOT

  type = map(object({
    vm_id        = number
    ip           = string
    mac_address  = string
    machine_type = optional(string, "controlplane")
    cpu_cores    = optional(number, 2)
    cpu_type     = optional(string, "host")

    # Dedicated memory in MiB. floating > 0 turns on ballooning, which is how
    # the guest ended up seeing only ~8 GiB of a 10 GiB VM and thrashing.
    # Leave floating at 0 unless you specifically want ballooning back.
    memory_dedicated = optional(number, 10240)
    memory_floating  = optional(number, 0)

    disk_size     = optional(number, 64)
    datastore_id  = optional(string, "local-lvm")
    install_disk  = optional(string, "/dev/sda")
    interface     = optional(string, "ens18")
    bridge        = optional(string, "vmbr0")
    start_on_boot = optional(bool, true)
  }))
}

# ── Network ───────────────────────────────────────────────────────────────────

variable "gateway" {
  description = "Default gateway for the node network"
  type        = string
}

variable "subnet_prefix" {
  description = "CIDR prefix length for the node network"
  type        = number
  default     = 24
}

variable "nameservers" {
  description = <<-EOT
    DNS servers for the nodes. Do not point these at the Tailscale MagicDNS
    address (100.100.100.100): anything not on the tailnet gets hanging
    lookups while the network itself looks fine.
  EOT
  type        = list(string)
}

variable "lan_subnet" {
  description = "LAN subnet the node advertises to the tailnet as a subnet route, e.g. 192.168.1.0/24"
  type        = string
}

# ── Tailscale ─────────────────────────────────────────────────────────────────

variable "tailscale_enabled" {
  description = "Bake the Tailscale system extension into the image and generate an auth key"
  type        = bool
  default     = true
}

variable "tailscale_tailnet" {
  description = "Tailnet name for the Tailscale API, or \"-\" for the default tailnet of the credential"
  type        = string
  default     = "-"
}

variable "tailscale_tags" {
  description = "Tags applied to nodes authenticated with the generated key. Must exist in tagOwners in your ACL."
  type        = list(string)
  default     = ["tag:k8s"]
}

variable "tailnet_domain" {
  description = "Tailnet MagicDNS domain, e.g. <tailnet>.ts.net. Used for the CoreDNS gitea-ssh rewrite."
  type        = string
}

# ── UniFi ─────────────────────────────────────────────────────────────────────

variable "unifi_enabled" {
  description = <<-EOT
    Manage the node's static DHCP reservation on the UniFi controller.

    This is the network controller only. UNAS Pro NFS shares are not exposed by
    any UniFi API, so exports stay a one-time manual step in the UI; the
    cluster consumes them through the nfs-csi StorageClasses.
  EOT
  type        = bool
  default     = false
}

variable "unifi_api_url" {
  description = "UniFi controller URL, without the /api path"
  type        = string
  default     = ""
}

variable "unifi_site" {
  description = "UniFi site name"
  type        = string
  default     = "default"
}

variable "unifi_insecure" {
  description = "Skip TLS verification against the UniFi controller"
  type        = bool
  default     = true
}

variable "unifi_network_id" {
  description = "UniFi network ID to attach the reservation to. Leave empty to let the controller decide."
  type        = string
  default     = ""
}
