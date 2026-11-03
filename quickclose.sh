#!/usr/bin/env bash
# Tear down the coneshare-alt test stack started by quickstart.sh and delete everything it created.
#
#   ./quickclose.sh              remove containers, volumes, images, all data, AND this project folder
#   ./quickclose.sh --keep-code  same, but keep this project folder (source code + git history)
#   ./quickclose.sh -y ...       do not ask for confirmation
#
# What gets deleted:
#   - all coneshare containers, networks and named volumes (redis data)
#   - all images used by the stack (coneshare:dev, coneshare:frontend, the core image, redis)
#   - ~/.coneshare-data            uploaded files stored by the Go file service
#   - backend/db.sqlite3 (+ -journal/-wal/-shm), backend/media/* uploads, .env
#   - this project folder itself (unless --keep-code)
#
# Unpushed git commits in this folder are lost with the folder. Push first if you want to keep them.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
STORAGE_DIR="${HOME}/.coneshare-data"
export COMPOSE_PROJECT_NAME=coneshare

KEEP_CODE=0
ASSUME_YES=0
for arg in "$@"; do
  case "$arg" in
    --keep-code) KEEP_CODE=1 ;;
    -y|--yes)    ASSUME_YES=1 ;;
    -h|--help)   sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; sed -n '2,15p' "$0"; exit 1 ;;
  esac
done

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }

# Refuse to run anywhere that does not look like this project, so a moved or
# symlinked copy of the script can never rm -rf an unrelated directory.
[[ -f "${PROJECT_DIR}/docker-compose.yml" && -f "${PROJECT_DIR}/quickstart.sh" && -d "${PROJECT_DIR}/backend" && -d "${PROJECT_DIR}/core" ]] \
  || die "${PROJECT_DIR} does not look like the coneshare-alt project folder. Refusing to continue."
case "${PROJECT_DIR}" in
  /|"${HOME}"|"${HOME}/"|/home|/root|/usr*|/etc*|/var|/opt|/tmp) die "Refusing to delete ${PROJECT_DIR}." ;;
esac

echo "This will permanently delete:"
echo "  - coneshare containers, volumes, networks and images"
echo "  - ${STORAGE_DIR}"
echo "  - database, uploads and .env in ${PROJECT_DIR}"
[[ $KEEP_CODE -eq 0 ]] && echo "  - the project folder ${PROJECT_DIR}"
if [[ -d "${PROJECT_DIR}/.git" && $KEEP_CODE -eq 0 ]] && command -v git >/dev/null 2>&1; then
  unpushed="$(git -C "${PROJECT_DIR}" log --oneline --branches --not --remotes 2>/dev/null | wc -l | tr -d ' ')"
  [[ "${unpushed}" != "0" ]] && warn "${unpushed} local commit(s) have not been pushed to any remote and will be lost."
fi

if [[ $ASSUME_YES -eq 0 ]]; then
  read -r -p "Type DELETE to continue: " ans
  [[ "${ans}" == "DELETE" ]] || { echo "Aborted. Nothing was deleted."; exit 0; }
fi

cd "${PROJECT_DIR}"

if docker compose version >/dev/null 2>&1; then
  DC=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  DC=(docker-compose)
else
  DC=()
fi

if [[ ${#DC[@]} -gt 0 ]] && docker info >/dev/null 2>&1; then
  log "Removing containers, networks, volumes and images"
  # docker-compose.yml references env_file: .env; make sure it exists so `down` can parse the file.
  [[ -f .env ]] || touch .env
  "${DC[@]}" down --volumes --rmi all --remove-orphans || warn "docker compose down reported errors (continuing)."
else
  warn "Docker/Compose not available; skipping container and image cleanup."
fi

log "Deleting stored files in ${STORAGE_DIR}"
if [[ -d "${STORAGE_DIR}" ]]; then
  rm -rf "${STORAGE_DIR}" 2>/dev/null || {
    # Files written by the containers may be owned by root.
    warn "Some files are root-owned; retrying with sudo."
    sudo rm -rf "${STORAGE_DIR}"
  }
fi

if [[ $KEEP_CODE -eq 1 ]]; then
  log "Deleting local database, uploads and .env (keeping source code)"
  rm -f backend/db.sqlite3 backend/db.sqlite3-journal backend/db.sqlite3-wal backend/db.sqlite3-shm .env
  find backend/media -mindepth 1 ! -name .gitkeep -exec rm -rf {} + 2>/dev/null || sudo find backend/media -mindepth 1 ! -name .gitkeep -exec rm -rf {} +
  log "Done. Source code kept in ${PROJECT_DIR}."
else
  log "Deleting project folder ${PROJECT_DIR}"
  cd /
  rm -rf "${PROJECT_DIR}" 2>/dev/null || {
    warn "Some files are root-owned; retrying with sudo."
    sudo rm -rf "${PROJECT_DIR}"
  }
  log "Done. Everything has been removed."
fi
