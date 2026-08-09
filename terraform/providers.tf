# Authenticates from the environment: OP_SERVICE_ACCOUNT_TOKEN, or OP_ACCOUNT
# for desktop-app (biometric) auth. Nothing is configured here, so nothing
# secret is committed.
provider "onepassword" {}

# NOTE: the provider blocks below read credentials from onepassword_item data
# sources (via locals). Terraform allows this, but provider configuration that
# depends on data sources is a known sharp edge: the data source must be
# readable during plan, and `destroy` can fail if 1Password is unreachable
# because the provider cannot be configured to tear anything down.
#
# If that ever bites, export the values as TF_VAR_* for the duration of the
# run instead — the variables still exist for exactly that escape hatch.

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
#
# Terraform configures every declared provider, whether or not any resource
# uses it, and this one hard-fails on empty credentials. So when unifi_enabled
# is false there is nothing in 1Password to read and placeholders are supplied
# instead: no unifi resources exist in that case, so the provider is never
# actually contacted.
provider "unifi" {
  api_url        = var.unifi_api_url != "" ? var.unifi_api_url : "https://unifi.invalid"
  site           = var.unifi_site
  allow_insecure = var.unifi_insecure

  # With username/password the provider performs a login POST while it is being
  # configured, and Terraform configures every provider that any resource block
  # references — even one whose for_each is empty. So with unifi_enabled = false
  # an unreachable controller failed the whole plan.
  #
  # api_key auth skips that login, so a dummy key keeps the provider
  # configurable while disabled. It is never used: no unifi resources exist.
  api_key  = var.unifi_enabled ? null : "disabled-not-in-use"
  username = var.unifi_enabled ? local.unifi_username : null
  password = var.unifi_enabled ? local.unifi_password : null
}
