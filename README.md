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

## Architecture

### Cluster overview

![Architecture diagram](docs/architecture.png)

> **Edge colours:** 🔵 blue = GitOps / networking · 🟢 green = Flux reconcile · 🟣 purple = secrets · 🟡 yellow = NFS storage · 🔴 red = local-path storage

<details>
<summary>Mermaid source (text version)</summary>

```mermaid
graph TB
    subgraph EXTERNAL["☁️ External"]
        OP["1Password\n(cloud)"]
        GH["GitHub\n(mirror)"]
    end

    subgraph NODE["🖥️ Talos Linux — NODE_IP_PLACEHOLDER (Proxmox VM)"]

        subgraph GITOPS["GitOps Layer"]
            GITEA["Gitea\n(source of truth)"]
            FLUX["Flux CD"]
            GITEA -->|"watches via SSH\ngitea-ssh.<tailnet>.ts.net"| FLUX
        end

        subgraph INFRA["Infrastructure Kustomization"]
            TS_OP["Tailscale Operator"]
            NFS_CSI["NFS CSI Driver"]
            COREDNS["CoreDNS\n+ rewrite rule"]
            ESO["External Secrets\nOperator"]
        end

        subgraph APPS["Apps Kustomization"]
            direction LR
            subgraph MEDIA["Media"]
                JF["Jellyfin"]
                JS["Jellyseerr"]
            end
            subgraph SERVARR_GRP["Servarr"]
                SN["Sonarr"]
                RD["Radarr"]
                PR["Prowlarr"]
                BZ["Bazarr"]
                GL["Gluetun + qBittorrent"]
            end
            subgraph PERSONAL["Personal"]
                IM["Immich"]
                AB["Actual Budget"]
                ABS["Audiobookshelf"]
                WL["Wallos"]
            end
            subgraph INFRA_APPS["Infrastructure Apps"]
                GR["Grafana + Loki + Prometheus"]
                NT["ntfy"]
                OL["Outline"]
                UK["Uptime Kuma"]
                HP["Homepage"]
            end
        end

        subgraph NETWORK["Networking"]
            TS_PROXY["Tailscale Proxies\n*.<tailnet>.ts.net"]
            TS_OP --> TS_PROXY
        end

        subgraph STORAGE["Storage"]
            NFS_PVC["NFS PVCs\n(app data — survives wipes)"]
            LOCAL_PVC["local-path PVCs\n(metrics, monitors)"]
            NFS_CSI --> NFS_PVC
        end
    end

    subgraph NAS["🗄️ Unifi NAS — NAS_IP_PLACEHOLDER"]
        NFS_SRV["NFS Server"]
    end

    FLUX -->|"reconciles"| INFRA
    FLUX -->|"reconciles"| APPS
    GH -->|"push mirror"| GITEA
    NFS_PVC <-->|"NFS mount"| NFS_SRV
    APPS -->|"Ingress"| TS_PROXY

    JF & SN & RD & PR & BZ & GL & IM & AB & ABS & WL & OL & GITEA -->|"NFS PVC"| NFS_PVC
    GR & UK -->|"local-path PVC"| LOCAL_PVC
```

</details>

### Flux reconciliation order

```mermaid
graph LR
    FS["flux-system\n(GitRepository + self)"]
    INF["infrastructure\n(Tailscale, NFS, CoreDNS, ESO Helm)"]
    STORE["external-secrets-store\n(ClusterSecretStore)"]
    APPS["apps\n(all user-facing apps)"]

    FS -->|"deploys"| INF
    INF -->|"dependsOn"| STORE
    STORE -->|"dependsOn"| APPS

    style FS fill:#4a4a6a,color:#fff
    style INF fill:#2d5a8e,color:#fff
    style STORE fill:#1a7a4a,color:#fff
    style APPS fill:#7a3a1a,color:#fff
```

### Secret management

```mermaid
sequenceDiagram
    participant OP as 1Password (cloud)
    participant ESO as External Secrets Operator
    participant CSS as ClusterSecretStore (onepasswordSDK)
    participant ES as ExternalSecret
    participant KS as Kubernetes Secret
    participant APP as App Pod

    note over ESO,CSS: Bootstrap (once, via recover.sh)<br/>kubectl create secret onepassword-service-account-token

    ESO->>CSS: registers store using SA token
    CSS->>OP: authenticates via service account token

    note over ES,KS: Every refreshInterval (1h)

    ESO->>ES: watches ExternalSecret resources
    ES->>CSS: requests secret data
    CSS->>OP: fetches item fields
    OP-->>CSS: returns field values
    CSS-->>ES: secret data
    ESO->>KS: creates/updates Kubernetes Secret

    APP->>KS: reads via secretKeyRef or envFrom
```

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

