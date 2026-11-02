# talos-home

GitOps repo and provisioning CLI for a single-node Kubernetes cluster on Proxmox. Terraform builds a [Talos Linux](https://talos.dev) VM, [Flux](https://fluxcd.io) deploys everything else from git, and a Python CLI, `./provision`, sequences the two.

## What it does

- **Provisions the node.** Terraform builds the Proxmox VM, installs Talos, joins the node to the tailnet, pins its address and bootstraps Kubernetes. It stops at a healthy, empty cluster.
- **Deploys with Flux.** Flux syncs `homelab/kubernetes/` from Gitea and reconciles in order: `infrastructure`, then `external-secrets-store`, then `apps`, each waiting on the last through `dependsOn`.
- **Keeps secrets out of git.** They live in 1Password. Terraform reads them at plan time and the cluster syncs them every hour.
- **Serves apps on the tailnet.**
- **Keeps data on NFS.** Everything durable is on NFS and only disposable caches use local-path, so a rebuilt node reattaches its data.
- **Updates and checks itself.** Renovate opens PRs for charts and images, and patch and minor bumps merge once CI is green. A CronJob pings healthchecks.io every 5 minutes as a dead-man's switch.
- **Rebuilds from scratch** with one idempotent command.

## Stack

| Layer | Tools |
|---|---|
| OS | Talos Linux |
| Hypervisor | Proxmox |
| Provisioning | Terraform (Proxmox, Talos, Tailscale, UniFi, 1Password and healthchecks.io providers) |
| GitOps | FluxCD, with Gitea as the source |
| Secrets | 1Password and External Secrets |
| Ingress | Tailscale Operator |
| Storage | NFS CSI on a UniFi NAS; local-path for disposable caches |
| CI | Gitea Actions runner |
| Updates | Renovate |
| CLI | Python and Click, a thin dispatcher over `terraform`, `talosctl`, `kubectl`, `flux` and `op` |

## Running it

Requirements:

- A Proxmox host prepared as in [docs/proxmox.md](docs/proxmox.md): API token, storage content types and host SSH.
- A 1Password vault holding the secrets. `./provision homelab secrets list` shows what is expected.
- An NFS server. The exports are created once by hand; see [operations](docs/operations.md#adding-a-stateful-app).
- A Tailscale tailnet and a UniFi controller.
- `terraform`, `talosctl`, `kubectl`, `flux`, `op` and Python 3. `./provision` creates its own `.venv` on first run, so nothing installs globally.

```bash
# Configure
$EDITOR provision.toml                      # fill in the <placeholder> values
export OP_ACCOUNT=my.1password.com          # or OP_SERVICE_ACCOUNT_TOKEN

# Build the cluster
./provision homelab preflight               # tooling, 1Password sign-in, config completeness
./provision homelab up                      # preflight, secrets check, config sync, terraform apply,
                                            # kubeconfig and talosconfig, cluster secrets, Flux handoff,
                                            # state backup. Idempotent, so it also resumes after a failure
./provision homelab bootstrap gitea         # optional: Flux reads from Gitea, which runs on this cluster, so a
                                            # cold cluster syncs only once Gitea is up. This speeds it along

# Day to day
./provision homelab check                   # exit 0 = in sync, 2 = changes pending
./provision homelab status                  # cluster state; --deep also runs talosctl health
./provision homelab apps protect            # once, before the first disable
./provision homelab apps disable <app>      # data survives; enabling reattaches the volumes
./provision sandbox new                     # a throwaway Ubuntu VM, ~40s
```

[`provision.toml`](provision.toml) is the single source of truth for non-secret configuration; `terraform.tfvars.json` and `homelab/kubernetes/apps/kustomization.yaml` are generated from it. This public copy has values that identify my network replaced with placeholders such as `<tailnet>` and `<node-ip>`.

## Three stacks

`./provision` manages three things that share a Proxmox host and nothing else:

| | `homelab` | `sandbox` | `pihole` |
|---|---|---|---|
| what | the Talos cluster and its apps | a throwaway Ubuntu VM | DNS for the tailnet |
| driven by | Terraform + Flux | the Proxmox API, directly | Ansible |
| state | Terraform state, backed up to 1Password | none | its own, disposable |
| lifetime | months | minutes | years |

Each has its own state, so a plan aimed at one cannot touch another.

## Repo layout

```
provision          # CLI entrypoint
provision.toml     # all non-secret configuration
homelab/           # the cluster
  terraform/       # Proxmox VM, Talos, Tailscale, UniFi reservation, healthchecks
  kubernetes/      # Flux: apps/, infrastructure/, flux/
  scripts/         # recover.sh (bare-machine bootstrap), deploy-gitea.sh
  docs/            # inventory, upgrade procedures
sandbox/           # a throwaway Ubuntu VM, no state
pihole/            # DNS for the tailnet (Terraform + Ansible)
cli/               # the ./provision implementation (Python + Click, with tests)
docs/              # operations, secret rotation, Proxmox preparation, the diagram
```

<details>
<summary>Apps running on it</summary>

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
| [Ghostfolio](https://ghostfol.io) | Wealth & portfolio tracker |
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
| Grafana + Loki + Prometheus | Metrics & logs — **disabled**, see `[apps]` in provision.toml |

</details>

## Architecture

![Architecture diagram](docs/architecture.png)

> Edge colours: blue = GitOps / networking · green = Flux reconcile · purple = secrets · yellow = NFS storage · red = local-path storage

## Docs

- [Operations](docs/operations.md): Terraform state and the NAS, turning apps on and off, adding a stateful app, the sandbox, recovery after a wipe or a NAS restart, and what is deliberately not done.
- [Rotating secrets](docs/rotate.md) and [preparing a Proxmox host](docs/proxmox.md).
- [Upgrading](homelab/docs/upgrading.md) and [inventory](homelab/docs/inventory.md).
- [CLI](cli/README.md), [Terraform](homelab/terraform/README.md) and [Pi-hole](pihole/README.md).
