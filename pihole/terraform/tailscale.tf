# ── Tailnet DNS ───────────────────────────────────────────────────────────────
#
# Which resolvers every device in the tailnet uses. Previously this lived only
# in the admin console, so "what is my DNS" was answerable only by logging in,
# and a rebuild had no record of it.
#
# The Pi-hole containers are reached at their *tailnet* addresses, not their LAN
# ones — which is why changing a container's LAN IP is safe and recreating the
# container is not. A new container means a new Tailscale identity, and this
# list would point at an address that no longer answers.
#
# Deliberately not managing dns_preferences (MagicDNS): it is on tailnet-wide
# and toggling it from here would be an unrelated blast radius.
resource "tailscale_dns_nameservers" "tailnet" {
  count = length(var.tailnet_nameservers) > 0 ? 1 : 0

  nameservers = var.tailnet_nameservers
}
