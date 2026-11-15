# Deploying to a Verda instance

One small Ubuntu server runs everything: the gateway, the kitchen service, and Caddy, which serves the built frontend and forwards `/api` and `/gateway` to the two services. The database stays on Aiven.

```
browser ──► Caddy :80 ──┬─► /api/*      kitchen service  127.0.0.1:8002
                        ├─► /gateway/*  gateway          127.0.0.1:8001 ──► Aimo API, Aiven Postgres
                        └─► everything else: Frontend/dist (static files)
```

Files in `deploy/`:

| File | Purpose |
|---|---|
| `Caddyfile`, `nginx.conf` | The single front door (written by the team; `README.md` → Hosting) |
| `setup.sh` | First install on a fresh server |
| `update.sh` | Every later update: pull, install, build, restart |
| `install-caddyfile.sh` | Installs `Caddyfile` on the server: absolute frontend path, optional hostname and password |
| `tta-gateway.service`, `tta-kitchen.service` | Keep the two services running and restart them if they crash |

Tested before writing this: the Caddy config, with the built frontend and both services behind it, placed an order end to end. Not tested: the scripts on a real Verda instance, and the Aimo API from a cloud address. Step 4 checks the second one before you invest any time.

## Before you start

On your laptop, commit and push `Frontend/` and `deploy/` so the server can clone them.

## First deployment

### 1. Add your SSH key to Verda

```bash
ls ~/.ssh/id_ed25519.pub || ssh-keygen -t ed25519      # create one if you have none
cat ~/.ssh/id_ed25519.pub                              # copy the whole line
```

In the Verda console: **Keys → SSH Keys → Create**, paste the line.

### 2. Create the instance

**Instances → Deploy Instance**:

- Type: **CPU**, the smallest one offered (2 to 4 cores and 8 GB of memory is plenty).
- Image: **Ubuntu 24.04** (it ships Python 3.12; the backend needs 3.11 or newer).
- Storage: the default system disk is enough (about 2 GB is used).
- SSH key: the one from step 1.

Deploy, wait until it is running, and copy its **IP address**.

### 3. Log in

```bash
ssh root@YOUR_SERVER_IP
```

### 4. Check that Aimo answers from this server (do this first)

The mock API sits behind Cloudflare and has never been called from a cloud host.

```bash
curl -sS -o /dev/null -w "%{http_code}\n" -A "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36" https://junction26.valioaimo.fi/api/ping
```

- `200`: carry on.
- `403`, a reset or a timeout: Cloudflare is blocking this address. Stop here and tell the team; a different instance location, or asking Valio to allow the address, are the options. Nothing below fixes this.

### 5. Get the code

```bash
git clone YOUR_REPO_URL /opt/tta
```

If the repository is private, GitHub asks for a user name and a **personal access token** as the password (GitHub → Settings → Developer settings → Fine-grained tokens → read-only access to this repository). To avoid typing it on every update: `git config --global credential.helper store` before the clone.

### 6. Copy the secrets (from your laptop, in a second terminal)

```bash
scp Backend/.env root@YOUR_SERVER_IP:/opt/tta/Backend/.env
```

The `.env` is never committed, so it travels this way. The 87 MB product file is **not** needed on the server: the catalogue is already in Aiven.

### 7. Run the setup

Back on the server:

```bash
bash /opt/tta/deploy/setup.sh
```

It installs Python tooling, Node 22 and Caddy, creates a `tta` service user, converts `.env` to Unix line endings, installs the backend, builds the frontend, and starts three services. (`KITCHEN_JWKS` now defaults to the kitchen on the same host, so it no longer needs setting.) It ends with two `{"status":"ok"...}` lines and `frontend: HTTP 200`. It takes a few minutes.

### 8. Check it, then open it

On the server, the team's smoke test walks the whole path from a sentence to a placed order and names anything broken:

```bash
cd /opt/tta/Backend && sudo -u tta .venv/bin/python scripts/smoke_test.py --kitchen http://127.0.0.1:8002 --gateway http://127.0.0.1:8001 --reset
```

Exit code 0 means it works. Then open `http://YOUR_SERVER_IP/` and place one order end to end. Then open `/demo` and reset, so the weekly limit starts clean.

If the page does not load from your laptop but step 7 ended well, port 80 is closed somewhere. Check `ufw status` on the server and any firewall setting on the instance in the Verda console.

## Updating later

Push to GitHub, then on the server:

```bash
ssh root@YOUR_SERVER_IP
bash /opt/tta/deploy/update.sh
```

