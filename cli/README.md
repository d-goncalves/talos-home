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
/path/to/talos/provision apps list
```

`uv` is used to build the venv when available, otherwise `python3 -m venv`.
Delete `.venv/` to force a rebuild.

## A machine from scratch

```bash
./provision up
```

Runs preflight → secrets check → sync → terraform apply → write local configs →
bootstrap → state backup. Every step is idempotent, so it doubles as the resume
path after a failure partway through.

## Is anything pending?

```bash
./provision check
```

Exits 0 when the cluster matches the config, 2 when changes are waiting. Safe to
run on a schedule as a drift check.

## Commands

| Command | Does |
|---|---|
| `up` | The whole cold path, end to end |
| `check` | Drift check; exit 2 means changes pending |
| `sync` | Regenerate `terraform.tfvars.json` and the apps kustomization |
| `plan` `apply` `destroy` `init` `output` | Terraform lifecycle |
| `preflight` | Tooling, 1Password sign-in, `provision.toml` completeness |
| `apps list\|enable\|disable\|protect\|check\|diff` | Which apps run — see below |
| `secrets list\|check` | What 1Password must hold, and whether it does |
| `config write\|show` | Writes `~/.talos/config` and `~/.kube/config` |
| `bootstrap secrets\|gitea\|flux\|all` | Cluster secrets, Gitea, Flux handoff |
| `state backup\|restore\|status` | Terraform state ↔ 1Password document |
| `status` `health` | Cluster state; `talosctl health` |

## Configuration

[`provision.toml`](../provision.toml) at the repo root is the single source of
truth. Two files are generated from it and should never be hand-edited:

| Generated | From | Committed? |
|---|---|---|
| `terraform/terraform.tfvars.json` | `[cluster]` `[proxmox]` `[network]` `[tailscale]` `[unifi]` `[nodes]` | no, gitignored |
| `kubernetes/apps/kustomization.yaml` | `[apps]` | yes, Flux reads it |

`provision sync` rebuilds both; `apply` and `check` do it automatically.

## Turning apps off to free resources

```toml
[apps]
adventurelog = false
immich       = true
```

```bash
./provision apps disable adventurelog
git commit -am "disable adventurelog" && git push
```

Flux prunes the Deployments, StatefulSets, Services and Ingresses, and the CPU
and memory come back. Namespaces, PVCs and PVs are annotated prune-exempt, so
the data stays and `provision apps enable adventurelog` reattaches the same
volumes.

> **Run `provision apps protect` once first.** Flux reads the prune-exempt
> annotation from the object *in the cluster*, not from git. Objects applied
> before this protection existed do not carry it yet and would be pruned on the
> first disable. `apps disable` refuses to run until it can verify the live
> Namespace and PVCs are annotated; `--force` overrides.

Enable and disable edit `provision.toml` in place, preserving comments.

## Secrets

The CLI never handles a secret value. Terraform reads credentials from 1Password
itself through the `onepassword` provider, and ESO does the same in-cluster.

```bash
./provision secrets list    # what is expected, and what reads it
./provision secrets check   # verify presence — never prints values
```

Authentication is environment-only. Set `OP_SERVICE_ACCOUNT_TOKEN`, or
`OP_ACCOUNT` for desktop biometrics; if neither is set the CLI passes through
whichever account `op` is signed in to.

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

CI runs on the GitHub mirror, which lags behind Gitea, and Flux reconciles from
Gitea. So install the pre-push hook — it is the only check that runs *before*
the cluster sees a change:

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
