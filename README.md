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
| [Docmost](https://docmost.com) | Wiki |
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

On a new machine, sign into Tailscale and 1Password, then run:

```bash
curl -s https://gitea.<tailnet>.ts.net/admin/talos-home/raw/branch/master/scripts/recover.sh | bash
```

Fetches the talosconfig from 1Password, generates kubeconfig, and clones the repo.
