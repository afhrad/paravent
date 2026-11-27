#!/usr/bin/env bash
#
# Send Paravent to the Linux server and restart its container.
#
#   ./sync-server.sh              code only: rebuild the image, restart, wait until healthy
#   ./sync-server.sh --corpus     also the 5eTools corpus and images, from the folders .env
#                                 names here (PARAVENT_REFERENCE_DIR, PARAVENT_IMAGE_DIR)
#   ./sync-server.sh --library    also the library, mirrored: the server's becomes exactly
#                                 this one (--delete). Backed up on the server first; what
#                                 changes is listed before it is sent, files newer on the
#                                 server marked. The server's signing key and logins stay.
#   ./sync-server.sh --translations
#                                 only the translations: the library's i18n folder (glossary,
#                                 translated texts, suggestions, UI overrides), for a server
#                                 without a translation key. No code, no rebuild, no restart:
#                                 the server reads changed translation files by itself. Its
#                                 own are backed up first; nothing only there is deleted;
#                                 files newer there are listed, then overwritten.
#   ./sync-server.sh --dry-run    show what would be sent, change nothing
#   ./sync-server.sh --no-build   send files, restart without rebuilding the image
#
# Where the server is comes from sync.env next to this script (gitignored):
#
#   PARAVENT_SYNC_HOST=user@server        ssh destination (a ~/.ssh/config alias works)
#   PARAVENT_SYNC_DIR=/srv/paravent       the folder holding docker-compose.yaml there
#
# The server keeps its own .env (its paths, PUID/PGID, secret key): never sent, never
# touched. The library and corpus folders are read from the running container's
# mounts, so they are found wherever the server's .env puts them.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

CORPUS=0
LIBRARY=0
TRANSLATIONS=0
DRY=0
BUILD=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --corpus)   CORPUS=1; shift ;;
    --library)  LIBRARY=1; shift ;;
    --translations) TRANSLATIONS=1; shift ;;
    --dry-run)  DRY=1; shift ;;
    --no-build) BUILD=0; shift ;;
    -h|--help)
      sed -n '3,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "Unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
done

if [[ $TRANSLATIONS -eq 1 && $((CORPUS + LIBRARY)) -gt 0 ]]; then
  echo "--translations sends the translations alone; --library already holds them." >&2
  exit 2
fi

# --- where to -------------------------------------------------------------------
if [[ -f sync.env ]]; then
  # shellcheck disable=SC1091
  source sync.env
fi
HOST="${PARAVENT_SYNC_HOST:-}"
DIR="${PARAVENT_SYNC_DIR:-}"
if [[ -z "$HOST" || -z "$DIR" ]]; then
  cat >&2 <<'EOF'
Where is the server? Create sync.env next to this script:

  PARAVENT_SYNC_HOST=user@server
  PARAVENT_SYNC_DIR=/srv/paravent
EOF
  exit 1
fi

# What --corpus sends, found before anything is sent. A whole release's corpus is its
# data/ folder, as the app reads it (Settings._find_corpus_in_release).
local_setting() {
  [[ -f .env ]] || return 0
  sed -n "s/^$1=//p" .env | tail -n 1 | tr -d "\"'"
}
if [[ $CORPUS -eq 1 ]]; then
  CORPUS_FROM="$(local_setting PARAVENT_REFERENCE_DIR)"
  CORPUS_FROM="${CORPUS_FROM:-data/reference}"
  [[ -f "$CORPUS_FROM/data/books.json" ]] && CORPUS_FROM="$CORPUS_FROM/data"
  if [[ ! -f "$CORPUS_FROM/books.json" ]]; then
    echo "No 5eTools corpus in $CORPUS_FROM (no books.json): see PARAVENT_REFERENCE_DIR in .env." >&2
    exit 1
  fi
  IMAGES_FROM="$(local_setting PARAVENT_IMAGE_DIR)"
  IMAGES_FROM="${IMAGES_FROM:-$(dirname "$CORPUS_FROM")/img}"
fi

RSYNC=(rsync -az)
[[ $DRY -eq 1 ]] && RSYNC+=(-n -v)

remote() { ssh "$HOST" "$@"; }

step() { printf '\n==> %s\n' "$*"; }

# --- before sending ---------------------------------------------------------------
step "Checking the server ($HOST:$DIR)"
remote "test -f '$DIR/docker-compose.yaml'" || {
  echo "No docker-compose.yaml in $DIR on $HOST." >&2
  exit 1
}

if [[ $TRANSLATIONS -eq 0 && -n "$(git status --porcelain 2>/dev/null)" ]]; then
  echo "Note: uncommitted changes are sent as they are in the working tree."
fi

# A mount's folder on the server, read from the running container (empty if it is
# not running, or the mount is a named volume).
mount_of() {
  remote "docker inspect paravent --format '{{range .Mounts}}{{if eq .Destination \"$1\"}}{{.Source}}{{end}}{{end}}' 2>/dev/null" || true
}

