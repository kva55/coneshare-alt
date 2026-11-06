#!/usr/bin/env bash
# coneshare-alt local test stack.
#
#   ./quickstart.sh            build + start everything, migrate, create the admin account
#   ./quickstart.sh test       run backend (pytest), frontend (lint + vitest) and Go tests in the containers
#   ./quickstart.sh check      verify the hand-written migrations match the models (makemigrations --check)
#   ./quickstart.sh encrypt-existing   encrypt files stored before encryption was on
#   ./quickstart.sh logs       follow container logs
#
# Encrypted by default: AES-256-GCM files at rest, TLS to the app, the file service and Redis.
# TLS=off ./quickstart.sh or STORAGE_ENCRYPTION=off ./quickstart.sh are explicit, warned opt-outs.
#   ./quickstart.sh stop       stop the stack (data is kept)
#   ./quickstart.sh reset      stop and delete ALL local data (database, uploaded files, .env)
#
# To remove containers, images, all data and this folder: ./quickclose.sh
#
# This is a development/test setup (Django runserver, DEBUG=True). E-mail is printed in the backend log
# unless you configure your own mail server (Admin > Notifications > E-mail setup, or EMAIL_* in .env).
# Do not expose it to a network you do not trust.
set -euo pipefail

cd "$(dirname "$0")"

ADMIN_EMAIL="${ADMIN_EMAIL:-admin@coneshare.local}"
# ADMIN_PASSWORD: set it to choose one; otherwise a random one is generated on each start and printed at the end.
ORG_NAME="${ORG_NAME:-Default Organization}"
STORAGE_DIR="${HOME}/.coneshare-data"   # mounted by docker-compose.yml into the core service
export COMPOSE_PROJECT_NAME=coneshare
export COMPOSE_FILE="docker-compose.yml"
# TLS is on unless switched off with TLS=off, or (when TLS is not given) the existing .env says it is off.
if [[ -z "${TLS:-}" && -f .env ]] && grep -q '^SITE_DOMAIN=http://' .env; then TLS=off; fi
if [[ "${TLS:-on}" == "off" ]]; then
  export COMPOSE_FILE="${COMPOSE_FILE}:docker-compose.plain.yml"
else
  export COMPOSE_PROFILES=tls   # the HTTPS front door (nginx)
fi

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }

if docker compose version >/dev/null 2>&1; then
  DC=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  DC=(docker-compose)
else
  die "Docker Compose not found. Install Docker Engine with the compose plugin."
fi
docker info >/dev/null 2>&1 || die "Cannot talk to the Docker daemon (is it running? are you in the docker group / using sudo?)."

random_secret() {
  if command -v openssl >/dev/null 2>&1; then openssl rand -hex 32
  else python3 -c 'import secrets; print(secrets.token_hex(32))'; fi
}

ensure_env() {
  if [[ -f .env ]]; then
    log "Using existing .env"
    return
  fi
  log "Creating .env from .env.template with freshly generated secrets"
  cp .env.template .env
  local secret token
  secret="$(random_secret)"
  token="$(random_secret)"
  # Replace the public template defaults; nothing here is reused across installs.
  sed -i.bak \
    -e "s|^SECRET_KEY=.*|SECRET_KEY=${secret}|" \
    -e "s|^INTERNAL_API_TOKEN=.*|INTERNAL_API_TOKEN=${token}|" \
    -e "s|^DEBUG=.*|DEBUG=True|" \
    -e "s|^EMAIL_BACKEND=.*|EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend|" \
    .env
  rm -f .env.bak
}

