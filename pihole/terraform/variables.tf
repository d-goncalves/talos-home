variable "op_vault" {
  description = "1Password vault holding the infrastructure credentials"
  type        = string
  default     = "Server Infrastructure"
}

variable "op_item_tailscale" {
  description = "1Password item with the Tailscale OAuth client (username = client ID, password = the secret)"
  type        = string
  default     = "Tailscale Talos"
}

variable "tailscale_tailnet" {
  description = "Tailnet name for the Tailscale API, or \"-\" for the default tailnet of the credential"
  type        = string
  default     = "-"
}

variable "tailnet_nameservers" {
  description = <<-EOT
    Tailnet-wide DNS servers, in preference order — the Pi-hole containers'
    tailnet addresses. Generated from [[pihole.hosts]] in provision.toml.

    This is what devices actually resolve against. An empty list hands DNS back
    to Tailscale's default resolvers rather than disabling DNS, but it does
    switch off Pi-hole's filtering for every device at once.
  EOT
  type        = list(string)
  default     = []
}
