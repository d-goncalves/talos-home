# Todo

## Pin the `:latest` image tags

23 of 49 images run on `:latest` — see [inventory.md](inventory.md) for the
list. A floating tag changes whenever a pod restarts and re-pulls, so upgrades
happen unreviewed, and Renovate cannot track them because there is no version
to bump. The charts are all pinned; images are the gap.

**Pin these — they own data**, so a bad upgrade can migrate a schema or corrupt
config:

`outline` · `adventurelog-backend` · `adventurelog-frontend` · `homebox` ·
`uptime-kuma` · `ntfy` · `actual-server` · `wallos` · `audiobookshelf` ·
`sonarr` · `radarr` · `prowlarr` · `bazarr` · `jellyfin` · `qbittorrent` ·
`watchyourlan`

**Leave these floating** — stateless, worst case is a restart:

`it-tools` · `bentopdf` · `speedtest` · `homepage` · `jellyseerr` · `gluetun` ·
`alpine`

### Finding the running version

`:latest` does not say what it is. In order of reliability:

1. Digest → tag: `kubectl get pod -n <ns> <pod> -o jsonpath='{.status.containerStatuses[0].imageID}'`, then match against the registry's tags.
2. The `org.opencontainers.image.version` label, via `crane` or `skopeo`.
3. Ask the app — Sonarr and Radarr expose `/api/v3/system/status`; Outline and Uptime Kuma show it in settings.

LinuxServer images (`lscr.io/linuxserver/*`) tag unconventionally; read their
published tags rather than assuming semver.

### Doing it

Start with the four that have real databases — Outline, AdventureLog, Homebox,
Uptime Kuma. If that goes cleanly the rest is repetition. A few apps per commit,
so a wrong tag is easy to isolate.

Verify the pinned tag equals what was running: nothing should upgrade as a side
effect. Then pods healthy and 26/26 PVCs bound. Update the counts in
[inventory.md](inventory.md) afterwards.

Once pinned, Renovate takes over — patch and minor merge themselves after CI,
majors open a PR.

## Not doing

Recorded so they stop being rediscovered as open questions.

| | why |
|---|---|
| Offsite database backups | Accepted risk. Immich, Outline, AdventureLog and Actual Budget exist only on the NAS. |
| Talos / Kubernetes upgrades via Renovate | Deliberately excluded — a node reboot is not a dependency bump. Run `talosctl upgrade` when you choose to. |
| Second node for HA | Waiting on new hardware. One host means every reboot is a full outage. |
| Immich machine learning | ~2 GB for smart search. Waiting on new hardware. |
