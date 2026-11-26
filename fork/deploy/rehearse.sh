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
  read -r phase tag file <"$phase_file"
  case "$phase" in
    stock) files=(-f "$state_dir/stock-$tag.yml" -f "$state_dir/stock-images-$tag.yml") ;;
    custom) files=(-f "$root/fork/deploy/coolify/$file.yml"); custom_tag=(SECURO_CUSTOM_TAG="$tag") ;;
  esac
  # Scrubbed environment, as fork/tools/stack.sh: no host variable reaches the stack.
  env -i PATH="$PATH" HOME="$HOME" ${DOCKER_HOST:+DOCKER_HOST="$DOCKER_HOST"} \
    "${custom_tag[@]}" \
    docker compose -p "$project" --project-directory "$state_dir" "${files[@]}" \
    -f "$root/fork/deploy/rehearsal.override.yml" "$@"
}

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
  backup)
    label="${1:?backup LABEL}"
    docker exec "$db" pg_dump -U postgres -Fc securo >"$out/$label.dump"
    docker exec -i "$db" pg_restore --list <"$out/$label.dump" | grep -c "TABLE DATA" | sed 's/^/tables with data in the dump: /'
    for volume in attachments agent_knowledge; do
      docker run --rm -v "${project}_$volume:/src:ro" --entrypoint tar pgvector/pgvector:pg16 -C /src -czf - . \
        >"$out/$label-$volume.tgz"
    done
    ls -l "$out/$label".*
    ;;
  restore)
    label="${1:?restore LABEL}"
    compose stop migrate backend celery-worker celery-beat mcp-server frontend
    docker exec "$db" dropdb -U postgres --force securo
    docker exec "$db" createdb -U postgres securo
    docker exec -i "$db" pg_restore -U postgres -d securo --exit-on-error <"$out/$label.dump"
    for volume in attachments agent_knowledge; do
      docker run --rm -i -v "${project}_$volume:/dst" --entrypoint sh pgvector/pgvector:pg16 \
        -c 'find /dst -mindepth 1 -delete && tar -C /dst -xzf -' <"$out/$label-$volume.tgz"
    done
    echo "restored $label; alembic_version: $(docker exec "$db" psql -U postgres -d securo -Atc 'select string_agg(version_num, $$,$$) from alembic_version')"
    ;;
  alembic)
    compose run --rm --no-deps -T migrate "$@"
    ;;
  stop-app) compose stop migrate backend celery-worker celery-beat mcp-server frontend ;;
  ps) compose ps -a --format 'table {{.Service}}\t{{.Image}}\t{{.Status}}' ;;
  stats) docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}' $(docker ps -q --filter "label=com.docker.compose.project=$project") ;;
  logs) compose logs --tail 100 "$@" ;;
  down)
    [[ -f $phase_file ]] && compose down -v --remove-orphans
    rm -f "$phase_file"
    ;;
  *) sed -n '2,18p' "$0" >&2; exit 2 ;;
esac
