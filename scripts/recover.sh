#!/usr/bin/env bash
set -euo pipefail

TALOS_ENDPOINT="NODE_IP_PLACEHOLDER"
GITEA_HOST="gitea.<tailnet>.ts.net"
GITEA_REPO="https://${GITEA_HOST}/admin/talos-home.git"
GITHUB_REPO="https://github.com/d-goncalves/talos-home.git"
REPO_DIR="$HOME/talos"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()    { echo -e "${GREEN}==>${NC} $1"; }
warn()    { echo -e "${YELLOW}==>${NC} $1"; }
success() { echo -e "${GREEN}✓${NC} $1"; }

OS=$(uname -s)
ARCH=$(uname -m)

install_tools_macos() {
  if ! command -v brew &>/dev/null; then
    info "Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  fi
  info "Installing talosctl, kubectl, flux..."
  brew install talosctl kubectl fluxcd/tap/flux git
}

install_tools_linux() {
  local BIN_DIR="/usr/local/bin"

  info "Installing talosctl..."
  TALOS_VERSION=$(curl -s https://api.github.com/repos/siderolabs/talos/releases/latest | grep tag_name | cut -d'"' -f4)
  curl -sL "https://github.com/siderolabs/talos/releases/download/${TALOS_VERSION}/talosctl-linux-amd64" -o /tmp/talosctl
  chmod +x /tmp/talosctl && sudo mv /tmp/talosctl "$BIN_DIR/talosctl"

  info "Installing kubectl..."
  KUBECTL_VERSION=$(curl -sL https://dl.k8s.io/release/stable.txt)
  curl -sL "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" -o /tmp/kubectl
  chmod +x /tmp/kubectl && sudo mv /tmp/kubectl "$BIN_DIR/kubectl"

  info "Installing flux..."
  curl -s https://fluxcd.io/install.sh | sudo bash
}

# ── Install tools ─────────────────────────────────────────────────────────────
info "Checking tools..."
MISSING=()
for cmd in talosctl kubectl flux git; do
  command -v "$cmd" &>/dev/null || MISSING+=("$cmd")
done

if [[ ${#MISSING[@]} -gt 0 ]]; then
  warn "Missing: ${MISSING[*]}"
  if [[ "$OS" == "Darwin" ]]; then
    install_tools_macos
  elif [[ "$OS" == "Linux" ]]; then
    install_tools_linux
  else
    echo -e "${RED}Unsupported OS: $OS${NC}"
    exit 1
  fi
else
  success "All tools already installed"
fi

# ── Restore talosconfig ───────────────────────────────────────────────────────
mkdir -p ~/.talos
if [[ -f ~/.talos/config ]]; then
  warn "~/.talos/config already exists, skipping (delete it first to overwrite)"
else
  info "Fetching talosconfig from 1Password..."
  op document get "Talos - talosconfig" --vault "Server Infrastructure" > ~/.talos/config
  success "talosconfig saved"
fi

talosctl config endpoint "$TALOS_ENDPOINT"
talosctl config node "$TALOS_ENDPOINT"

# ── Fetch kubeconfig ──────────────────────────────────────────────────────────
info "Fetching kubeconfig from node..."
talosctl kubeconfig --force
success "kubeconfig saved to ~/.kube/config"

# ── Verify cluster access ─────────────────────────────────────────────────────
info "Verifying cluster access..."
kubectl get nodes
echo ""

# ── Bootstrap External Secrets (1Password service account token) ──────────────
info "Bootstrapping External Secrets Operator token..."
if kubectl get secret onepassword-service-account-token -n external-secrets &>/dev/null; then
  warn "onepassword-service-account-token already exists, skipping"
else
  ESO_TOKEN=$(op item get "1Password Service Account - talos-home" --vault "Server Infrastructure" --fields token --reveal 2>/dev/null || true)
  if [[ -z "$ESO_TOKEN" ]]; then
    warn "Could not fetch ESO token from 1Password — create it manually:"
    warn "  kubectl create namespace external-secrets"
    warn "  kubectl create secret generic onepassword-service-account-token \\"
    warn "    --from-literal=token=<token> -n external-secrets"
  else
    kubectl create namespace external-secrets --dry-run=client -o yaml | kubectl apply -f -
    kubectl create secret generic onepassword-service-account-token \
      --from-literal=token="$ESO_TOKEN" \
      --namespace external-secrets
    success "ESO token secret created"
  fi
fi

# ── Bootstrap cluster-vars (Flux variable substitution) ───────────────────────
info "Bootstrapping cluster-vars secret..."
if kubectl get secret cluster-vars -n flux-system &>/dev/null; then
  warn "cluster-vars already exists, skipping"
else
  kubectl create namespace flux-system --dry-run=client -o yaml | kubectl apply -f -
  kubectl create secret generic cluster-vars \
    --from-literal=TAILNET_DOMAIN="${GITEA_HOST#gitea.}" \
    --namespace flux-system
  success "cluster-vars secret created (TAILNET_DOMAIN=${GITEA_HOST#gitea.})"
fi

# ── Clone repo ────────────────────────────────────────────────────────────────
if [[ -d "$REPO_DIR" ]]; then
  warn "Repo already exists at $REPO_DIR, skipping clone"
else
  if curl -sf --max-time 5 "https://${GITEA_HOST}" > /dev/null 2>&1; then
    info "Gitea is reachable — cloning from Gitea..."
    CLONE_URL="$GITEA_REPO"
  else
    warn "Gitea unreachable — falling back to GitHub mirror..."
    CLONE_URL="$GITHUB_REPO"
  fi
  git clone "$CLONE_URL" "$REPO_DIR"
  success "Repo cloned to $REPO_DIR"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "  ${GREEN}Recovery complete!${NC}"
echo ""
echo "  Repo  : $REPO_DIR"
echo "  Flux  : $(kubectl get kustomization -n flux-system -o wide 2>/dev/null | tail -n +2 || echo 'run: kubectl get kustomization -n flux-system')"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
