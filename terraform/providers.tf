# Authenticates from the environment: OP_SERVICE_ACCOUNT_TOKEN, or OP_ACCOUNT
# for desktop-app (biometric) auth. Nothing is configured here, so nothing
# secret is committed.
provider "onepassword" {}

provider "proxmox" {
  endpoint  = var.proxmox_endpoint
  api_token = local.proxmox_api_token
  insecure  = var.proxmox_insecure

  # bpg/proxmox shells out over SSH for a few operations the REST API does not
  # cover (disk import, some file uploads). `ssh root@pve` works; `ssh pve`
  # does not, because Tailscale SSH tries the local username.
  ssh {
    agent    = true
    username = var.proxmox_ssh_username
  }
}

# No configuration required — credentials flow in per-resource from
# talos_machine_secrets.
provider "talos" {}

provider "tailscale" {
  tailnet             = var.tailscale_tailnet
  oauth_client_id     = local.tailscale_oauth_client_id
  oauth_client_secret = local.tailscale_oauth_client_secret
}

# Requires a controller-local account (Limited Admin, Local Access Only).
# Ubiquiti SSO accounts and 2FA are not supported by this provider.
provider "unifi" {
  username       = local.unifi_username
  password       = local.unifi_password
  api_url        = var.unifi_api_url
  site           = var.unifi_site
  allow_insecure = var.unifi_insecure
}
