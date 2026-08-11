# Pi-hole

DNS for the tailnet, not just the LAN. The container joins Tailscale, and its
tailnet address is what every device resolves against — Terraform publishes
that list as the tailnet nameservers. LAN clients go to the router, which is
why changing the container's LAN address is uneventful.

```bash
./provision pihole check          # container spec vs provision.toml
./provision pihole apply --check  # config drift, changes nothing
./provision pihole apply          # converge
```

## Who owns what

| | declares | applies |
|---|---|---|
| `provision.toml` `[pihole]` | host list, addresses, container sizing | — |
| `homelab/terraform/tailscale.tf` | which resolvers the tailnet uses | `provision homelab apply` |
| `ansible/group_vars/piholes.yml` | upstreams, local DNS, blocklists, password | `provision pihole apply` |

The container itself is **not** Terraform-managed, deliberately — see below.

## Two rules

**The container is never recreated.** A rebuild means a new Tailscale identity,
and the nameserver list then points at an address that does not answer. DNS
breaks on every device, including away from home, and the fix requires DNS to
look up how to fix it. Everything else here follows from that.

**So the container spec is asserted, not enforced.** `pihole check` compares the
live container against `provision.toml` and reports; it never corrects. Two
reasons it is not a Terraform resource:

- An `apply` would be one confirmation away from replacing it.
- The provider cannot express the raw `lxc.*` entries that pass `/dev/net/tun`
  through, which is what `tailscaled` needs to run at all. A resource that
  cannot represent the thing keeping DNS alive is worse than no resource.

Changing sizing or the address is `pct set` on the host, then `pihole check` to
confirm reality matches what is written down.

## A second Pi-hole

Add a second `[[pihole.hosts]]` entry and re-run `sync`. The inventory is a
group, so the playbook converges both on the same declared config — Ansible is
the sync mechanism, and no replication tool (orbital-sync and friends) is
involved. Terraform picks the second address up as another tailnet nameserver.

On one Proxmox host that removes the "DNS is down while I patch it" window. It
is not HA against the host itself until there is a second node.

## Idempotence

`pihole-FTL --config <key>` prints values in one format and accepts JSON, so
comparing the two directly reports `changed` forever and the playbook stops
being a drift check. `ansible/filter_plugins/pihole.py` normalises both sides.

The admin password has no read path at all, so the role asks the API to accept
it instead. Note that a **wrong** password still returns HTTP 200, with
`session.valid` true and the reason buried in `session.message` — only a
non-null `sid` means it was right. Keying on the status code silently never
sets the password.

## Upstream resolution

Pi-hole forwards to a local `unbound` on `127.0.0.1#5335`, which forwards over
DNS-over-TLS. `1.0.0.1` and `1.1.1.1` sit alongside it as plain-DNS upstreams
and FTL spreads queries across all three, so a share of lookups skip the
encrypted path. That is how the box was found and it is recorded rather than
silently changed. Drop the two Cloudflare entries from `pihole_upstreams` to
send everything through unbound; keep them if DNS surviving an unbound failure
matters more.
