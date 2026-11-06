#!/usr/bin/env bash
# coneshare-alt local test stack.
#
#   ./quickstart.sh            normal start: build + start everything, migrate; the first time it asks for the
#                              admin e-mail address and shows a generated password once (change it at first sign-in)
#   ./quickstart.sh reset-admin   ask for an admin address again and set a new generated password
#   ./quickstart.sh testmail-demo add a demo mail server and webmail with admin@lab.local, user@lab.local and
#                              guest@lab.local (see testmail-demo.sh); optional, for trying e-mail features
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
# unless you configure your own mail server (Admin > Notifications > E-mail setup, or EMAIL_* in .env)
# or start the demo mail server (./quickstart.sh testmail-demo).
# Do not expose it to a network you do not trust.
set -euo pipefail

cd "$(dirname "$0")"

ORG_NAME="${ORG_NAME:-Default Organization}"
STORAGE_DIR="${HOME}/.coneshare-data"   # mounted by docker-compose.yml into the core service
# log/warn/die, compose and TLS detection, random_secret, gen_password, create_app_user
. scripts/common.sh

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

admin_exists() {
  [[ "$("${DC[@]}" exec -T backend python manage.py shell -c "from core.models import User; print(User.objects.filter(role='admin', is_active=True).count())" 2>/dev/null | tail -1 | tr -d '[:space:]')" -gt 0 ]] 2>/dev/null
}

# Ask who the administrator is, make them an account with a generated password, and show it once.
create_admin() {
  local email="${ADMIN_EMAIL:-}"
  if [[ -z "${email}" ]]; then
    [[ -t 0 ]] || die "No admin address: run this from a terminal, or set ADMIN_EMAIL=you@example.org"
    while :; do
      read -r -p "E-mail address for the administrator account: " email
      email="$(printf '%s' "${email}" | tr -d '[:space:]')"
      valid_email "${email}" && break
      warn "That does not look like an e-mail address. Try again."
    done
  elif ! valid_email "${email}"; then
    die "ADMIN_EMAIL is not a valid e-mail address: ${email}"
  fi
  ADMIN_EMAIL="${email}"
  ADMIN_PASSWORD="$(gen_password)"
  create_app_user "${ADMIN_EMAIL}" "${ADMIN_PASSWORD}" admin Admin 1
  NEW_ADMIN=1
}

cmd_up() {
  ensure_env
  ensure_encryption_env
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
  if admin_exists; then
    log "An administrator account already exists; leaving it as it is (./quickstart.sh reset-admin sets a new one)."
  else
    create_admin
  fi
  local ADMIN_BLOCK="    Administrator: the account you created earlier (./quickstart.sh reset-admin sets a new one)."
  if [[ "${NEW_ADMIN:-0}" == 1 ]]; then
    ADMIN_BLOCK="    Administrator account (SHOWN ONCE: write it down; you must change it at first sign-in)
      E-mail:    ${ADMIN_EMAIL}
      Password:  ${ADMIN_PASSWORD}"
  fi
  local APP_URL="https://localhost:${HTTPS_PORT:-8443}"
  [[ "${TLS:-on}" == "off" ]] && APP_URL="http://localhost:5173 (TLS is OFF)"
  cat <<EOF

  coneshare-alt is running.

    App:       ${APP_URL}   (HTTPS; import certs/ca.crt into your browser to trust it)
    API:       http://localhost:8000/api/v1/   (plain HTTP, localhost only; use the app URL)
    Swagger:   http://localhost:8000/api/schema/swagger/   (DEBUG only)

    Encryption: files at rest AES-256-GCM (key in .env: back it up), TLS at the front door, to the
    file service and to Redis. Details: docs/fork/container-security.md

${ADMIN_BLOCK}
  E-mail (access codes, sign-in links, approval requests) is printed in the backend log until you
  configure a mail server:   ./quickstart.sh logs
  Your own mail server: Admin > Notifications > E-mail setup, or EMAIL_* in .env.

  Other commands: ./quickstart.sh test | check | stop | reset | reset-admin | testmail-demo
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
  rm -rf certs .testmail-demo
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
  reset-admin)
    "${DC[@]}" exec -T backend true >/dev/null 2>&1 || die "Stack is not running. Run ./quickstart.sh first."
    create_admin
    printf '\n  Administrator account (SHOWN ONCE: write it down; you must change it at first sign-in)\n    E-mail:    %s\n    Password:  %s\n\n' "${ADMIN_EMAIL}" "${ADMIN_PASSWORD}" ;;
  testmail-demo)
    "${DC[@]}" exec -T backend true >/dev/null 2>&1 || cmd_up
    exec ./testmail-demo.sh "${@:2}" ;;
  *)        sed -n '2,20p' "$0"; exit 1 ;;
esac