> **Gitea dependency**: The recovery script is hosted on Gitea, which runs on the cluster. If the cluster is completely gone, use the GitHub mirror instead (same content, push-mirrored on every commit).

### Step 1 — Reinstall Talos on the new VM

In Proxmox, boot the new VM from the Talos ISO, then apply the machine config:

```bash
talosctl apply-config --insecure --nodes NODE_IP_PLACEHOLDER --file talos/controlplane.yaml
```

> The machine config is in this repo under `talos/`. Fetch it from 1Password or the GitHub mirror if Gitea is unavailable.

### Step 2 — Restore tooling and repo

On your Mac — the script auto-detects whether Gitea is up and falls back to the GitHub mirror if not:

```bash
curl -s https://gitea.<tailnet>.ts.net/admin/talos-home/raw/branch/master/scripts/recover.sh | bash
```

If Gitea itself is unreachable (total cluster loss), fetch from GitHub instead:

```bash
curl -s https://raw.githubusercontent.com/d-goncalves/talos-home/master/scripts/recover.sh | bash
```

This fetches the talosconfig from 1Password, generates kubeconfig, bootstraps the External Secrets Operator token, and clones the repo to `~/talos`.

### Step 3 — Bootstrap Flux

`recover.sh` creates two manual secrets that are never stored in git:

| Secret | Namespace | Purpose |
|---|---|---|
| `onepassword-service-account-token` | `external-secrets` | ESO → 1Password auth |
| `cluster-vars` | `flux-system` | Flux variable substitution (`TAILNET_DOMAIN`) |

```bash
kubectl apply -k ~/talos/kubernetes/flux
```

Flux's `GitRepository` source points to `ssh://git@gitea-ssh.<tailnet>.ts.net` (the Tailscale address). CoreDNS has a rewrite rule that resolves this to the `gitea-ssh-tailscale` LoadBalancer service inside the cluster. On a fresh cluster the Tailscale Operator and Gitea must be running before Flux can sync — Flux will retry automatically once they come up.

> **Bootstrap order**: Flux applies the infrastructure Kustomization first (Tailscale Operator, NFS CSI, CoreDNS patch), then apps. The Tailscale Operator will register the `gitea-ssh` proxy in the tailnet and Gitea will deploy. Flux retries every minute, so recovery is fully automatic — just wait a few minutes after bootstrap.

Once Flux can sync, it reconciles all apps automatically. Most app data is on NFS and survives node wipes.

### What survives a full node wipe

| Storage | Apps | Survives wipe? |
|---|---|---|
| NFS (Unifi NAS) | Jellyfin, Immich, Sonarr, Radarr, Prowlarr, Bazarr, qBittorrent, Audiobookshelf, Actual Budget, Wallos, Homebox, AdventureLog, Gitea, Outline | ✅ Yes |
| local-path (node disk) | Uptime Kuma (monitors), Prometheus metrics, Grafana dashboards | ❌ No |

### Post-recovery manual steps

After Flux reconciles, the following need manual reconfiguration if the node was wiped:

- **Outline** — data is on NFS, restores automatically ✅
- **Uptime Kuma** — monitors need to be re-added in the UI
- **Ntfy** — admin user is recreated automatically by init container ✅
- **Servarr apps** — Sonarr/Radarr/Prowlarr/Bazarr config is on NFS, should restore automatically ✅

### Secrets management

All app secrets are managed by [External Secrets Operator](https://external-secrets.io) and pulled from 1Password automatically. The only manual bootstrap step is the ESO service account token (handled by `recover.sh`).

The token is stored in 1Password under **"1Password Service Account - talos-home"** in the Server Infrastructure vault. If you need to rotate it, generate a new token at [1password.com](https://1password.com) → Integrations → Service Accounts, then re-run:

```bash
kubectl create secret generic onepassword-service-account-token \
  --from-literal=token=<new-token> \
  --namespace external-secrets \
  --dry-run=client -o yaml | kubectl apply -f -
```

