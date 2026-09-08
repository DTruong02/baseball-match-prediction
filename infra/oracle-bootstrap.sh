#!/usr/bin/env bash
# Bootstrap an Oracle Cloud Always Free VM for the baseball-chatbot Compose stack.
# Run as a user with sudo (Ubuntu or Oracle Linux). Idempotent where practical.
set -euo pipefail

REPO_URL="${REPO_URL:-}"
DEPLOY_PATH="${DEPLOY_PATH:-$HOME/baseball-chatbot}"
INSTALL_DOCKER="${INSTALL_DOCKER:-1}"

echo "==> Oracle Always Free bootstrap"

if [[ "${INSTALL_DOCKER}" == "1" ]]; then
  if command -v docker >/dev/null 2>&1; then
    echo "==> Docker already installed: $(docker --version)"
  else
    echo "==> Installing Docker Engine + Compose plugin"
    if [[ -f /etc/os-release ]]; then
      # shellcheck source=/dev/null
      . /etc/os-release
    else
      echo "ERROR: /etc/os-release missing; install Docker manually." >&2
      exit 1
    fi

    case "${ID:-}" in
      ubuntu|debian)
        sudo apt-get update -y
        sudo apt-get install -y ca-certificates curl gnupg
        sudo install -m 0755 -d /etc/apt/keyrings
        if [[ ! -f /etc/apt/keyrings/docker.asc ]]; then
          sudo curl -fsSL https://download.docker.com/linux/${ID}/gpg -o /etc/apt/keyrings/docker.asc
          sudo chmod a+r /etc/apt/keyrings/docker.asc
        fi
        echo \
          "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/${ID} ${VERSION_CODENAME} stable" \
          | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
        sudo apt-get update -y
        sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
        ;;
      ol|oracle|centos|rhel|fedora)
        sudo dnf -y install dnf-plugins-core
        sudo dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
        sudo dnf -y install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
        sudo systemctl enable --now docker
        ;;
      *)
        echo "ERROR: Unsupported distro '${ID:-unknown}'. Install Docker manually, then re-run." >&2
        exit 1
        ;;
    esac
  fi

  if ! groups | grep -qw docker; then
    echo "==> Adding ${USER} to the docker group (log out/in afterward)"
    sudo usermod -aG docker "$USER" || true
  fi

  sudo systemctl enable --now docker 2>/dev/null || true
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "ERROR: 'docker compose' plugin not available." >&2
  exit 1
fi

if [[ -n "${REPO_URL}" ]]; then
  if [[ -d "${DEPLOY_PATH}/.git" ]]; then
    echo "==> Repo already present at ${DEPLOY_PATH}"
  else
    echo "==> Cloning ${REPO_URL} → ${DEPLOY_PATH}"
    git clone "${REPO_URL}" "${DEPLOY_PATH}"
  fi
fi

if [[ -d "${DEPLOY_PATH}" ]]; then
  echo "==> Next steps in ${DEPLOY_PATH}:"
  echo "    1. cp .env.example .env && edit secrets (see infra/HOSTING.md)"
  echo "    2. Set COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml in .env"
  echo "    3. docker compose up -d --build"
  echo "    4. Open OCI Security List + host firewall for 22/80/443"
else
  echo "==> Clone the repo (REPO_URL=... $0) or copy it to ${DEPLOY_PATH}, then see infra/HOSTING.md"
fi

echo "==> Bootstrap complete"
