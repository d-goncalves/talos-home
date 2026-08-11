# ── Dead-man's switch ─────────────────────────────────────────────────────────
#
# Uptime Kuma and ntfy both run on the cluster they watch, so when the cluster
# died the monitor, the notifier and the subject all went down together and
# nothing was reported. A watchdog inside the box cannot report the box being
# dead.
#
# This inverts the dependency: a CronJob in the cluster pings healthchecks.io
# every five minutes, and if the pings stop, healthchecks.io says so. Silence
# becomes the signal, and the alerting path shares nothing with the cluster --
# not the hardware, not the network, not the notifier.
#
# It is also the only shape of external monitoring that works here: nothing
# needs to reach in past the tailnet, because the cluster calls out.

# Channels cannot be created through the API — the provider only reads them —
# so notification integrations are set up in the healthchecks.io UI and looked
# up here by kind.
data "healthchecksio_channel" "email" {
  kind = "email"
}

resource "healthchecksio_check" "cluster" {
  name = "${var.cluster_name} cluster"
  desc = "Pinged every 5 minutes by a CronJob in kube-system. Silence means the cluster, the node or the house is down. Managed by terraform/healthchecks.tf."

  tags = ["homelab", "talos", var.cluster_name]

  # Expect a ping every 5 minutes, but do not alert until 15 have passed. A
  # node reboot or a Flux reconcile can comfortably eat two intervals, and an
  # alert that cries wolf during routine maintenance is one you learn to
  # ignore.
  timeout = 5 * 60
  grace   = 15 * 60

  channels = [
    data.healthchecksio_channel.email.id,
  ]
}

# The ping URL embeds the check's UUID, so it is a credential in its own right:
# anyone holding it can fake a heartbeat. It is deliberately not committed —
# `provision homelab output` prints it, and it is stored in 1Password for the
# ExternalSecret that feeds the CronJob.
output "healthcheck_ping_url" {
  value       = healthchecksio_check.cluster.ping_url
  description = "Ping URL for the cluster dead-man's switch."
  sensitive   = true
}
