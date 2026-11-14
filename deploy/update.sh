#!/usr/bin/env bash
# Bring the server up to date with the repository. Run on the server:
#     sudo -u tta bash /opt/tta/deploy/update.sh            # pull, install, build
#     sudo systemctl restart tta-kitchen tta-gateway        # then restart the services
# or, from a root shell, simply:
#     bash /opt/tta/deploy/update.sh
# Flags: --no-pull (use the code as it is), --no-restart (skip the restart step)
set -euo pipefail

APP=/opt/tta
PULL=1; RESTART=1
for arg in "$@"; do
  case "$arg" in --no-pull) PULL=0 ;; --no-restart) RESTART=0 ;; esac
done

# Everything except the restart runs as the service user, so files stay owned by it.
if [ "$(id -u)" = "0" ]; then
  sudo -u tta bash "$APP/deploy/update.sh" --no-restart $([ $PULL = 0 ] && echo --no-pull)
  if [ $RESTART = 1 ]; then
    cp "$APP/deploy/tta-gateway.service" "$APP/deploy/tta-kitchen.service" /etc/systemd/system/
    systemctl daemon-reload
    systemctl restart tta-kitchen tta-gateway
    bash "$APP/deploy/install-caddyfile.sh"
    sleep 3
    curl -fsS http://127.0.0.1:8002/health && echo
    curl -fsS http://127.0.0.1:8001/health && echo
  fi
  exit 0
fi

cd "$APP"
if [ $PULL = 1 ]; then
  echo "== git pull"
  git pull --ff-only
fi
git log -1 --format='now at %h  %s  (%cr)'

echo "== backend"
cd "$APP/Backend"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt

echo "== frontend"
cd "$APP/Frontend"
npm ci --silent
npm run build

if [ $RESTART = 1 ]; then
  echo "Now restart the services:  sudo systemctl restart tta-kitchen tta-gateway"
fi
