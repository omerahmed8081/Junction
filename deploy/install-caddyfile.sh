#!/usr/bin/env bash
# Install the repo's deploy/Caddyfile as the server's /etc/caddy/Caddyfile, adapted for
# this server, then reload Caddy. Run as root; setup.sh and update.sh call it.
#
# deploy/Caddyfile is written to be run from Backend/ by hand, so it is not used as-is:
#   * its relative "root * ../Frontend/dist" becomes the absolute /opt/tta/Frontend/dist
#     (the Caddy system service does not start in Backend/)
#   * ":80" becomes the hostname in /etc/tta/site, if that file exists (turns on HTTPS)
#   * the login in /etc/tta/basicauth, if that file exists, is put in front of the site
#     (one line: "<user> <hash from caddy hash-password>")
set -euo pipefail
APP=/opt/tta
OUT=/etc/caddy/Caddyfile
TMP=$(mktemp)

site=":80"
[ -s /etc/tta/site ] && site="$(head -n1 /etc/tta/site | tr -d '[:space:]')"

sed -e "s#root \* \.\./Frontend/dist#root * $APP/Frontend/dist#" \
    -e "0,/^:80 {/s##${site} {#" \
    "$APP/deploy/Caddyfile" > "$TMP"

if [ -s /etc/tta/basicauth ]; then
  read -r user hash < /etc/tta/basicauth
  awk -v u="$user" -v h="$hash" '
    { print }
    !done && /^[^#].* \{$/ { print "\tbasic_auth {\n\t\t" u " " h "\n\t}"; done=1 }
  ' "$TMP" > "$TMP.auth" && mv "$TMP.auth" "$TMP"
fi

grep -q "root \* $APP/Frontend/dist" "$TMP" || { echo "deploy/Caddyfile no longer has 'root * ../Frontend/dist'; check it by hand."; exit 1; }
if ! check=$(caddy validate --config "$TMP" --adapter caddyfile 2>&1); then echo "$check"; exit 1; fi
install -m 644 "$TMP" "$OUT"
rm -f "$TMP"
systemctl reload caddy 2>/dev/null || systemctl restart caddy
echo "Caddy serving $site"
