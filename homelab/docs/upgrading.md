# Upgrading

Renovate handles Helm charts and container images on its own — patch and minor
merge themselves once CI is green, majors open a PR. See
[inventory.md](inventory.md) for what that covers.

Three things it deliberately does not touch, because none of them is a
dependency bump.

## Flux

Renovate is switched off for `fluxcd/*`. `homelab/kubernetes/flux/flux-system/gotk-components.yaml`
is generated and carries the CRDs alongside all four controllers for one
release, so bumping a single image would leave that controller newer than the
CRDs it reads and than its three siblings.

```bash
brew upgrade fluxcd/tap/flux
flux install --export > homelab/kubernetes/flux/flux-system/gotk-components.yaml
```

The CLI version is what gets written, so upgrade it first. Commit and push, and
Flux applies its own upgrade through the `flux-system` Kustomization.

Afterwards the four Kustomizations briefly report `DependencyNotReady` while
the restarted controllers walk the chain — that resolves itself. Then:

```bash
flux check
flux get kustomizations
```

Every CRD version the repo uses should still be served and still be the storage
version. Read the release notes first: v2.9 arrived with a behaviour change
that turned a tolerated manifest into a hard build error, and CI did not catch
it because `kubectl kustomize` does no post-build substitution. The
`apps vars` audit exists because of that, and now runs in CI.

## Talos and Kubernetes

A node reboot you choose, not something a bot should start — on a single node
it is a full outage.

```bash
talosctl upgrade --nodes <ip> --image ghcr.io/siderolabs/installer:<version>
talosctl upgrade-k8s --nodes <ip> --to <version>
```

The pinned versions live in `provision.toml`; update them so a rebuild matches
what is running.

## Postgres majors

Renovate proposes nothing here — `postgres`, `postgis`, `redis` and `valkey`
majors are disabled outright. Postgres refuses to start against a data
directory written by an older major, so this is a `pg_upgrade` or a
dump-and-restore, not a tag change. A PR that looked routine would be one click
from taking Immich, Outline and AdventureLog down. Minor and patch still flow
normally.
