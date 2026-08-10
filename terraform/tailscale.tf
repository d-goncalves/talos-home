# ── Tailscale auth key ────────────────────────────────────────────────────────
#
# Replaces the manual loop of: generate a key in the admin console, paste it
# into the 1Password item "Tailscale Auth Key - talos-home", let recover.sh sed
# it into a gitignored patch file.
#
# reusable  — the same key configures every node in var.nodes
# ephemeral — false, so the node persists in the tailnet across reboots
# expiry    — keys cap at 90 days. The key only has to survive long enough to
#             authenticate at install time, so a short life is fine and the
#             node stays up afterwards. Re-running apply mints a fresh one.
#
# Requires tag:k8s (or whatever you set in var.tailscale_tags) to be listed
# under tagOwners in the tailnet ACL, alongside the tag:k8s-operator entry the
# Kubernetes operator already needs.

resource "tailscale_tailnet_key" "node" {
  count = var.tailscale_enabled ? 1 : 0

  # Tailscale rejects punctuation here: parentheses return
  # "description had invalid characters (400)". Letters, digits, spaces and
  # hyphens only.
  description   = "${var.cluster_name} nodes - terraform managed"
  reusable      = true
  ephemeral     = false
  preauthorized = true
  expiry        = 7776000 # 90 days, the maximum
  tags          = var.tailscale_tags

  # If the key is revoked or expires out from under us, mint a new one on the
  # next apply rather than failing.
  recreate_if_invalid = "always"
}
