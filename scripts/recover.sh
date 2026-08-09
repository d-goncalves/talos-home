#!/usr/bin/env bash
#
# Bare-machine bootstrap.
#
# This does the only two things ./provision cannot do for itself: put the
# required tools on a machine that has none, and get the repo onto it. Once
# those exist, `./provision up` owns the rest — provisioning the node, writing
# kubeconfig/talosconfig, seeding cluster secrets and handing off to Flux.
#
#     bash <(curl -s https://raw.githubusercontent.com/d-goncalves/talos-home/master/scripts/recover.sh)
#
# It is deliberately standalone: no repo-relative paths, no 1Password lookups,
# nothing that assumes the cluster exists. That matters because the usual
# source of this script is Gitea, which runs on the cluster it is recovering.
#
# Previous versions of this script also generated Talos machine-config patches
# and seeded Kubernetes secrets. Both now live in terraform/ and the CLI:
# patches are rendered from provision.toml, and credentials are read straight
# from 1Password by Terraform, so nothing is templated onto disk any more.

set -euo pipefail

GITHUB_REPO="https://github.com/d-goncalves/talos-home.git"
REPO_DIR="${REPO_DIR:-$HOME/talos}"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()    { echo -e "${GREEN}==>${NC} $1"; }
warn()    { echo -e "${YELLOW}==>${NC} $1"; }
success() { echo -e "${GREEN}✓${NC} $1"; }

# ── Git identity & signing ────────────────────────────────────────────────────
info "Configuring git identity and commit signing..."
git config --global user.name  "Diogo Gonçalves"
git config --global user.email "124159934+d-goncalves@users.noreply.github.com"
git config --global commit.gpgsign  true
git config --global tag.gpgsign     true
git config --global gpg.format      ssh
git config --global gpg.ssh.program "/Applications/1Password.app/Contents/MacOS/op-ssh-sign"
# user.signingkey is set by 1Password — Settings → Developer → SSH Agent.

# ── Tools ─────────────────────────────────────────────────────────────────────
# python3 and git are needed for ./provision itself; the rest are what the CLI
# shells out to.
REQUIRED=(git python3 talosctl kubectl flux helm op)

install_macos() {
  if ! command -v brew &>/dev/null; then
    info "Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  fi
  info "Installing missing tools via Homebrew..."
  brew install git talosctl kubectl fluxcd/tap/flux helm 1password-cli
}

install_linux() {
  local BIN_DIR="/usr/local/bin"

  info "Installing talosctl..."
  local TALOS_VERSION
  TALOS_VERSION=$(curl -s https://api.github.com/repos/siderolabs/talos/releases/latest | grep tag_name | cut -d'"' -f4)
  curl -sL "https://github.com/siderolabs/talos/releases/download/${TALOS_VERSION}/talosctl-linux-amd64" -o /tmp/talosctl
  chmod +x /tmp/talosctl && sudo mv /tmp/talosctl "$BIN_DIR/talosctl"

  info "Installing kubectl..."
  local KUBECTL_VERSION
  KUBECTL_VERSION=$(curl -sL https://dl.k8s.io/release/stable.txt)
  curl -sL "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" -o /tmp/kubectl
  chmod +x /tmp/kubectl && sudo mv /tmp/kubectl "$BIN_DIR/kubectl"

  info "Installing flux..."
  curl -s https://fluxcd.io/install.sh | sudo bash

  info "Installing helm..."
  curl -s https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash

  warn "Install the 1Password CLI manually: https://developer.1password.com/docs/cli/get-started/"
}

info "Checking tools..."
MISSING=()
for cmd in "${REQUIRED[@]}"; do
  command -v "$cmd" &>/dev/null || MISSING+=("$cmd")
done

if [[ ${#MISSING[@]} -gt 0 ]]; then
  warn "Missing: ${MISSING[*]}"
  case "$(uname -s)" in
    Darwin) install_macos ;;
    Linux)  install_linux ;;
    *) echo -e "${RED}Unsupported OS: $(uname -s)${NC}"; exit 1 ;;
  esac
else
  success "All tools already installed"
fi

# ── Repo ──────────────────────────────────────────────────────────────────────
# Always GitHub: Gitea runs on the cluster, so it cannot be relied on here.
if [[ -d "$REPO_DIR/.git" ]]; then
  warn "Repo already exists at $REPO_DIR, skipping clone"
else
  info "Cloning from the GitHub mirror..."
  git clone "$GITHUB_REPO" "$REPO_DIR"
  success "Repo cloned to $REPO_DIR"
fi

# ── Hand off ──────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "  ${GREEN}Bootstrap complete.${NC}"
echo ""
echo "  Next:"
echo "    cd $REPO_DIR"
echo "    \$EDITOR provision.toml       # fill in the <placeholder> values"
echo "    export OP_ACCOUNT=my.1password.com"
echo "    ./provision up"
echo ""
echo "  ./provision creates its own .venv on first run — nothing to install."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
