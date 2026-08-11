# Dead-man's switch. Uptime Kuma and ntfy both die with the cluster they watch,
# so a CronJob pings this every five minutes and healthchecks.io alerts when the
# pings stop. See kubernetes/infrastructure/healthcheck/.

# Channels cannot be created through the API, only read, so the notification
# integration is set up in the UI and looked up here by kind.
data "healthchecksio_channel" "email" {
  kind = "email"
}

resource "healthchecksio_check" "cluster" {
  name = "${var.cluster_name} cluster"
  desc = "Pinged every 5 minutes by a CronJob in kube-system. Silence means the cluster, the node or the house is down. Managed by terraform/healthchecks.tf."

  tags = ["homelab", "talos", var.cluster_name]

  # 5 minute period, 15 minute grace: a reboot or a slow reconcile eats two
  # intervals, and an alert that fires during maintenance gets ignored.
  timeout = 5 * 60
  grace   = 15 * 60

  channels = [
    data.healthchecksio_channel.email.id,
  ]
}

# The ping URL embeds the check UUID, so it is a credential — anyone with it
# can fake a heartbeat. Stored in 1Password for the CronJob's ExternalSecret.
output "healthcheck_ping_url" {
  value       = healthchecksio_check.cluster.ping_url
  description = "Ping URL for the cluster dead-man's switch."
  sensitive   = true
}
