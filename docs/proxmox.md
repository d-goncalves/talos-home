# Preparing a Proxmox host

Four things are set up by hand before the first `./provision homelab up`. None
are Terraform-managed — they are what Terraform authenticates *with*.

`./provision homelab preflight` checks all of them, so run that first rather
than discovering a missing privilege partway through an apply.

## 1. API token

Run on the Proxmox host:

```bash
pveum role add TerraformProv --privs "\
VM.Allocate,VM.Audit,VM.Clone,VM.PowerMgmt,VM.Migrate,\
VM.Config.CDROM,VM.Config.CPU,VM.Config.Cloudinit,VM.Config.Disk,\
VM.Config.HWType,VM.Config.Memory,VM.Config.Network,VM.Config.Options,\
VM.GuestAgent.Audit,VM.Snapshot,VM.Snapshot.Rollback,\
Datastore.Allocate,Datastore.AllocateSpace,Datastore.AllocateTemplate,\
Datastore.Audit,Sys.Audit,Sys.Modify"

pveum user add terraform@pve
pveum aclmod / --user terraform@pve --role TerraformProv
pveum user token add terraform@pve provision --privsep 0
```

The last command prints the token **once**. Store it in 1Password as
`Proxmox API - talos-home`, with the password field in the form
`USER@REALM!TOKENID=UUID`.

`VM.Snapshot` and `VM.Snapshot.Rollback` are only needed by
`provision sandbox reset`; everything else fails without the rest.

## 2. Storage content types

```bash
pvesm set local --content iso,vztmpl,backup,import,snippets
```

| content | needed for |
|---|---|
| `iso` | the Talos boot image |
| `import` | `sandbox template` — importing the Ubuntu cloud image |
| `snippets` | the sandbox's cloud-init user-data, and so its console autologin |

The VM disk store (`local-lvm` by default) needs `images`, which is the Proxmox
default.

## 3. SSH from your machine to the host

```bash
ssh-copy-id root@<proxmox-ip>
ssh root@<proxmox-ip> true    # must succeed without a password
```

The Terraform provider shells out over SSH for disk import, and
`sandbox new` copies its cloud-init snippet the same way — the Proxmox API
cannot upload snippets.

## 4. NAS exports

Add each node's IP to the NFS export allow-list on the UNAS. There is no UniFi
API for this, so it stays manual. `preflight` verifies it with `showmount -e`.

## Then

```bash
$EDITOR provision.toml          # endpoint, node_name, datastore ids, bridge, install_disk
./provision homelab preflight
./provision homelab up
```

One more thing that is not a Proxmox setting: if this tailnet has had a cluster
before, delete the old `tag:k8s` devices in the Tailscale admin console first.
Tailscale suffixes duplicate hostnames, so apps come back as `immich-1`,
`gitea-2` and so on.