# Encryption and TLS are on by default. The storage key is created once and then kept: files written
# with it can only be read with it.
ensure_encryption_env() {
  NEW_STORAGE_KEY=0
  local current
  current="$(grep -E '^STORAGE_ENCRYPTION_KEY=' .env | head -1 | cut -d= -f2- || true)"
  if [[ -z "${current}" && "${STORAGE_ENCRYPTION:-required}" != "off" ]]; then
    set_env STORAGE_ENCRYPTION_KEY "$(random_secret)"
    NEW_STORAGE_KEY=1
    warn "Generated STORAGE_ENCRYPTION_KEY in .env. Back it up: without it stored files cannot be read."
  fi
  grep -q '^STORAGE_ENCRYPTION=' .env || set_env STORAGE_ENCRYPTION "${STORAGE_ENCRYPTION:-required}"
  if [[ "${TLS:-on}" == "off" ]]; then
    warn "TLS=off: connections between the parts and to the app are NOT encrypted."
    set_env SITE_DOMAIN "http://localhost:5173"
    set_env REDIS_URL "redis://redis:6379"
    set_env CORE_API_URL "http://core:8080"
    set_env INTERNAL_CA_FILE ""
    set_env AUTH_COOKIE_SECURE False; set_env SESSION_COOKIE_SECURE False; set_env CSRF_COOKIE_SECURE False
    set_env CORE_TLS_CERT_FILE ""; set_env CORE_TLS_KEY_FILE ""
  else
    log "TLS on: making sure certificates exist in ./certs (internal CA)"
    EDGE_HOSTNAMES="${EDGE_HOSTNAMES:-}" ./scripts/gen-certs.sh
    set_env SITE_DOMAIN "https://localhost:${HTTPS_PORT:-8443}"
    set_env REDIS_URL "rediss://redis:6379"
    set_env CORE_API_URL "https://core:8080"
    set_env INTERNAL_CA_FILE "/certs/ca.crt"
    set_env AUTH_COOKIE_SECURE True; set_env SESSION_COOKIE_SECURE True; set_env CSRF_COOKIE_SECURE True
    set_env CORE_TLS_CERT_FILE "/certs/core.crt"; set_env CORE_TLS_KEY_FILE "/certs/core.key"
  fi
}

set_env() {  # set_env KEY VALUE: replace the line in .env or append it
  local key="$1" value="$2"
  if grep -q "^${key}=" .env; then
    sed -i.bak -e "s|^${key}=.*|${key}=${value}|" .env && rm -f .env.bak
  else
    printf '%s=%s\n' "${key}" "${value}" >> .env
  fi
}

wait_for_core() {
  log "Waiting for the file service (core) to finish compiling and listen on :8080..."
  local i
  for i in $(seq 1 90); do
    if "${DC[@]}" exec -T core sh -c 'wget -qO- --no-check-certificate https://127.0.0.1:8080/health || wget -qO- http://127.0.0.1:8080/health' >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  "${DC[@]}" logs --tail=80 core || true
  die "File service (core) did not come up. See the log output above."
}

wait_for_backend() {
  log "Waiting for the backend to answer (first build can take several minutes)..."
  local i state
  for i in $(seq 1 120); do
    if "${DC[@]}" exec -T backend python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/languages/', timeout=3)" >/dev/null 2>&1; then
      return 0
    fi
    # A backend that has stopped or keeps restarting will not come up by waiting: show why now.
    state="$("${DC[@]}" ps --format '{{.Service}} {{.State}}' backend 2>/dev/null | awk '{print $2}')"
    if [[ "${state}" == "exited" || "${state}" == "restarting" || "${state}" == "dead" ]]; then
      "${DC[@]}" logs --tail=120 backend || true
      die "The backend container is ${state}. The error is in the log above."
    fi
    if [[ $i -eq 10 ]]; then
      warn "Still waiting after 30 s. Last backend log lines (a traceback here is the cause):"
      "${DC[@]}" logs --tail=40 backend || true
    fi
    sleep 3
  done
  "${DC[@]}" logs --tail=120 backend || true
  die "Backend did not come up. See the log output above."
}

create_admin() {
  log "Creating/updating admin account ${ADMIN_EMAIL}"
  "${DC[@]}" exec -T \
    -e QS_ADMIN_EMAIL="${ADMIN_EMAIL}" \
    -e QS_ADMIN_PASSWORD="${ADMIN_PASSWORD}" \
    -e QS_ORG_NAME="${ORG_NAME}" \
    backend python manage.py shell -c '
import os
from core.models import Organization, User
email = os.environ["QS_ADMIN_EMAIL"].strip().lower()
org = Organization.objects.order_by("created_at").first()
if org is None:
    org = Organization.objects.create(name=os.environ["QS_ORG_NAME"])
user, created = User.objects.get_or_create(
    email=email,
    defaults={"username": email, "organization": org, "name": "Admin"},
)
user.organization = user.organization or org
user.role = "admin"
user.is_staff = True
user.is_superuser = True
user.is_active = True
user.set_password(os.environ["QS_ADMIN_PASSWORD"])
user.save()
print(("created" if created else "updated"), user.email, "in organization", user.organization.name)
'
}