That pulls, installs any new Python packages, rebuilds the frontend, reinstalls the service files and the Caddyfile, restarts both services and prints their health. About a minute. Baskets, orders, the mandate and the audit logs all survive, because they are all in Postgres now; so do the signing keys (`Backend/.keys/`, the one thing still on disk, because it is this kitchen's identity rather than its data).

| What changed | What to do |
|---|---|
| Code (backend or frontend) | `bash /opt/tta/deploy/update.sh` |
| A new table in `sql/audit.sql` | `python scripts/apply_sql.py` from your laptop, then `update.sh`. `--check` first lists what exists |
| A value in `.env` | edit `/opt/tta/Backend/.env`, then `systemctl restart tta-kitchen tta-gateway` |
| Clearing the demo while the server is up | `python scripts/clean_slate.py --yes --kitchen http://YOUR_SERVER_IP --gateway http://YOUR_SERVER_IP`. Without those it resets localhost, and the deployed kitchen writes its memory back |
| Before pushing anything | `python -m pytest tests/ -q`, then `python scripts/end_to_end.py` and `python scripts/check_i18n.py`. All three are offline and take seconds |
| Cut-offs have passed and nothing can be ordered | check with `python scripts/cutoff_check.py --gateway http://YOUR_SERVER_IP/gateway`; if they are fixed and past, set `AIMO_CUTOFF_NOW` in `.env` and restart the gateway |
| `deploy/Caddyfile` or a `.service` file | `bash /opt/tta/deploy/update.sh` (it reinstalls both) |
| The catalogue or labels | run the loader from your laptop against Aiven, as before; nothing on the server |
| You edited a file on the server by hand | `update.sh` refuses to pull; `cd /opt/tta && git stash` first |

## Looking at what it is doing

```bash
systemctl status tta-kitchen tta-gateway caddy     # are they running
journalctl -u tta-kitchen -f                       # live log (also tta-gateway, caddy)
journalctl -u tta-gateway --since "10 min ago"
curl -s localhost:8002/health; curl -s localhost:8001/health
```

A service that crashes is restarted after two seconds.

## HTTPS (optional, 2 minutes)

Without a domain, use your IP with dashes plus `.sslip.io`:

```bash
mkdir -p /etc/tta
echo "203-0-113-10.sslip.io" > /etc/tta/site          # your IP, dashes instead of dots
bash /opt/tta/deploy/install-caddyfile.sh
```

Caddy fetches a certificate by itself; ports 80 and 443 must both be reachable. The address becomes `https://203-0-113-10.sslip.io/`, and later updates keep it.

## A password on the site (recommended before sharing)

```bash
caddy hash-password                                   # type a password, copy the hash it prints
mkdir -p /etc/tta
echo "team PASTE_THE_HASH" > /etc/tta/basicauth
bash /opt/tta/deploy/install-caddyfile.sh
```

The browser then asks for user `team` and that password. Remove the file and run the installer again to take it off. The second-client script for demo beat 5 would need the same login, or should call the gateway on the server itself.

## Things to know before sharing the address

- **The site has no login unless you add one** (section above). Without it, anyone with the address can use your Gemini quota, call Aimo with your key, and press reset on `/demo`.
- **Aiven's free plan powers off when idle.** Open the Aiven console before a demo and check the service is running.
- **The clock matters.** The order cut-off for the demo product is Sunday 17:00; after that, beat 1 shows a cut-off exception instead.
- **Reset before every rehearsal** (`/demo`), or the EUR 5,000 weekly mandate limit fills up after a few runs.
- **Delete the instance after the event.** Check Verda's "Shutdown, Hibernate, and Delete" page for what a shut-down instance still costs.

## If something fails

| Symptom | Likely cause |
|---|---|
| `setup.sh` stops at "Copy your .env" | step 6 was skipped |
| Kitchen health fails, log shows a database error | Aiven is powered off, or `DATABASE_URL` is wrong |
| Chat answers "Aimo did not answer" | step 4: Aimo is not reachable from this address |
| Approve is refused with `mandate_invalid` | `KITCHEN_JWKS` missing from `.env`, or the gateway started before the kitchen: `systemctl restart tta-gateway` |
| Page loads but every call fails | `systemctl status tta-kitchen` and its log |
| `/approvals` gives 404 on refresh, or a blank page | the Caddyfile was copied by hand: run `bash /opt/tta/deploy/install-caddyfile.sh` |
| `.env` values look right but the API key is rejected | Windows line endings; `setup.sh` strips them, or run `sed -i 's/\r$//' /opt/tta/Backend/.env` |
