#!/usr/bin/env bash
set -euo pipefail

# Deploys/upgrades Gitea via Helm.
#
# Gitea is intentionally NOT managed by a Flux HelmRelease: Flux sources its
# GitRepository from Gitea, so letting Flux own Gitea creates a chicken-and-egg
# dependency. That means two things this script handles for you:
#
#   1. ${TAILNET_DOMAIN} in values.yaml is not expanded by Flux postBuild here,
#      so it is substituted from the flux-system/cluster-vars Secret.
#   2. The Deployment's data-volume claimName was once changed with `kubectl
#      patch`, which handed that field to the "kubectl-patch" field manager.
#      Helm's server-side apply then refuses to touch it and the whole upgrade
#      aborts (this is what silently pinned the release at revision 4 and kept
#      passwordMode=keepUpdated live, resetting the admin password on every pod
#      restart). --force-conflicts takes the field back for Helm.

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VALUES="$REPO_DIR/kubernetes/apps/gitea/values.yaml"
CHART_VERSION="12.6.0"

info() { echo "==> $1"; }

info "Reading TAILNET_DOMAIN from flux-system/cluster-vars..."
TAILNET_DOMAIN="$(kubectl get secret -n flux-system cluster-vars \
  -o jsonpath='{.data.TAILNET_DOMAIN}' | base64 -d)"
[[ -n "$TAILNET_DOMAIN" ]] || { echo "TAILNET_DOMAIN is empty"; exit 1; }

info "Ensuring the admin credential is synced from 1Password..."
kubectl apply -f "$REPO_DIR/kubernetes/apps/gitea/externalsecret.yaml"
kubectl wait --for=condition=Ready --timeout=60s \
  externalsecret/gitea-admin -n gitea

RENDERED="$(mktemp -t gitea-values)"
trap 'rm -f "$RENDERED"' EXIT
TAILNET_DOMAIN="$TAILNET_DOMAIN" envsubst '${TAILNET_DOMAIN}' < "$VALUES" > "$RENDERED"

info "Upgrading Gitea (chart $CHART_VERSION)..."
helm repo add gitea https://dl.gitea.com/charts >/dev/null
helm repo update gitea >/dev/null
helm upgrade --install gitea gitea/gitea \
  --namespace gitea \
  --version "$CHART_VERSION" \
  --values "$RENDERED" \
  --force-conflicts \
  --wait --timeout 10m

info "Verifying the init container did not reset the admin password..."
POD="$(kubectl get pod -n gitea -l app.kubernetes.io/instance=gitea \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}')"
kubectl logs -n gitea "$POD" -c configure-gitea | grep -i 'admin account' || true

info "Done. Gitea is at https://gitea.${TAILNET_DOMAIN}"