cmd_up() {
  ensure_env
  ensure_encryption_env
  ADMIN_PASSWORD="${ADMIN_PASSWORD:-$(random_secret | cut -c1-20)}"
  mkdir -p "${STORAGE_DIR}"
  log "Building and starting containers"
  "${DC[@]}" up -d --build
  wait_for_core
  # Migrate first: on a fresh database the backend cannot answer requests until its tables exist, so
  # waiting for it before migrating would wait forever. `exec` starts its own process, so it works even
  # while the web server is still returning errors.
  log "Applying database migrations"
  local tries
  for tries in 1 2 3 4 5 6 7 8 9 10; do
    "${DC[@]}" exec -T backend python manage.py migrate --noinput && break
    [[ $tries -eq 10 ]] && { "${DC[@]}" logs --tail=80 backend || true; die "Migrations failed. The error is above."; }
    sleep 3
  done
  wait_for_backend
  log "Checking that the backend can reach the file service"
  # Through Django, so the internal CA that settings.py trusts is used exactly as the application uses it.
  "${DC[@]}" exec -T backend python manage.py shell -c "
import requests
from django.conf import settings
r = requests.get(settings.CORE_API_URL.rstrip('/') + '/health', timeout=5)
r.raise_for_status()
print('file service reachable at', settings.CORE_API_URL)
" \
    || die "Backend cannot reach the file service (CORE_API_URL, with TLS verified against the internal CA). Check: ./quickstart.sh logs"
  if [[ "${NEW_STORAGE_KEY:-0}" == 1 ]] && find "${STORAGE_DIR}" -type f 2>/dev/null | grep -q .; then
    log "Encrypting files that were stored before encryption was switched on"
    "${DC[@]}" exec -T core go run . -encrypt-existing || warn "Converting existing files failed; they are still served as they were. Re-run: ./quickstart.sh encrypt-existing"
  fi
  create_admin
  local APP_URL="https://localhost:${HTTPS_PORT:-8443}"
  [[ "${TLS:-on}" == "off" ]] && APP_URL="http://localhost:5173 (TLS is OFF)"
  cat <<EOF

  coneshare-alt is running.

    App:       ${APP_URL}   (HTTPS; import certs/ca.crt into your browser to trust it)
    API:       http://localhost:8000/api/v1/   (plain HTTP, localhost only; use the app URL)
    Swagger:   http://localhost:8000/api/schema/swagger/   (DEBUG only)

    Encryption: files at rest AES-256-GCM (key in .env: back it up), TLS at the front door, to the
    file service and to Redis. Details: docs/fork/container-security.md

    Admin account
      ${ADMIN_EMAIL}   password: ${ADMIN_PASSWORD}   (shown once: set ADMIN_PASSWORD yourself to choose it)

  E-mail (access codes, sign-in links, approval requests) is printed in the backend log until you
  configure a mail server:   ./quickstart.sh logs
  Your own mail server: Admin > Notifications > E-mail setup, or EMAIL_* in .env.

  Other commands: ./quickstart.sh test | check | stop | reset
  Remove everything (containers, images, data, this folder): ./quickclose.sh
EOF
}

cmd_test() {
  "${DC[@]}" exec -T backend true >/dev/null 2>&1 || die "Stack is not running. Run ./quickstart.sh first."
  local rc=0
  log "Backend tests (pytest)"
  "${DC[@]}" exec -T backend pytest -q || rc=1
  log "Frontend lint"
  "${DC[@]}" exec -T frontend npm run lint || rc=1
  log "Frontend tests (whitelisted vitest suite)"
  "${DC[@]}" exec -T frontend npm run test:whitelist || rc=1
  log "Go core tests"
  "${DC[@]}" exec -T core go test ./... || rc=1
  if [[ $rc -eq 0 ]]; then log "All test suites passed."; else warn "Some test suites failed (see above)."; fi
  return $rc
}

cmd_check() {
  "${DC[@]}" exec -T backend true >/dev/null 2>&1 || die "Stack is not running. Run ./quickstart.sh first."
  log "Checking that migrations match the models"
  if "${DC[@]}" exec -T backend python manage.py makemigrations --check --dry-run; then
    log "Migrations are in sync."
  else
    warn "Model changes without migrations detected (output above)."
    return 1
  fi
}

cmd_reset() {
  read -r -p "This deletes the local database, uploaded files in ${STORAGE_DIR} and .env. Continue? [y/N] " ans
  [[ "${ans}" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }
  "${DC[@]}" down -v || true
  rm -f backend/db.sqlite3 .env
  rm -rf certs
  rm -rf "${STORAGE_DIR}"
  log "Reset complete."
}

case "${1:-up}" in
  up|start) cmd_up ;;
  test)     cmd_test ;;
  check)    cmd_check ;;
  encrypt-existing) "${DC[@]}" exec -T core go run . -encrypt-existing ;;
  logs)     "${DC[@]}" logs -f ;;
  stop)     "${DC[@]}" stop ;;
  reset)    cmd_reset ;;
  *)        sed -n '2,14p' "$0"; exit 1 ;;
esac
