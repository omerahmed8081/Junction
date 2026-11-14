#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu 24.04 server. Run as root, from the cloned repo:
#     sudo bash /opt/tta/deploy/setup.sh
# Safe to run again: every step checks before it changes anything.
set -euo pipefail

APP=/opt/tta
[ -f "$APP/Backend/requirements.txt" ] || { echo "Clone the repository to $APP first."; exit 1; }
[ -f "$APP/Backend/.env" ] || { echo "Copy your .env to $APP/Backend/.env first."; exit 1; }

echo "== packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip git curl ca-certificates debian-keyring debian-archive-keyring apt-transport-https gnupg

if ! command -v node >/dev/null || [ "$(node -v | cut -c2- | cut -d. -f1)" -lt 20 ]; then
  echo "== Node 22"
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
  apt-get install -y nodejs
fi

if ! command -v caddy >/dev/null; then
  echo "== Caddy"
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -y
  apt-get install -y caddy
fi

echo "== user and permissions"
id tta >/dev/null 2>&1 || useradd --system --create-home --shell /bin/bash tta
chown -R tta:tta "$APP"
chmod 600 "$APP/Backend/.env"                 # keys: readable by the service user only
git config --system --add safe.directory "$APP" || true

echo "== .env: Unix line endings (a trailing \\r corrupts every value)"
sed -i 's/\r$//' "$APP/Backend/.env"

echo "== backend and frontend"
sudo -u tta bash "$APP/deploy/update.sh" --no-restart --no-pull

echo "== services"
cp "$APP/deploy/tta-gateway.service" "$APP/deploy/tta-kitchen.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now tta-kitchen tta-gateway
systemctl enable --now caddy
bash "$APP/deploy/install-caddyfile.sh"

sleep 4
echo "== check"
curl -fsS http://127.0.0.1:8002/health && echo
curl -fsS http://127.0.0.1:8001/health && echo
curl -fsS -o /dev/null -w "frontend: HTTP %{http_code}\n" -H "Host: localhost" http://127.0.0.1/ || true
echo "Done. Open http://$(curl -fsS -4 ifconfig.me 2>/dev/null || echo '<server ip>')/"
