# Todo

Nothing outstanding. What follows is the part that is not code: things a
rebuild would not restore, and things decided against so they stop coming back
as open questions.

## Upgrading Flux

Not Renovate's job — it is switched off for `fluxcd/*` deliberately.
`kubernetes/flux/flux-system/gotk-components.yaml` is generated and carries the
CRDs plus all four controllers for one release, so they move together:

```bash
brew upgrade fluxcd/tap/flux
flux install --export > kubernetes/flux/flux-system/gotk-components.yaml
```

The CLI version is what gets written, so upgrade it first. Commit and push, and
Flux applies its own upgrade through the `flux-system` Kustomization. Currently
on v2.9.4.

Afterwards the four Kustomizations briefly report `DependencyNotReady` while
the restarted controllers walk the chain — that resolves itself. `flux check`
should pass and every CRD version in use should still be served.

## Not in git

The Gitea PR webhook (repo → Settings → Webhooks) posts to ntfy so a new PR
reaches your phone. It lives in Gitea's database, not in this repo, so a Gitea
rebuild loses it. Recreate: POST to `http://ntfy.ntfy/Down` with ntfy basic
auth in the authorization header, event `pull_request`, and ntfy templating in
the query string (`tpl=yes`, plus `title`, `message`, `click`, `priority`).
`ALLOWED_HOST_LIST` in values.yaml is what permits it.

## Not doing

| | why |
|---|---|
| Offsite database backups | Accepted risk. Immich, Outline, AdventureLog and Actual Budget exist only on the NAS. |
| Talos / Kubernetes upgrades via Renovate | Deliberately excluded — a node reboot is not a dependency bump. Run `talosctl upgrade` when you choose to. |
| Second node for HA | Waiting on new hardware. One host means every reboot is a full outage. |
| Immich machine learning | ~2 GB for smart search. Waiting on new hardware. |
| Pinning the last 7 `:latest` images | Stateless — see [inventory.md](inventory.md). The worst a surprise upgrade does is restart them. |
