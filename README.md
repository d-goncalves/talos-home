# talos-home

GitOps config for a single-node [Talos Linux](https://talos.dev) cluster running on Proxmox. Managed with [FluxCD](https://fluxcd.io).

## Stack

| Layer | Tools |
|---|---|
| OS | Talos Linux |
| Hypervisor | Proxmox |
| GitOps | FluxCD + Gitea |
| Ingress | Tailscale Operator |
| Storage | local-path (node) · NFS (Unifi NAS) |

## Apps

| App | Purpose |
|---|---|
| [Immich](https://immich.app) | Photo library |
| [Audiobookshelf](https://audiobookshelf.org) | Audiobooks |
| [Actual Budget](https://actualbudget.org) | Personal finance |
| [Wallos](https://wallosapp.com) | Subscription tracker |
| [AdventureLog](https://adventurelog.app) | Travel tracker |
| [Homebox](https://homebox.software) | Home inventory |
| [Homarr](https://homarr.dev) | Dashboard |
| [WatchYourLAN](https://github.com/aceberg/WatchYourLAN) | Network monitor |
| [LibreSpeed](https://librespeed.org) | Speed test |
| [IT Tools](https://it-tools.tech) | Developer utilities |
| [BentoPDF](https://bentopdf.com) | PDF toolkit |
| Grafana + Loki + Prometheus | Monitoring |

## Repo structure

```
kubernetes/
  apps/          # user-facing applications
  infrastructure/  # cluster-level components (Tailscale operator, storage classes)
  flux/          # FluxCD bootstrap
talos/           # Talos machine config patches
scripts/         # tooling
```

## Recovery

### Prerequisites
- NAS must be online (all app data lives there)
- Tailscale and 1Password signed in on your Mac

### Step 1 — Reinstall Talos on the new VM

In Proxmox, boot the new VM from the Talos ISO, then apply the machine config:

```bash
talosctl apply-config --insecure --nodes NODE_IP_PLACEHOLDER --file talos/controlplane.yaml
```

> The machine config is in this repo under `talos/`. If you don't have the repo yet, fetch the file from 1Password or another backup before running this.

### Step 2 — Restore tooling and repo

On your Mac:

```bash
curl -s https://gitea.<tailnet>.ts.net/admin/talos-home/raw/branch/master/scripts/recover.sh | bash
```

This fetches the talosconfig from 1Password, generates kubeconfig, and clones the repo to `~/talos`.

### Step 3 — Bootstrap Flux

```bash
kubectl apply -k ~/talos/kubernetes/flux
```

Flux reconciles all apps automatically. PVCs bind to NFS and everything comes back up on its own — no manual app restores needed.
