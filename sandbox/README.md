# Sandbox

A throwaway Ubuntu VM on the same Proxmox host. Not Terraform, not Flux: the
whole thing is direct Proxmox API calls from `./provision sandbox`, so it holds
no state and a mistake here cannot plan a change against the cluster.

```bash
./provision sandbox new        # create and boot it
./provision sandbox console    # serial console, --serial for raw
./provision sandbox offline    # disconnect the NIC, keep the VM
./provision sandbox destroy    # remove it entirely
```

Configuration is `[sandbox]` in [provision.toml](../provision.toml). The
implementation is `cli/src/provision/commands/sandbox.py` — there is no
`sandbox/` infrastructure directory because there is no infrastructure to
declare: the VM is created, used and deleted within one command each.

Its address must sit inside the 192.168.1.20-29 range reserved for these
machines; .21 is the Talos node and .23 is Pi-hole.
