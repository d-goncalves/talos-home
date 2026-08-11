# Credentials come from 1Password at plan time, the same way homelab/ does it,
# so nothing secret is in any committed file.
provider "onepassword" {}

provider "tailscale" {
  tailnet             = var.tailscale_tailnet
  oauth_client_id     = local.tailscale_oauth_client_id
  oauth_client_secret = local.tailscale_oauth_client_secret
}
