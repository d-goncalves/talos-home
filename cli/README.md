# provision

A thin dispatcher over `terraform`, `talosctl`, `kubectl`, `flux` and `op`.

The infrastructure logic lives in `terraform/`. This exists to sequence it and
to handle the parts Terraform cannot own: seeding cluster secrets, handing off
to Flux, and backing up state.

One dependency (`click`). TOML parsing is stdlib.

## Running it

There is nothing to install. Use the `./provision` shim at the repo root:

```bash
./provision --help
```

On first run it creates a `.venv` inside the repo and installs this package
into it, editable. Nothing lands in your global Python, so the command cannot
leak into other projects, and edits under `cli/src/provision/` take effect with
no reinstall. Later runs skip straight to the CLI (~0.2s).

It resolves the repo from its own location, so it works from any directory:

```bash
/path/to/talos/provision homelab apps list
```

`uv` is used to build the venv when available, otherwise `python3 -m venv`.
Delete `.venv/` to force a rebuild.

## A machine from scratch

```bash
./provision homelab up
```

Runs preflight → secrets check → sync → terraform apply → write local configs →
bootstrap → state backup. Every step is idempotent, so it doubles as the resume
path after a failure partway through.

## Is anything pending?

```bash
./provision homelab check
```

Exits 0 when the cluster matches the config, 2 when changes are waiting. Safe to
run on a schedule as a drift check.

## Commands

Three groups, one per project directory: `homelab` is the Talos cluster in
`homelab/`, driven by Terraform and Flux; `sandbox` is a throwaway VM in
`sandbox/`, driven straight through the Proxmox API; `pihole` is DNS for the
tailnet in `pihole/`, driven by Ansible.

They share the config loader, 1Password access, the Proxmox client and the
console helpers, and nothing else — in particular the sandbox has no Terraform
state, so no sandbox command can plan a change against the cluster.

### `provision homelab`

| Command | Does |
|---|---|
| `up` | The whole cold path, end to end |
| `check` | Drift check; exit 2 means changes pending |
| `sync` | Regenerate `terraform.tfvars.json` and the apps kustomization |
| `plan` `apply` `destroy` `init` `output` | Terraform lifecycle |
| `preflight` | Tooling, 1Password sign-in, `provision.toml` completeness |
| `apps list\|enable\|disable\|protect\|check\|diff\|storage\|vars` | Which apps run — see below |
| `secrets list\|check` | What 1Password must hold, and whether it does |
| `config write\|show` | Writes `~/.talos/config` and `~/.kube/config` |
| `bootstrap secrets\|gitea\|flux\|all` | Cluster secrets, Gitea, Flux handoff |
| `state backup\|restore\|status` | Terraform state ↔ 1Password document |
| `status` | Cluster state; `--deep` also runs `talosctl health` |

### `provision pihole`

| Command | Does |
|---|---|
| `check` | Live containers vs `provision.toml`; reports, never corrects |
| `apply` | Converge the config; `--check` for drift, `--limit` for one host |
| `dns plan\|apply\|init` | The tailnet nameserver list — its own Terraform root and state |

`dns` is a second Terraform root with its own state, separate from the
cluster's — see [pihole/README.md](../pihole/README.md), which also explains why
the containers themselves are deliberately not Terraform-managed.

### `provision sandbox`

| Command | Does |
|---|---|
| `template` | Build the Ubuntu cloud-init golden image (once) |
| `new` | Clone and boot; `--offline` starts with the NIC down |
| `payload DIR` | Attach a directory as a read-only CD, hotplugged |
| `offline` `online` | Disconnect / reconnect the NIC (`online --reboot`) |
| `console` | How to reach the console; `--serial` attaches to it |
| `reset` | Roll back to the first-boot snapshot |
| `destroy` | Stop and delete |
| `status` | Exists, running, connected, payload attached |

## Configuration

[`provision.toml`](../provision.toml) at the repo root is the single source of
truth. Two files are generated from it and should never be hand-edited:

| Generated | From | Committed? |
|---|---|---|
| `terraform/terraform.tfvars.json` | `[cluster]` `[proxmox]` `[network]` `[tailscale]` `[unifi]` `[nodes]` | no, gitignored |
| `kubernetes/apps/kustomization.yaml` | `[apps]` | yes, Flux reads it |

`provision homelab sync` rebuilds both; `apply` and `check` do it automatically.

## Turning apps off to free resources

```bash
./provision homelab apps protect              # once, before the first disable
./provision homelab apps disable adventurelog
```

Data survives: namespaces, PVCs and PVs are annotated prune-exempt, so
re-enabling reattaches the same volumes. See the README for why `protect` has
to run first.

## Secrets

The CLI never handles secret values. Terraform reads 1Password itself, and ESO
does the same in-cluster.

```bash
./provision homelab secrets list    # what is expected, and what reads it
./provision homelab secrets check   # verify presence — never prints values
```

Rotating any of them: [docs/rotate.md](../docs/rotate.md).

## Tests

```bash
pip install -e "cli[dev]"
pytest cli/tests -q
```

CI runs these plus `pyflakes`, a `terraform validate`, a `kustomize build` of
every overlay, and two checks that have each caught a real outage:

- **the generated kustomization matches `provision.toml`** — otherwise a
  hand-edit survives until the next `sync` silently overwrites it
- **nothing under `kubernetes/` is untracked** — a manifest referencing an
  uncommitted file builds locally and breaks the whole Kustomization once Flux
  pulls it

CI runs on the Gitea runner (`.gitea/workflows/ci.yml`), against the same
repository Flux reconciles from. The pre-push hook is still worth installing —
it is the only check that runs *before* the push:

```bash
ln -sf ../../scripts/pre-push .git/hooks/pre-push
```

## Terraform vs OpenTofu

Either works. `tofu` wins if both are installed; override with
`PROVISION_TF_BIN`.

## Notes

- `bootstrap secrets` reads `NAS_IP` and `NAS_DATA_PATH` from the environment
  or prompts. The NAS is not Terraform-managed, so they cannot come from state.
- `destroy` requires typing the cluster name.
- `config write` refuses to overwrite existing files without `--force`.
