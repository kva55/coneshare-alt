#!/usr/bin/env bash
# coneshare-alt local test stack.
#
#   ./quickstart.sh            build + start everything, migrate, create the admin account
#   ./quickstart.sh test       run backend (pytest), frontend (lint + vitest) and Go tests in the containers
#   ./quickstart.sh check      verify the hand-written migrations match the models (makemigrations --check)
#   ./quickstart.sh logs       follow container logs
#   ./quickstart.sh stop       stop the stack (data is kept)
#   ./quickstart.sh reset      stop and delete ALL local data (database, uploaded files, .env)
#
# This is a development/test setup only (Django runserver, DEBUG=True, console email).
# Do not expose it to a network you do not trust.
set -euo pipefail

cd "$(dirname "$0")"

ADMIN_EMAIL="${ADMIN_EMAIL:-admin@coneshare.local}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-ConeshareAdmin!2026}"
ORG_NAME="${ORG_NAME:-Default Organization}"
STORAGE_DIR="${HOME}/.coneshare-data"   # mounted by docker-compose.yml into the core service
export COMPOSE_PROJECT_NAME=coneshare

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

wait_for_backend() {
  log "Waiting for the backend to answer (first build can take several minutes)..."
  local i
  for i in $(seq 1 120); do
    if "${DC[@]}" exec -T backend python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/languages/', timeout=3)" >/dev/null 2>&1; then
      return 0
    fi
    sleep 3
  done
  "${DC[@]}" logs --tail=80 backend || true
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
  mkdir -p "${STORAGE_DIR}"
  log "Building and starting containers"
  "${DC[@]}" up -d --build
  wait_for_backend
  log "Applying database migrations"
  "${DC[@]}" exec -T backend python manage.py migrate --noinput
  create_admin
  cat <<EOF

  coneshare-alt is running.

    App:       http://localhost:5173
    API:       http://localhost:8000/api/v1/
    Swagger:   http://localhost:8000/api/schema/swagger/   (DEBUG only)

    Admin login
      email:    ${ADMIN_EMAIL}
      password: ${ADMIN_PASSWORD}

  Emails (share-link verification, notifications) are printed to the backend log:
    ./quickstart.sh logs

  Other commands: ./quickstart.sh test | check | stop | reset
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
  rm -rf "${STORAGE_DIR}"
  log "Reset complete."
}

case "${1:-up}" in
  up|start) cmd_up ;;
  test)     cmd_test ;;
  check)    cmd_check ;;
  logs)     "${DC[@]}" logs -f ;;
  stop)     "${DC[@]}" stop ;;
  reset)    cmd_reset ;;
  *)        sed -n '2,12p' "$0"; exit 1 ;;
esac