# --- the translations alone (optional) ----------------------------------------------
# Made here, where the translation key is; the server has none. Sent into the server's
# library as they are here, without touching anything else of it, and read there as soon
# as they change: the glossary and the translated texts follow their files' times.
if [[ $TRANSLATIONS -eq 1 ]]; then
  LOCAL_LIBRARY="$(local_setting PARAVENT_LIBRARY_DIR)"
  LOCAL_LIBRARY="${LOCAL_LIBRARY:-data/library}"
  if [[ ! -d "$LOCAL_LIBRARY/i18n" ]]; then
    echo "No translations in $LOCAL_LIBRARY/i18n." >&2
    exit 1
  fi
  step "Sending the translations"
  library="$(mount_of /data/library)"
  library="${library:-$DIR/data/library}"
  target="$library/i18n"
  echo "    from $LOCAL_LIBRARY/i18n to $target"
  # Never --delete: a translation made on the server stays. The glossary's own backups
  # (*.bak-*) stay here.
  I18N_RSYNC=(rsync -az --exclude '.DS_Store' --exclude '*.bak-*' --exclude '.*')
  changes="$("${I18N_RSYNC[@]}" -n -i "$LOCAL_LIBRARY/i18n/" "$HOST:$target/" 2>/dev/null \
    | grep -E '^<f' | sed -E 's/^[^ ]+ +//' || true)"
  keep_newer="$("${I18N_RSYNC[@]}" -n -i -u "$LOCAL_LIBRARY/i18n/" "$HOST:$target/" 2>/dev/null \
    | grep -E '^<f' | sed -E 's/^[^ ]+ +//' || true)"
  newer="$(comm -23 <(sort <<<"$changes" | sed '/^$/d') <(sort <<<"$keep_newer" | sed '/^$/d'))"
  count() { sed '/^$/d' <<<"$1" | wc -l | tr -d ' '; }
  echo "    $(count "$changes") to send, $(count "$newer") of them newer on the server"
  if [[ -n "$newer" ]]; then
    echo "    Newer on the server, overwritten (kept in the backup):"
    sed 's/^/      /' <<<"$newer"
  fi
  if [[ $DRY -eq 1 ]]; then
    [[ -n "$changes" ]] && { echo "    Sent:"; sed 's/^/      /' <<<"$changes"; }
    step "Dry run: nothing was changed"
    exit 0
  fi
  if [[ -z "$changes" ]]; then
    step "The server's translations are already these"
    exit 0
  fi
  stamp="$(date +%Y%m%d-%H%M%S)"
  remote "mkdir -p '$target' '$DIR/backups' \
    && tar -czf '$DIR/backups/translations-$stamp.tar.gz' -C '$target' . \
    && echo '    backup: $DIR/backups/translations-$stamp.tar.gz'"
  "${I18N_RSYNC[@]}" "$LOCAL_LIBRARY/i18n/" "$HOST:$target/"
  # The container writes as PUID:PGID; files sent as another user must belong to it.
  remote "cd '$DIR' && set -a && { [ -f .env ] && . ./.env || true; } && set +a \
    && owner=\"\${PUID:-1000}:\${PGID:-1000}\" \
    && if [ \"\$(id -u):\$(id -g)\" = \"\$owner\" ]; then :; \
       elif sudo -n true 2>/dev/null; then sudo chown -R \"\$owner\" '$target'; \
       else echo \"    Note: run  sudo chown -R \$owner $target  so the app can write.\"; fi"
  step "Translations sent; the server reads them without a restart"
  exit 0
fi

# --- the code ---------------------------------------------------------------------
# Exactly what the image is built from (see .dockerignore), plus the compose file.
# --delete inside app/ and static/ so a file removed here is removed there too.
step "Sending the code"
"${RSYNC[@]}" Dockerfile docker-compose.yaml .dockerignore pyproject.toml uv.lock LICENSE NOTICE.md \
  "$HOST:$DIR/"
"${RSYNC[@]}" --delete --exclude '__pycache__/' --exclude '.DS_Store' \
  app/ "$HOST:$DIR/app/"
"${RSYNC[@]}" --delete --exclude '.DS_Store' static/ "$HOST:$DIR/static/"
remote "mkdir -p '$DIR/data/rules' '$DIR/data/library/tags' '$DIR/documentation'"
"${RSYNC[@]}" --delete data/rules/ "$HOST:$DIR/data/rules/"
"${RSYNC[@]}" data/library/tags/5etools_tags.json "$HOST:$DIR/data/library/tags/"
"${RSYNC[@]}" documentation/character.schema.json "$HOST:$DIR/documentation/"

# --- the corpus (optional) ----------------------------------------------------------
if [[ $CORPUS -eq 1 ]]; then
  step "Sending the corpus"
  target="$(mount_of /data/reference)"
  target="${target:-$DIR/data/reference}"
  echo "    to $target"
  "${RSYNC[@]}" --delete --exclude '.DS_Store' "$CORPUS_FROM/" "$HOST:$target/"
  if [[ -d "$IMAGES_FROM" ]]; then
    target="$(mount_of /data/img)"
    target="${target:-$DIR/data/img}"
    echo "    to $target"
    "${RSYNC[@]}" --delete --exclude '.DS_Store' "$IMAGES_FROM/" "$HOST:$target/"
  else
    echo "    no images in $IMAGES_FROM, skipped"
  fi
