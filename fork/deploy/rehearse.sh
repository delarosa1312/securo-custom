#!/usr/bin/env bash
# Upgrade and rollback rehearsal on an isolated local database with synthetic data (docs/fork/DEPLOY.md).
# One Compose project (securo-custom-rehearsal) whose volumes survive every switch of images, as a Coolify
# resource's volumes survive a redeploy. Loopback ports 23000 (app), 28000 (API), 28765 (MCP). Never production.
# Usage: fork/deploy/rehearse.sh <command> [args]
#   stock [TAG]            run upstream's own docker-compose.prod.yml of v<TAG> with its images (default 0.22.0)
#   custom TAG [FILE]      run fork/deploy/coolify/<FILE>.yml (production by default) with SECURO_CUSTOM_TAG=TAG
#   seed                   synthetic data (fork/stack/seed.py) and one synthetic attachment, into a fresh database
#   snapshot LABEL         data snapshot to OUT/LABEL.json (the first one is the baseline the others compare to)
#   compare LABEL [OTHER]  compare a snapshot with the baseline (or OTHER)
#   backup LABEL           pg_dump -Fc and the attachments and agent-knowledge volumes to OUT/LABEL.*
#   restore LABEL          stop the app, recreate the database from OUT/LABEL.dump, restore the volumes
#   alembic ARGS...        alembic with the image of the current phase (`alembic current`, `downgrade fork@f001`)
#   stop-app               stop everything but db and redis
#   ps | stats | logs [SERVICE] | down
# OUT: REHEARSAL_OUT (default ~/scratch/securo-custom/rehearsal). Takes the heavy lock only while pulling.
set -euo pipefail
root="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
project="securo-custom-rehearsal"
state_dir="${XDG_CACHE_HOME:-$HOME/.cache}/securo-custom/rehearsal"
out="${REHEARSAL_OUT:-$HOME/scratch/securo-custom/rehearsal}"
mkdir -p "$state_dir" "$out"
phase_file="$state_dir/phase"
db="$project-db-1"
api="http://127.0.0.1:${REHEARSAL_BACKEND_PORT:-28000}"

compose() {
  local files=() phase tag file custom_tag=()
  [[ -f $phase_file ]] || { echo "no rehearsal running: start with 'stock' or 'custom'" >&2; exit 1; }
  read -r phase tag file <"$phase_file"
  case "$phase" in
    stock) files=(-f "$state_dir/stock-$tag.yml" -f "$state_dir/stock-images-$tag.yml") ;;
    custom) files=(-f "$root/fork/deploy/coolify/$file.yml"); custom_tag=(SECURO_CUSTOM_TAG="$tag") ;;
  esac
  # Scrubbed environment, as fork/tools/stack.sh: no host variable reaches the stack.
  env -i PATH="$PATH" HOME="$HOME" ${DOCKER_HOST:+DOCKER_HOST="$DOCKER_HOST"} \
    ${DOCKER_CONTEXT:+DOCKER_CONTEXT="$DOCKER_CONTEXT"} ${XDG_RUNTIME_DIR:+XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR"} \
    "${custom_tag[@]}" \
    docker compose -p "$project" --project-directory "$state_dir" "${files[@]}" \
    -f "$root/fork/deploy/rehearsal.override.yml" "$@"
}

# The running container of one service, found by Compose labels as on the Coolify host.
svc() { docker ps -q --filter "label=com.docker.compose.project=$project" --filter "label=com.docker.compose.service=$1"; }

up() {
  exec 9>"$state_dir/../heavy.lock"
  if ! flock -n 9; then echo "another heavy run holds the lock; waiting..." >&2; flock 9; fi
  compose pull --ignore-buildable --quiet --policy missing
  flock -u 9
  compose up -d --wait --wait-timeout 300 --remove-orphans
  compose ps --format 'table {{.Service}}\t{{.Image}}\t{{.Status}}'
}

