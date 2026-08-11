# ── Secrets, straight from 1Password ──────────────────────────────────────────
#
# Terraform fetches these itself, so no credential is written to any file in
# this repo. That is what makes the repo publishable: provision.toml and every
# .tf file here are safe to commit.
#
# Authentication — pick either, both are environment-only:
#   OP_SERVICE_ACCOUNT_TOKEN   the same kind of token ESO already uses
#   OP_ACCOUNT                 desktop app auth, unlocks with biometrics
#
# The secrets DO land in terraform.tfstate. That is a deliberate, accepted
# trade: state is gitignored and backed up to 1Password. If you later want that
# closed off too, OpenTofu supports native state encryption.

data "onepassword_vault" "infra" {
  name = var.op_vault
}

# Store the API token as the item's password:
#   USER@REALM!TOKENID=UUID
data "onepassword_item" "proxmox" {
  vault = data.onepassword_vault.infra.uuid
  title = var.op_item_proxmox
}

# username → OAuth client ID, password → OAuth client secret.
data "onepassword_item" "tailscale" {
  count = var.tailscale_enabled ? 1 : 0

  vault = data.onepassword_vault.infra.uuid
  title = var.op_item_tailscale
}

# username / password of a controller-local account.
data "onepassword_item" "unifi" {
  count = var.unifi_enabled ? 1 : 0

  vault = data.onepassword_vault.infra.uuid
  title = var.op_item_unifi
}

locals {
  # `credential` is populated for API-credential items, `password` for login and
  # password items. coalesce skips nulls and empty strings, so either category
  # works without the item having to be a specific type.
  proxmox_api_token = coalesce(
    data.onepassword_item.proxmox.credential,
    data.onepassword_item.proxmox.password,
  )

  tailscale_oauth_client_id     = var.tailscale_enabled ? data.onepassword_item.tailscale[0].username : ""
  tailscale_oauth_client_secret = var.tailscale_enabled ? data.onepassword_item.tailscale[0].password : ""

  unifi_username = var.unifi_enabled ? data.onepassword_item.unifi[0].username : ""
  unifi_password = var.unifi_enabled ? data.onepassword_item.unifi[0].password : ""

  healthchecksio_api_key = coalesce(
    data.onepassword_item.healthchecksio.credential,
    data.onepassword_item.healthchecksio.password,
  )
}

# Read-write API key, stored as the item's credential field. Used only to
# manage the dead-man's-switch check; it has no access to anything else.
data "onepassword_item" "healthchecksio" {
  vault = data.onepassword_vault.infra.uuid
  title = var.op_item_healthchecksio
}
