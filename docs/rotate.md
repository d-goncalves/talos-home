# Rotating secrets

Every secret lives in 1Password. Rotating one means changing it there and, for
some, telling something to pick it up.

Three rules cover most of it:

- **Terraform** reads 1Password on every run, so most rotations need no action.
- **External Secrets** re-syncs into the cluster within **1 hour**.
- **App admin passwords are the exception** — the app only reads them when it
  first creates the account, and ignores 1Password forever after.

## No action needed

Terraform reads these fresh on every `plan` and `apply`. Change the 1Password
item and you are done; the next apply uses the new value.

| Secret | 1Password item |
|---|---|
| healthchecks.io API key | `Healthchecks.io - talos-home` |
| Proxmox API token | `Proxmox API - talos-home` — role setup in [proxmox.md](proxmox.md) |
| Tailscale OAuth client | `Tailscale Talos` |
| UniFi local admin | `UniFi Local Admin - talos-home` |

> The old value stays in `terraform.tfstate` until the next apply. Harmless —
> the state is gitignored, and the old secret is revoked.

## App secrets — wait an hour, or force it

External Secrets pulls these from 1Password on a 1 hour interval. Update the
item, then either wait or force it:

```bash
kubectl annotate externalsecret -n <namespace> <name> force-sync=$(date +%s) --overwrite
```

| App | 1Password item |
|---|---|
| adventurelog | `AdventureLog - talos-home` |
| gitea | `Gitea` |
| homebox | `Homebox - talos-home` |
| immich | `Immich - talos-home` |
| monitoring | `Grafana - talos-home` |
| ntfy | `ntfy` |
| outline | `Outline - talos-home` |
| servarr | `servarr` |
| tailscale-operator | `Tailscale Talos` ⚠ shared |

> ⚠ `Tailscale Talos` is read by **both** the Tailscale operator in the cluster
> and Terraform. Rotating it means doing both: force the ExternalSecret sync
> **and** run `./provision homelab apply`.

Most apps also need a restart to read the new value:

```bash
kubectl rollout restart -n <namespace> deploy/<name>
```

## Admin passwords — the one that catches you out

Apps that seed an admin account from an environment variable only honour it
**when the account is created**. Change 1Password and the app keeps the old
password forever, with nothing reporting a problem. This is why an
AdventureLog login failed against a password that was provably correct.

Affects: **AdventureLog**, **Gitea**, **Grafana**, **ntfy**.

Rotate the 1Password item, then reset it inside the app as well. For
AdventureLog (Django), from the environment the pod already has:

```bash
kubectl exec -n adventurelog deploy/adventurelog-backend -- python manage.py shell -c '
import os
from django.contrib.auth import get_user_model
U = get_user_model()
u = U.objects.get(username=os.environ["DJANGO_ADMIN_USERNAME"])
u.set_password(os.environ["DJANGO_ADMIN_PASSWORD"])
u.save()
print("reset:", u.check_password(os.environ["DJANGO_ADMIN_PASSWORD"]))'
```

For ntfy:

```bash
kubectl exec -n ntfy deploy/ntfy -- ntfy user change-pass <username>
```

Restart the pod first so it has the new value in its environment.

## Cluster credentials — re-seed them

These are written into the cluster once by `bootstrap`, not by External
Secrets, so they need re-seeding after a rotation.

| Secret | 1Password item | After rotating |
|---|---|---|
| 1Password service account (used by ESO) | `1Password Service Account - talos-home` | `./provision homelab bootstrap secrets` |
| Flux deploy key | `Flux Deploy Key - talos-home` | `./provision homelab bootstrap secrets`, then replace the deploy key in Gitea → repo → Settings → Deploy Keys |

## Terraform-generated — replace them

Not stored in 1Password. Terraform creates them, so rotating means asking
Terraform for a new one.

**Tailscale node auth key** — only used when provisioning a node. The running
node already holds its own key, so this does not interrupt anything:

```bash
cd terraform && terraform apply -replace='tailscale_tailnet_key.node[0]'
```

**Talos cluster secrets** (cluster CA, etcd CA, bootstrap token, secretbox key)
— replacing these means rebuilding the cluster. Do not rotate them unless you
believe they have leaked, in which case follow [recovery](../README.md#recovery).

Back the state up afterwards, since it holds the new values:

```bash
./provision homelab state backup
```

## GitHub mirror token

Gitea stores this in its own database, not in 1Password, so nothing picks it up
automatically. After rotating the token, replace the mirror in
**Gitea → repo → Settings → Repository → Mirror Settings**, or leave the item
updated and ask for the mirror to be recreated.

The token needs `Contents: Read and write`. A classic token with `repo` works
too.

> It previously also needed `Workflows: Read and write`, because the repo
> carried `.github/workflows/`. That is gone — CI runs on the Gitea runner —
> so a plain contents-write token is now enough.

## Checking it worked

```bash
./provision homelab secrets check      # what 1Password must hold, and whether it does
kubectl get externalsecrets -A         # every app secret, and whether it synced
cd terraform && terraform plan         # proves Terraform can still authenticate
```
