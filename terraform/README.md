# terraform

Provisions a Talos node on Proxmox, puts it on the tailnet, and pins its
address on the UniFi controller.

## Scope

| Concern | Owner | Why |
|---|---|---|
| Proxmox VM | Terraform (`bpg/proxmox`) | |
| Talos image + system extensions | Terraform (Image Factory) | Extensions cannot be added to a running node |
| Talos config, bootstrap, kubeconfig | Terraform (`siderolabs/talos`) | |
| Tailscale auth key + node enrolment | Terraform (`tailscale/tailscale`) | Replaces the manual key-paste into 1Password |
| Static DHCP reservation | Terraform (`ubiquiti-community/unifi`) | |
| **NAS NFS exports** | **Manual, once, in the UNAS UI** | No UniFi API exposes UNAS shares |
| NFS mounts in-cluster | Flux (`kubernetes/infrastructure/nfs-csi/`) | Already the right place |
| Apps | Flux (`kubernetes/apps/`) | |

Terraform stops at a healthy, empty cluster. Flux takes it from there.

## Secrets

Read from 1Password at plan time via the `onepassword` provider, so no
credential is written to any file here. Authenticate with
`OP_SERVICE_ACCOUNT_TOKEN` or `OP_ACCOUNT`, then:

```bash
./provision homelab secrets check
```

Rotating any of them: [docs/rotate.md](../docs/rotate.md).

## Configuration

`terraform.tfvars.json` is **generated** from `provision.toml` by
`provision homelab sync` and is gitignored. Edit `provision.toml`, not the tfvars.

Running terraform directly works too — just run `provision homelab sync` first so the
tfvars exists.

## State holds secrets, by design

`talos_machine_secrets` writes the cluster CA private key, the bootstrap
tokens and the secretbox encryption key into `terraform.tfstate`. Everything
read from 1Password lands there too.

This is accepted rather than solved: `talos_machine_secrets` exists to generate
that PKI, so it cannot be sourced from 1Password. What matters is that no secret
is in a **committed file**. State is gitignored; back it up after every apply:

```bash
./provision homelab state backup
```

That stores it as a 1Password document in the `Server Infrastructure` vault,
the same trust boundary already used for the ESO service account token.

If you later want state itself encrypted at rest, OpenTofu supports native
state encryption and Terraform does not. The config runs on both.

`.terraform.lock.hcl` **is** committed, so provider versions are reproducible.

## Usage

Configuration comes from `provision.toml` at the repo root:

```bash
./provision homelab sync    # generates terraform.tfvars.json
./provision homelab apply
```

Or use terraform directly after a sync:

```bash
./provision homelab sync
terraform init && terraform apply
```

Requires Terraform >= 1.5.0 or OpenTofu >= 1.6. Validated against Terraform
1.5.7.

## What is and is not proven

`terraform validate` checks schema and references. It does not prove a VM
boots. Be honest about which parts have actually been exercised:

| | Status |
|---|---|
| Config validity, `fmt`, provider schemas | ✅ verified in CI |
| Image Factory: `tailscale` + `qemu-guest-agent` exist for the pinned Talos version | ✅ verified against the live API |
| Schematic POST returns a stable ID | ✅ `2f077b65…` |
| ISO / disk image / **installer OCI manifest** resolve | ✅ HTTP 302 / 200 / 200 |
| Proxmox VM creation, Talos config apply, bootstrap | ❌ **never run** |
| `ExtensionServiceConfig` delivered via `config_patches` | ❌ unverified |

The installer image mattering most: `machine.install.image` points at it, and a
bad reference fails the install after the VM is already up.

To close the remaining gap, provision a small throwaway node. The Proxmox host
has headroom for it (VM 102 `rocky-rke2` is stopped, so ~4 GB is free) — no
need to shrink the running cluster. Point `[nodes.*]` at a free `vm_id` and
~2560 MB, run `./provision homelab up`, confirm, then `./provision homelab destroy`.

## Notes

- **Do not point this at VM 100.** That is the running cluster. The example
  tfvars uses a free `vm_id` and a free address so a first apply cannot touch
  it.
- **`machine = "pc"`, not q35.** q35 has a history of silently failing to boot
  on this host: blank serial console, 99% CPU spin, no ARP.
- **Ballooning is off by default** (`memory_floating = 0`). A ballooned node
  reports more memory than it can use and thrashes under pressure instead of
  reclaiming.
- **The ExtensionServiceConfig patch** (`templates/tailscale-ext.yaml.tftpl`)
  is a separate Talos config document, not a MachineConfig section. It is
  passed through `config_patches`. Confirm it lands on the first apply with:
  `talosctl -n <node> get extensionserviceconfigs`.
- **Prometheus, Grafana and Uptime Kuma are on local-path** and do not survive
  a rebuild. Cheap rebuilds make that loss routine; consider moving them to NFS.
- **Upgrading Talos** means bumping `talos_version`, which produces a new
  schematic ID and a new installer image. Apply reinstalls the node.
