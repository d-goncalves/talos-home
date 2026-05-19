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
| [Jellyfin](https://jellyfin.org) | Media server |
| [Jellyseerr](https://github.com/Fallenbagel/jellyseerr) | Media requests |
| [Sonarr](https://sonarr.tv) | TV show management |
| [Radarr](https://radarr.video) | Movie management |
| [Prowlarr](https://github.com/Prowlarr/Prowlarr) | Indexer manager |
| [Bazarr](https://www.bazarr.media) | Subtitles |
| [qBittorrent](https://qbittorrent.org) | Torrent client (via Gluetun VPN) |
| [Immich](https://immich.app) | Photo library |
| [Audiobookshelf](https://audiobookshelf.org) | Audiobooks & podcasts |
| [Actual Budget](https://actualbudget.org) | Personal finance |
| [Wallos](https://wallosapp.com) | Subscription tracker |
| [AdventureLog](https://adventurelog.app) | Travel tracker |
| [Homebox](https://homebox.software) | Home inventory |
| [IT Tools](https://it-tools.tech) | Developer utilities |
| [BentoPDF](https://bentopdf.com) | PDF toolkit |
| [Homepage](https://gethomepage.dev) | Dashboard |
| [Gitea](https://gitea.io) | Git repositories |
| [WatchYourLAN](https://github.com/aceberg/WatchYourLAN) | Network monitor |
| [LibreSpeed](https://librespeed.org) | Speed test |
| [Outline](https://www.getoutline.com) | Wiki & docs |
| [Uptime Kuma](https://uptime.kuma.pet) | Uptime monitoring |
| [Ntfy](https://ntfy.sh) | Push notifications |
| Grafana + Loki + Prometheus | Metrics & logs |

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
- NAS must be online (all NFS-backed app data lives there)
- Tailscale and 1Password signed in on your Mac
- The repo cloned locally, or accessible via GitHub mirror

> **⚠️ Gitea dependency**: The recover script is hosted on Gitea, which runs on the cluster. If the cluster is gone, Gitea is unreachable. Either clone this repo to GitHub as a mirror, or store the recover script content in 1Password before you need it.

### Step 1 — Reinstall Talos on the new VM

In Proxmox, boot the new VM from the Talos ISO, then apply the machine config:

```bash
talosctl apply-config --insecure --nodes NODE_IP_PLACEHOLDER --file talos/controlplane.yaml
```

> The machine config is in this repo under `talos/`. Fetch it from 1Password or a GitHub mirror if Gitea is unavailable.

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

Flux reconciles all apps automatically. Most app data is on NFS and survives node wipes.

### What survives a full node wipe

| Storage | Apps | Survives wipe? |
|---|---|---|
| NFS (Unifi NAS) | Jellyfin, Immich, Sonarr, Radarr, Prowlarr, Bazarr, qBittorrent, Audiobookshelf, Actual Budget, Wallos, Homebox, AdventureLog, Gitea, Outline, Grafana data | ✅ Yes |
| local-path (node disk) | Uptime Kuma (monitors), Prometheus metrics, Grafana dashboards | ❌ No |

### Post-recovery manual steps

After Flux reconciles, the following need manual reconfiguration if the node was wiped:

- **Outline** — data is on NFS, restores automatically ✅
- **Uptime Kuma** — monitors need to be re-added in the UI
- **Ntfy** — admin user is recreated automatically by init container ✅
- **Servarr apps** — Sonarr/Radarr/Prowlarr/Bazarr config is on NFS, should restore automatically ✅