fi

# --- the library (optional) ---------------------------------------------------------
if [[ $LIBRARY -eq 1 ]]; then
  step "Sending the library"
  target="$(mount_of /data/library)"
  target="${target:-$DIR/data/library}"
  echo "    to $target"
  if [[ $DRY -eq 0 ]]; then
    # Stopped while it is written, so a fight in progress cannot write in between;
    # and backed up first, because this is the irreplaceable data.
    stamp="$(date +%Y%m%d-%H%M%S)"
    remote "cd '$DIR' && docker compose stop paravent \
      && mkdir -p '$DIR/backups' \
      && tar -czf '$DIR/backups/library-$stamp.tar.gz' -C '$target' . \
      && echo '    backup: $DIR/backups/library-$stamp.tar.gz'"
  fi
  # Mirrored: what is not here is deleted there, a file newer there is overwritten.
  # The signing key and the logins are the server's own: excluded, so --delete leaves
  # them alone too.
  LIBRARY_RSYNC=(rsync -az --delete --exclude '.DS_Store' --exclude '.secret_key'
    --exclude '/sessions.json')
  # What changes, listed first: sent (<f), deleted (*deleting), and of the sent the ones
  # newer on the server, found as those a pass with -u would leave alone.
  changes="$("${LIBRARY_RSYNC[@]}" -n -i data/library/ "$HOST:$target/" \
    | grep -E '^(<f|\*deleting)' || true)"
  keep_newer="$("${LIBRARY_RSYNC[@]}" -n -i -u data/library/ "$HOST:$target/" \
    | grep -E '^<f' | sed -E 's/^[^ ]+ +//' || true)"
  sent="$(grep -E '^<f' <<<"$changes" | sed -E 's/^[^ ]+ +//' || true)"
  deleted="$(grep -E '^\*deleting' <<<"$changes" | sed -E 's/^[^ ]+ +//' || true)"
  newer="$(comm -23 <(sort <<<"$sent" | sed '/^$/d') <(sort <<<"$keep_newer" | sed '/^$/d'))"
  count() { sed '/^$/d' <<<"$1" | wc -l | tr -d ' '; }
  echo "    $(count "$sent") to send, $(count "$deleted") to delete, $(count "$newer") newer on the server"
  if [[ -n "$newer" ]]; then
    echo "    Newer on the server, overwritten:"
    sed 's/^/      /' <<<"$newer"
  fi
  if [[ -n "$deleted" ]]; then
    echo "    Only on the server, deleted:"
    sed 's/^/      /' <<<"$deleted"
  fi
  if [[ $DRY -eq 1 ]]; then
    [[ -n "$sent" ]] && { echo "    Sent:"; sed 's/^/      /' <<<"$sent"; }
  else
    "${LIBRARY_RSYNC[@]}" data/library/ "$HOST:$target/"
  fi
  # The container writes as PUID:PGID; files sent as another user must belong to it.
  if [[ $DRY -eq 0 ]]; then
    remote "cd '$DIR' && set -a && { [ -f .env ] && . ./.env || true; } && set +a \
      && owner=\"\${PUID:-1000}:\${PGID:-1000}\" \
      && if [ \"\$(id -u):\$(id -g)\" = \"\$owner\" ]; then :; \
         elif sudo -n true 2>/dev/null; then sudo chown -R \"\$owner\" '$target'; \
         else echo \"    Note: run  sudo chown -R \$owner $target  so the app can write.\"; fi"
  fi
fi

if [[ $DRY -eq 1 ]]; then
  step "Dry run: nothing was changed, nothing restarted"
  exit 0
fi

# --- restart ----------------------------------------------------------------------
if [[ $BUILD -eq 1 ]]; then
  step "Rebuilding and restarting"
  remote "cd '$DIR' && docker compose up -d --build --remove-orphans"
else
  step "Restarting"
  remote "cd '$DIR' && docker compose up -d && docker compose restart paravent"
fi

# The image's healthcheck asks /health; loading the corpus takes a few seconds.
step "Waiting for the server to report healthy"
for _ in $(seq 1 60); do
  status="$(remote "docker inspect paravent --format '{{.State.Health.Status}}'" 2>/dev/null || echo unknown)"
  case "$status" in
    healthy)
      port="$(remote "cd '$DIR' && docker compose port paravent 8000" 2>/dev/null | sed 's/.*://')"
      echo "    healthy — http://${HOST#*@}:${port:-8000}"
      exit 0 ;;
    unhealthy)
      break ;;
  esac
  sleep 3
done

echo "The server did not become healthy. Its last log lines:" >&2
remote "cd '$DIR' && docker compose logs --tail 40 paravent" >&2 || true
exit 1
