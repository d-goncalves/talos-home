terraform {
  required_version = ">= 1.5.0"

  # ── This state is disposable ──────────────────────────────────────────────
  # Unlike homelab/, nothing irreplaceable lands here: the only resource is the
  # tailnet nameserver list, and losing the state means re-importing or simply
  # re-applying the same value. There is deliberately no `state backup` path
  # for it — see README.md.
  backend "local" {
    path = "terraform.tfstate"
  }

  required_providers {
    tailscale = {
      source  = "tailscale/tailscale"
      version = "~> 0.29"
    }
    onepassword = {
      source  = "1Password/onepassword"
      version = "~> 2.1"
    }
  }
}
