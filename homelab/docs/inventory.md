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
| Flux | v2.9.4 |

None is tracked by Renovate. Talos and Kubernetes are `talosctl upgrade` /
`talosctl upgrade-k8s` — a node reboot you choose, not a dependency bump. Flux
is upgraded as a bundle; see [upgrading.md](upgrading.md).

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

## Container images — 43 pinned

Renovate can see these, so they stay current on their own.

| image | namespace |
|---|---|
| `aceberg/watchyourlan:2.1.4` | watchyourlan |
| `actualbudget/actual-server:26.8.1` | actualbudget |
| `bellamy/wallos:5.4.2` | wallos |
| `binwiederhier/ntfy:v2.27.0` | ntfy |
| `curlimages/curl:8.11.1` | kube-system |
| `docker.gitea.com/gitea:1.26.1-rootless` | gitea |
| `docker.io/valkey/valkey:9.1-alpine` | immich |
| `ghcr.io/advplyr/audiobookshelf:2.36.0` | audiobookshelf |
| `ghcr.io/external-secrets/external-secrets:v2.5.0` | external-secrets |
| `ghcr.io/fluxcd/helm-controller:v1.6.3` | flux-system |
| `ghcr.io/fluxcd/kustomize-controller:v1.9.4` | flux-system |
| `ghcr.io/fluxcd/notification-controller:v1.9.3` | flux-system |
| `ghcr.io/fluxcd/source-controller:v1.9.4` | flux-system |
| `ghcr.io/immich-app/immich-server:v2.6.3` | immich |
| `ghcr.io/immich-app/postgres:14-vectorchord0.3.0-pgvectors0.2.0` | immich |
| `ghcr.io/seanmorley15/adventurelog-backend:latest` (digest-pinned) | adventurelog |
| `ghcr.io/seanmorley15/adventurelog-frontend:latest` (digest-pinned) | adventurelog |
| `ghcr.io/sysadminsmedia/homebox:0.26.2` | homebox |
| `gitea/act_runner:0.2.13` | gitea |
| `louislam/uptime-kuma:1.23.17` | uptime-kuma |
| `lscr.io/linuxserver/bazarr:v1.6.0-ls357` | servarr |
| `lscr.io/linuxserver/jellyfin:10.11.11ubu2604-ls43` | servarr |
| `lscr.io/linuxserver/prowlarr:2.5.2.5491-ls156` | servarr |
| `lscr.io/linuxserver/qbittorrent:5.2.3_v2.0.13-ls470` | servarr |
| `lscr.io/linuxserver/radarr:6.3.0.10514-ls313` | servarr |
| `lscr.io/linuxserver/sonarr:4.0.19.2979-ls321` | servarr |
| `nginxinc/nginx-unprivileged:1.27-alpine` | adventurelog |
| `outlinewiki/outline:1.9.2` | outline |
| `postgis/postgis:16-3.4` | adventurelog |
| `postgres:16` | outline |
| `rancher/local-path-provisioner:v0.0.31` | local-path-storage |
| `redis:7-alpine` | outline |
| `registry.k8s.io/coredns/coredns:v1.14.2` | kube-system |
| `renovate/renovate:39.264.0` | kube-system |
| `registry.k8s.io/metrics-server/metrics-server:v0.8.1` | kube-system |
| `registry.k8s.io/sig-storage/csi-node-driver-registrar:v2.17.0` | kube-system |
| `registry.k8s.io/sig-storage/csi-provisioner:v6.3.0` | kube-system |
| `registry.k8s.io/sig-storage/csi-resizer:v2.2.0` | kube-system |
| `registry.k8s.io/sig-storage/csi-snapshotter:v8.6.0` | kube-system |
| `registry.k8s.io/sig-storage/livenessprobe:v2.19.0` | kube-system |
| `registry.k8s.io/sig-storage/nfsplugin:v4.13.4` | kube-system |
| `tailscale/k8s-operator:v1.98.9` | tailscale |
| `tailscale/tailscale:v1.98.9` | tailscale |

## Container images — 8 still on `:latest`

Deliberately. Each is stateless: it holds no database and no config worth
keeping, so the worst a surprise upgrade does is restart it. `alpine` and
`alpine/curl` are not services at all — they run a command and exit.

| image | namespace |
|---|---|
| `adolfintel/speedtest:latest` | librespeed |
| `alpine` | servarr |
| `alpine/curl` | servarr |
| `corentinth/it-tools:latest` | it-tools |
| `fallenbagel/jellyseerr:latest` | servarr |
| `ghcr.io/alam00000/bentopdf-simple:latest` | bentopdf |
| `ghcr.io/gethomepage/homepage:latest` | homepage |
| `qmcgaw/gluetun:latest` | servarr |

## Finding what a `:latest` image actually is

Should another one need pinning, in order of reliability:

1. The `org.opencontainers.image.version` label on the image config blob —
   authoritative when present, absent on roughly a third of these.
2. Digest to tag: read `.status.containerStatuses[].imageID` off the pod, then
   match it against the registry's tag list. Docker Hub's `/v2/repositories`
   API returns digests inline, which makes this cheap; other registries need a
   manifest request per tag.
3. Ask the app — `ntfy --version`, or the `version` field in an app's
   `package.json`.

Two traps. LinuxServer images tag as `<upstream>-ls<build>` and are served from
`lscr.io`, which proxies `ghcr.io/linuxserver/*` — the registry API works
against ghcr, not lscr. And a `:latest` that is built from `main` may have no
release tag equal to it, which is why AdventureLog is pinned by digest.
