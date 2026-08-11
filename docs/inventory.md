# What is running

Snapshot taken 2026-08-11. The living version of this is
Renovate's **Dependency Dashboard** issue in Gitea — it recomputes nightly and
lists what is available. This file exists to record the shape of the problem,
not to be kept up to date by hand.

## Platform

| | version |
|---|---|
| Talos | v1.13.2 |
| Kubernetes | v1.36.0 |

Neither is tracked by Renovate. Upgrading them is `talosctl upgrade` /
`talosctl upgrade-k8s` — a node reboot you choose, not a dependency bump.

## Helm charts — all pinned

| release | chart | version |
|---|---|---|
| csi-driver-nfs | `csi-driver-nfs` | 4.13.4 |
| external-secrets | `external-secrets` | 2.5.0 |
| immich | `immich` | 0.12.0 |
| local-path-provisioner | `./deploy/chart/local-path-provisioner` | * |
| metrics-server | `metrics-server` | 3.13.1 |
| tailscale-operator | `tailscale-operator` | 1.98.9 |

Renovate proposes patch and minor for these; they merge themselves once CI is
green. Majors open a PR. Gitea, Flux, the runner and Renovate itself are
review-only whatever the semver says — an unattended bump to any of them can
break the loop you would use to roll it back.

## Container images — 26 pinned

Renovate can see these, so they stay current on their own.

| image | namespace |
|---|---|
| `curlimages/curl:8.11.1` | kube-system |
| `docker.gitea.com/gitea:1.26.1-rootless` | gitea |
| `docker.io/valkey/valkey:9.1-alpine` | immich |
| `ghcr.io/external-secrets/external-secrets:v2.5.0` | external-secrets |
| `ghcr.io/fluxcd/helm-controller:v1.5.4` | flux-system |
| `ghcr.io/fluxcd/kustomize-controller:v1.8.5` | flux-system |
| `ghcr.io/fluxcd/notification-controller:v1.8.4` | flux-system |
| `ghcr.io/fluxcd/source-controller:v1.8.4` | flux-system |
| `ghcr.io/immich-app/immich-server:v2.6.3` | immich |
| `ghcr.io/immich-app/postgres:14-vectorchord0.3.0-pgvectors0.2.0` | immich |
| `gitea/act_runner:0.2.13` | gitea |
| `nginxinc/nginx-unprivileged:1.27-alpine` | adventurelog |
| `postgis/postgis:16-3.4` | adventurelog |
| `postgres:16` | outline |
| `rancher/local-path-provisioner:v0.0.31` | local-path-storage |
| `redis:7-alpine` | outline |
| `registry.k8s.io/coredns/coredns:v1.14.2` | kube-system |
| `registry.k8s.io/metrics-server/metrics-server:v0.8.1` | kube-system |
| `registry.k8s.io/sig-storage/csi-node-driver-registrar:v2.17.0` | kube-system |
| `registry.k8s.io/sig-storage/csi-provisioner:v6.3.0` | kube-system |
| `registry.k8s.io/sig-storage/csi-resizer:v2.2.0` | kube-system |
| `registry.k8s.io/sig-storage/csi-snapshotter:v8.6.0` | kube-system |
| `registry.k8s.io/sig-storage/livenessprobe:v2.19.0` | kube-system |
| `registry.k8s.io/sig-storage/nfsplugin:v4.13.4` | kube-system |
| `tailscale/k8s-operator:v1.98.9` | tailscale |
| `tailscale/tailscale:v1.98.9` | tailscale |

## Container images — 23 on `:latest`

**These are the gap.** A floating tag has no version for Renovate to bump, so
it tracks none of them — and they change underneath you whenever a pod
restarts and the image is re-pulled. Two consequences worth being clear about:

- **Upgrades happen unreviewed.** Not nightly through CI, but at whatever
  moment a pod happens to restart.
- **A rebuild does not reproduce what you had.** The NFS migration means the
  *data* comes back exactly; the *software* comes back as whatever `latest`
  points at that day. Those are different guarantees.

| image | namespace |
|---|---|
| `aceberg/watchyourlan:latest` | watchyourlan |
| `actualbudget/actual-server:latest` | actualbudget |
| `adolfintel/speedtest:latest` | librespeed |
| `alpine` | servarr |
| `bellamy/wallos:latest` | wallos |
| `binwiederhier/ntfy:latest` | ntfy |
| `corentinth/it-tools:latest` | it-tools |
| `fallenbagel/jellyseerr:latest` | servarr |
| `ghcr.io/advplyr/audiobookshelf:latest` | audiobookshelf |
| `ghcr.io/alam00000/bentopdf-simple:latest` | bentopdf |
| `ghcr.io/gethomepage/homepage:latest` | homepage |
| `ghcr.io/seanmorley15/adventurelog-backend:latest` | adventurelog |
| `ghcr.io/seanmorley15/adventurelog-frontend:latest` | adventurelog |
| `ghcr.io/sysadminsmedia/homebox:latest` | homebox |
| `louislam/uptime-kuma:latest` | uptime-kuma |
| `lscr.io/linuxserver/bazarr:latest` | servarr |
| `lscr.io/linuxserver/jellyfin:latest` | servarr |
| `lscr.io/linuxserver/prowlarr:latest` | servarr |
| `lscr.io/linuxserver/qbittorrent:latest` | servarr |
| `lscr.io/linuxserver/radarr:latest` | servarr |
| `lscr.io/linuxserver/sonarr:latest` | servarr |
| `outlinewiki/outline:latest` | outline |
| `qmcgaw/gluetun:latest` | servarr |

Pinning them is mechanical: replace `:latest` with the tag running today, and
Renovate takes over from there. Worth doing per-app rather than in one sweep,
since each one needs its current digest resolved to a real version.

