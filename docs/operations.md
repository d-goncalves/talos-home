# Operations

Day-to-day and recovery notes for the cluster. The overview is in the [README](../README.md).

## Terraform state and the NAS

NAS exports are not managed here: no UniFi API exposes UNAS shares, so those
stay a one-time manual step and are consumed by the `nfs-csi` StorageClasses.
Adding an app with its own database is a *second*, recurring manual step, see
[Adding a stateful app](#adding-a-stateful-app).

> Terraform state holds the cluster CA key, bootstrap tokens and the secretbox
> key: `talos_machine_secrets` generates them, so they cannot come from
> 1Password. State is gitignored, so back it up with
> `./provision homelab state backup` after every apply.

See [homelab/terraform/README.md](../homelab/terraform/README.md) and [cli/README.md](../cli/README.md).

## Turning apps on and off

[provision.toml](../provision.toml) decides which apps Flux deploys. After `./provision homelab apps disable <app>`, commit and push so Flux sees the change.

Namespaces, PVCs and PVs are annotated prune-exempt, so data survives and
enabling reattaches the same volumes. Flux reads that annotation from the
cluster, not from git, so `./provision homelab apps protect` has to run before
an app leaves the source. `apps disable` refuses until it can verify this.

## Adding a stateful app

Copy an existing app with the same shape — Outline (Postgres + Redis) is the
usual template — and add a `StorageClass` for each new PVC under
[homelab/kubernetes/infrastructure/nfs-csi/](../homelab/kubernetes/infrastructure/nfs-csi/).

That StorageClass's `share` path will not exist on the NAS yet, and the CSI
driver only creates the `subDir` *inside* an existing share — never the share
itself. Skip this and the PVC sits `Pending` forever with `mount.nfs: ...
No such file or directory` in its events. Create the directory once, from
inside the cluster (the node's IP is what the NAS export allow-lists, not
this machine's):

```bash
kubectl run nas-mkdir --restart=Never -n kube-system --image=alpine:3.20 --overrides='{
  "spec": {"containers": [{
    "name": "mkdir", "image": "alpine:3.20",
    "securityContext": {"privileged": true, "capabilities": {"add": ["SYS_ADMIN"]}},
    "command": ["/bin/sh", "-c"],
    "args": ["apk add --no-cache nfs-utils >/dev/null && mount -t nfs -o nfsvers=3,nolock <NAS_IP>:<NAS_DATA_PATH> /mnt && mkdir -p /mnt/<app>-<component> && umount /mnt"]
  }]}
}'
kubectl wait --for=jsonpath='{.status.phase}'=Succeeded pod/nas-mkdir -n kube-system --timeout=60s
kubectl delete pod nas-mkdir -n kube-system
```

`--image` has to be passed twice — once as its own flag (`kubectl run` requires
it even when `--overrides` also sets it) and once inside the override.

`kube-system` is the only namespace exempt from this cluster's PodSecurity
baseline, which is also why the NFS CSI driver's own pods live there — a
`privileged` pod anywhere else is refused before it starts.

## The sandbox

A disposable Ubuntu VM for testing packages you would rather not run on a
machine you care about.

```bash
./provision sandbox template          # once — builds the golden image
./provision sandbox new               # clone and boot, ~40s
./provision sandbox payload ./stuff   # attach a directory as a read-only CD
./provision sandbox offline           # unplug the virtual cable
./provision sandbox console --serial  # the only way in once offline
./provision sandbox destroy
```

`offline` drops the NIC's carrier rather than deleting it, so `online` brings it
straight back. The payload is an ISO, so the guest cannot write to it and you
never mount an untrusted filesystem on the host.

Note that cutting the link afterwards does not undo the install — if a package
is actively malicious, the dangerous moment is `apt install`. Use
`new --offline` with a `payload` when that matters.

## Rotating secrets

Every secret lives in 1Password. Most rotations need no action at all — see
[docs/rotate.md](rotate.md) for the per-secret table, including the one
trap: app admin passwords are only read when the account is first created.

## Recovery

Recovering a wiped node is the same operation as provisioning a new one.

### Prerequisites

A Proxmox host prepared as in [docs/proxmox.md](proxmox.md) — API token,
storage content types, host SSH.
- NAS online — all NFS-backed app data lives there
- 1Password unlocked (`OP_ACCOUNT` or `OP_SERVICE_ACCOUNT_TOKEN`)
- Proxmox reachable

### On a machine that already has the repo

```bash
./provision homelab up
```

### On a bare machine

`homelab/scripts/recover.sh` installs the tooling and clones the repo, then hands off
to the CLI. It is fetched from the **GitHub mirror**, not Gitea — Gitea runs on
the cluster being recovered.

```bash
bash <(curl -s https://raw.githubusercontent.com/d-goncalves/talos-home/master/homelab/scripts/recover.sh)
```

Then:

```bash
cd ~/talos
$EDITOR provision.toml            # fill in the <placeholder> values
export OP_ACCOUNT=my.1password.com
./provision homelab up
```

`./provision homelab up` runs preflight, checks the 1Password entries, regenerates the
generated files, provisions the VM and Talos, writes `~/.kube/config` and
`~/.talos/config`, seeds the two cluster secrets, hands off to Flux, and backs
up Terraform state. Every step is idempotent, so re-run it after fixing
whatever stopped it.

> **Bootstrap ordering.** Flux sources its `GitRepository` from Gitea over SSH,
> and Gitea runs on this cluster. On a cold cluster Flux cannot sync until the
> Tailscale operator and Gitea are up. It retries every minute, so this
> resolves itself — deploy Gitea with `./provision homelab bootstrap gitea` if you want
> to hurry it along.

### What survives a full node wipe

Everything except Immich's valkey cache. Every PVC that holds anything worth
keeping is NFS-backed, including the servarr configs, Audiobookshelf metadata
and Uptime Kuma's database. Check with:

```bash
kubectl get pvc -A -o custom-columns=SC:.spec.storageClassName,NS:.metadata.namespace,NAME:.metadata.name
```

Verified by destroying the cluster and rebuilding it: apps came back with their
data attached and nothing needed reconfiguring.

### What a rebuild does not restore

The Gitea PR webhook (repo → Settings → Webhooks) posts to ntfy so a new PR
reaches your phone. It lives in Gitea's database, not in this repo, so a Gitea
rebuild loses it — silently, since nothing fails.

Recreate it: POST to `http://ntfy.ntfy/Down` with ntfy basic auth in the
authorization header, event `pull_request`, and ntfy templating in the query
string (`tpl=yes`, plus `title`, `message`, `click`, `priority`).
`ALLOWED_HOST_LIST` in `homelab/kubernetes/apps/gitea/values.yaml` is what permits
Gitea to call an in-cluster address at all.

Pi-hole is the second. Its container is deliberately never recreated: the
tailnet nameserver list points at its Tailscale address, so a rebuilt container
gets a new identity and every device loses DNS — including away from home. See
[pihole/README.md](../pihole/README.md).

### After the NAS restarts

Mounts use `hard`, so I/O blocks rather than erroring while the NAS is away —
that protects data, but leaves processes kubelet cannot kill.

```bash
./provision homelab nas recover
```

Waits for the NAS, proves it is writable, then restarts only NFS-backed pods
(16 at present, across 9 namespaces). Cleanest of all is to avoid the situation:
scale those apps down before a planned NAS reboot.

## Deliberately not doing

Recorded so they stop being rediscovered as open questions.

| | why |
|---|---|
| Offsite database backups | Accepted risk. Immich, Outline, AdventureLog and Actual Budget exist only on the NAS. |
| Talos / Kubernetes upgrades via Renovate | A node reboot is not a dependency bump — see [homelab/docs/upgrading.md](../homelab/docs/upgrading.md). |
| Second node for HA | Waiting on new hardware. One host means every reboot is a full outage. |
| Immich machine learning | ~2 GB for smart search. Waiting on new hardware. |
| Pinning the last 7 `:latest` images | Stateless — see [homelab/docs/inventory.md](../homelab/docs/inventory.md). The worst a surprise upgrade does is restart them. |
