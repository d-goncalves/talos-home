terraform {
  # Validated against Terraform 1.5.7 (the version currently on this machine)
  # and compatible with OpenTofu 1.6+. Nothing here needs a newer core.
  required_version = ">= 1.5.0"

  # ── State holds secrets, by design ────────────────────────────────────────
  # talos_machine_secrets puts the cluster CA private key, the bootstrap token
  # and the secretbox encryption key into state. The Tailscale auth key and
  # everything read from 1Password land there too.
  #
  # That is accepted: state is gitignored and backed up with
  # `provision state backup`. Nothing secret is in any committed file, which is
  # what makes this repo safe to publish.
  backend "local" {
    path = "terraform.tfstate"
  }

  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = "~> 0.111"
    }
    talos = {
      source  = "siderolabs/talos"
      version = "~> 0.11"
    }
    tailscale = {
      source  = "tailscale/tailscale"
      version = "~> 0.29"
    }
    unifi = {
      source  = "ubiquiti-community/unifi"
      version = "~> 0.55"
    }
    onepassword = {
      source  = "1Password/onepassword"
      version = "~> 2.1"
    }
    healthchecksio = {
      source  = "kristofferahl/healthchecksio"
      version = "~> 2.3"
    }
  }
}
