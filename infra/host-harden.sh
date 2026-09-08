#!/usr/bin/env bash
# Apply host firewall (SSH/HTTP/HTTPS only) and unattended security updates.
# Idempotent. Safe to re-run after oracle-bootstrap.sh.
set -euo pipefail

INSTALL_UPDATES="${INSTALL_UPDATES:-1}"
CONFIGURE_FIREWALL="${CONFIGURE_FIREWALL:-1}"

if [[ -f /etc/os-release ]]; then
  # shellcheck source=/dev/null
  . /etc/os-release
else
  echo "ERROR: /etc/os-release missing" >&2
  exit 1
fi

echo "==> Host hardening (${ID:-unknown})"

if [[ "${CONFIGURE_FIREWALL}" == "1" ]]; then
  case "${ID:-}" in
    ubuntu|debian)
      if command -v ufw >/dev/null 2>&1 || sudo apt-get install -y ufw; then
        echo "==> Configuring ufw (OpenSSH + 80/443)"
        sudo ufw allow OpenSSH
        sudo ufw allow 80/tcp
        sudo ufw allow 443/tcp
        # Non-interactive enable; ignore "already enabled"
        echo "y" | sudo ufw enable || true
        sudo ufw status verbose || true
      fi
      ;;
    ol|oracle|centos|rhel|fedora)
      if command -v firewall-cmd >/dev/null 2>&1 || sudo dnf -y install firewalld; then
        echo "==> Configuring firewalld (http/https; ssh usually already open)"
        sudo systemctl enable --now firewalld
        sudo firewall-cmd --permanent --add-service=ssh || true
        sudo firewall-cmd --permanent --add-service=http
        sudo firewall-cmd --permanent --add-service=https
        sudo firewall-cmd --reload
        sudo firewall-cmd --list-all || true
      fi
      ;;
    *)
      echo "WARN: Unknown distro; configure firewall manually (see infra/HOSTING.md)." >&2
      ;;
  esac
  echo "==> Do not open 5432/6379/8000/3000 publicly (prod Compose keeps them internal)."
fi

if [[ "${INSTALL_UPDATES}" == "1" ]]; then
  case "${ID:-}" in
    ubuntu|debian)
      echo "==> Installing unattended-upgrades"
      sudo apt-get update -y
      sudo DEBIAN_FRONTEND=noninteractive apt-get install -y unattended-upgrades apt-listchanges
      sudo dpkg-reconfigure -f noninteractive unattended-upgrades || true
      # Security updates only; reboot manually when needed.
      sudo tee /etc/apt/apt.conf.d/20auto-upgrades >/dev/null <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF
      ;;
    ol|oracle|centos|rhel|fedora)
      echo "==> Installing dnf-automatic (security upgrades)"
      sudo dnf -y install dnf-automatic
      if [[ -f /etc/dnf/automatic.conf ]]; then
        sudo sed -i 's/^upgrade_type = .*/upgrade_type = security/' /etc/dnf/automatic.conf || true
        sudo sed -i 's/^apply_updates = .*/apply_updates = yes/' /etc/dnf/automatic.conf || true
      fi
      sudo systemctl enable --now dnf-automatic.timer || sudo systemctl enable --now dnf-automatic-install.timer || true
      ;;
    *)
      echo "WARN: Enable unattended security updates manually for this distro." >&2
      ;;
  esac
fi

echo "==> Hardening complete"
