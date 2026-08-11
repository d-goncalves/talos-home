# ── UniFi static DHCP reservation ─────────────────────────────────────────────
#
# Scope note: this is the UniFi *Network* controller, not the UNAS storage app.
# There is no UniFi API for creating NFS shares or exports on a UNAS Pro, and
# the provider exposes no such resource, so NAS exports remain a one-time
# manual step in the UI. The cluster consumes them through the nfs-csi
# StorageClasses under kubernetes/infrastructure/nfs-csi/, which is the right
# place for that concern anyway.
#
# What Terraform can usefully own here is the reservation that stops the node's
# address from being a DHCP accident. Talos itself is configured with a static
# address; the reservation keeps the controller from handing that address to
# anything else.

resource "unifi_client" "node" {
  for_each = var.unifi_enabled ? var.nodes : {}

  mac  = each.value.mac_address
  name = each.key
  note = "Talos ${each.value.machine_type} — managed by Terraform"

  fixed_ip   = each.value.ip
  network_id = var.unifi_network_id != "" ? var.unifi_network_id : null

  # The controller creates a client record as soon as it sees the MAC on the
  # network, which usually happens before the first apply. Adopt that record
  # instead of failing on a conflict.
  allow_existing = true

  # Destroying the reservation should release the address, not leave a
  # half-managed client behind in the controller.
  skip_forget_on_destroy = false
}
