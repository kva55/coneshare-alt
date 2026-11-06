# Shared by quickstart.sh and testmail-demo.sh (source it; do not run it).
# Needs to be sourced from the project root.

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }

export COMPOSE_PROJECT_NAME=coneshare
export COMPOSE_FILE="docker-compose.yml"
# The optional mail demo (./quickstart.sh testmail-demo) adds its own compose file; once it exists every command
# (stop, logs, reset, quickclose) includes it so its containers are handled with the rest.
DEMO_DIR=".testmail-demo"
[[ -f "${DEMO_DIR}/docker-compose.yml" ]] && export COMPOSE_FILE="${COMPOSE_FILE}:${DEMO_DIR}/docker-compose.yml"
# TLS is on unless switched off with TLS=off, or (when TLS is not given) the existing .env says it is off.
if [[ -z "${TLS:-}" && -f .env ]] && grep -q '^SITE_DOMAIN=http://' .env; then TLS=off; fi
if [[ "${TLS:-on}" == "off" ]]; then
  export COMPOSE_FILE="${COMPOSE_FILE}:docker-compose.plain.yml"
else
  export COMPOSE_PROFILES=tls   # the HTTPS front door (nginx)
fi

if docker compose version >/dev/null 2>&1; then
  DC=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  DC=(docker-compose)
else
  die "Docker Compose not found. Install Docker Engine with the compose plugin."
fi
docker info >/dev/null 2>&1 || die "Cannot talk to the Docker daemon (is it running? are you in the docker group / using sudo?)."

set_env() {  # set_env KEY VALUE: replace the line in .env or append it
  local key="$1" value="$2"
  if grep -q "^${key}=" .env; then
    sed -i.bak -e "s|^${key}=.*|${key}=${value}|" .env && rm -f .env.bak
  else
    printf '%s=%s\n' "${key}" "${value}" >> .env
  fi
}

random_secret() {
  if command -v openssl >/dev/null 2>&1; then openssl rand -hex 32
  else python3 -c 'import secrets; print(secrets.token_hex(32))'; fi
}

# 16 characters with at least one upper-case letter, lower-case letter, digit and symbol. The symbols are
# chosen to survive copy and paste and shell quoting (no quotes, backslash, $, backtick, &, |, <, >, ;, parentheses).
gen_password() {
  local pw
  while :; do
    pw="$(LC_ALL=C tr -dc 'A-Za-z0-9!@#%^*_+=?-' </dev/urandom | head -c 16)"
    [[ ${#pw} -eq 16 && "$pw" =~ [A-Z] && "$pw" =~ [a-z] && "$pw" =~ [0-9] && "$pw" =~ [\!@#%^*_+=?-] ]] && { printf '%s' "$pw"; return; }
  done
}

valid_email() { [[ "$1" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$ || "$1" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.local$ ]]; }

# create_app_user EMAIL PASSWORD ROLE NAME MUST_CHANGE(0|1)   (ROLE: admin or member). Creates or updates the account.
create_app_user() {
  local email="$1" password="$2" role="$3" name="$4" must="$5"
  "${DC[@]}" exec -T \
    -e QS_EMAIL="${email}" -e QS_PASSWORD="${password}" -e QS_ROLE="${role}" -e QS_NAME="${name}" \
    -e QS_MUST="${must}" -e QS_ORG_NAME="${ORG_NAME:-Default Organization}" \
    backend python manage.py shell -c '
import os
from core.models import Organization, User
email = os.environ["QS_EMAIL"].strip().lower()
role = os.environ["QS_ROLE"]
org = Organization.objects.order_by("created_at").first() or Organization.objects.create(name=os.environ["QS_ORG_NAME"])
user, created = User.objects.get_or_create(email=email, defaults={"username": email, "organization": org, "name": os.environ["QS_NAME"]})
user.organization = user.organization or org
user.role = role
user.is_staff = role == "admin"
user.is_superuser = role == "admin"
user.is_active = True
user.must_change_password = os.environ["QS_MUST"] == "1"
user.set_password(os.environ["QS_PASSWORD"])
user.save()
print(("created" if created else "updated"), user.email, "(" + role + ")")
'
}