cmd="${1:-}"
[[ $# -gt 0 ]] && shift
case "$cmd" in
  stock)
    tag="${1:-0.22.0}"
    git -C "$root" show "v$tag:docker-compose.prod.yml" >"$state_dir/stock-$tag.yml"
    cat >"$state_dir/stock-images-$tag.yml" <<EOF
x-stock: &stock
  image: ghcr.io/securo-finance/securo-backend:$tag
services:
  migrate: *stock
  backend: *stock
  celery-worker: *stock
  celery-beat: *stock
  mcp-server: *stock
  frontend:
    image: ghcr.io/securo-finance/securo-frontend:$tag
EOF
    echo "stock $tag -" >"$phase_file"
    up
    ;;
  custom)
    tag="${1:?custom TAG [production|staging]}"
    echo "custom $tag ${2:-production}" >"$phase_file"
    up
    ;;
  seed)
    python3 "$root/fork/stack/seed.py" --base-url "$api"
    python3 "$root/fork/deploy/rehearsal_probe.py" attach --base-url "$api"
    ;;
  snapshot)
    label="${1:?snapshot LABEL}"
    baseline=()
    [[ -f $out/baseline.txt ]] && baseline=(--baseline "$out/$(cat "$out/baseline.txt").json")
    python3 "$root/fork/deploy/rehearsal_probe.py" snapshot --base-url "$api" --db "$db" \
      --token-file "$out/token.txt" "${baseline[@]}" >"$out/$label.json"
    [[ -f $out/baseline.txt ]] || echo "$label" >"$out/baseline.txt"
    python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print("alembic_version", d["db"]["alembic_version"], "| tables", len(d["db"]["tables"]), "| rows", sum(t["rows"] for t in d["db"]["tables"].values()), "| old token accepted", d["token_from_first_snapshot_accepted"], "| health", d["health"])' "$out/$label.json"
    ;;
  compare)
    python3 "$root/fork/deploy/rehearsal_probe.py" compare "$out/${2:-$(cat "$out/baseline.txt")}.json" "$out/$1.json"
    ;;
  # backup and restore run the commands of docs/fork/DEPLOY.md (Backups, Rollback B), with the rehearsal's
  # Compose project in place of the Coolify Service UUID.
  backup)
    label="${1:?backup LABEL}"
    umask 077
    DB=$(svc db)
    echo "alembic_version: $(docker exec "$DB" psql -U postgres -d securo -Atc 'select string_agg(version_num, $$,$$) from alembic_version')"
    docker exec "$DB" pg_dump -U postgres -Fc securo >"$out/$label.dump" && echo "dump ok"
    docker exec -i "$DB" pg_restore -f /dev/null <"$out/$label.dump" && echo "dump reads to the end"
    for v in attachments agent_knowledge; do
      vol=$(docker volume ls -q --filter "label=com.docker.compose.project=$project" | grep "_$v\$" || true)
      [[ -n $vol ]] || { echo "no $v volume"; continue; }
      docker run --rm -v "$vol:/src:ro" --entrypoint tar pgvector/pgvector:pg16 -C /src -czf - . >"$out/$label-$v.tgz" &&
        gzip -t "$out/$label-$v.tgz" && tar -tzf "$out/$label-$v.tgz" >/dev/null && echo "$v ok"
    done
    ls -l "$out/$label".* "$out/$label"-*
    ;;
  restore)
    label="${1:?restore LABEL}"
    DB=$(svc db)
    # 1. Check every file and restore into a side database while the app still runs: nothing live is touched yet.
    docker exec -i "$DB" pg_restore -f /dev/null <"$out/$label.dump" || { echo "dump unreadable: stop" >&2; exit 1; }
    for v in attachments agent_knowledge; do
      { gzip -t "$out/$label-$v.tgz" && tar -tzf "$out/$label-$v.tgz" >/dev/null; } || { echo "$v archive unreadable: stop" >&2; exit 1; }
    done
    docker exec "$DB" createdb -U postgres securo_restore
    if ! docker exec -i "$DB" pg_restore -U postgres -d securo_restore --exit-on-error <"$out/$label.dump"; then
      docker exec "$DB" dropdb -U postgres securo_restore
      echo "restore failed; the live database is untouched" >&2
      exit 1
    fi
    echo "restore ok"
    # 2. Stop the app; only db and redis may remain.
    for s in frontend mcp-server celery-beat celery-worker backend; do
      id=$(svc "$s"); [[ -z $id ]] || docker stop "$id" >/dev/null
    done
    running=$(docker ps --filter "label=com.docker.compose.project=$project" --format '{{.Label "com.docker.compose.service"}}' | sort | tr '\n' ' ')
    echo "running: $running"
    [[ $running == "db redis " ]] || { echo "something else still runs: stop" >&2; exit 1; }
    # 3. Swap the databases; the one from before the rollback is kept until the operator drops it.
    docker exec "$DB" psql -U postgres -d postgres -v ON_ERROR_STOP=1 -q \
      -c "select pg_terminate_backend(pid) from pg_stat_activity where datname = 'securo' and pid <> pg_backend_pid()" \
      -c 'alter database securo rename to securo_before_rollback' -c 'alter database securo_restore rename to securo' >/dev/null
    echo "alembic_version: $(docker exec "$DB" psql -U postgres -d securo -Atc 'select string_agg(version_num, $$,$$) from alembic_version')"
    # 4. Volumes, from the archives checked in step 1.
    for v in attachments agent_knowledge; do
      vol=$(docker volume ls -q --filter "label=com.docker.compose.project=$project" | grep "_$v\$")
      docker run --rm -i -v "$vol:/dst" --entrypoint sh pgvector/pgvector:pg16 \
        -c 'find /dst -mindepth 1 -delete && tar -C /dst -xzf -' <"$out/$label-$v.tgz" && echo "$v restored"
    done
    docker exec "$DB" dropdb -U postgres securo_before_rollback  # the rehearsal keeps nothing; production keeps it a while
    ;;
  alembic)
    compose run --rm --no-deps -T migrate "$@"
    ;;
  stop-app) compose stop migrate backend celery-worker celery-beat mcp-server frontend ;;
  ps) compose ps -a --format 'table {{.Service}}\t{{.Image}}\t{{.Status}}' ;;
  stats)
    ids=$(docker ps -q --filter "label=com.docker.compose.project=$project")
    [[ -n $ids ]] || { echo "no rehearsal containers running" >&2; exit 1; }
    # shellcheck disable=SC2086
    docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}' $ids
    ;;
  logs) compose logs --tail 100 "$@" ;;
  down)
    if [[ -f $phase_file ]]; then
      compose down -v --remove-orphans
    else
      docker compose -p "$project" down -v --remove-orphans
    fi
    rm -f "$phase_file"
    ;;
  *) sed -n '2,16p' "$0" >&2; exit 2 ;;
esac
