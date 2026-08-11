data "onepassword_vault" "infra" {
  name = var.op_vault
}

# username → OAuth client ID, password → OAuth client secret.
#
# The same item homelab/ reads for the node auth key. Rotating it means
# re-running apply in both roots — noted in docs/rotate.md.
data "onepassword_item" "tailscale" {
  vault = data.onepassword_vault.infra.uuid
  title = var.op_item_tailscale
}

locals {
  tailscale_oauth_client_id     = data.onepassword_item.tailscale.username
  tailscale_oauth_client_secret = data.onepassword_item.tailscale.password
}
