#!/usr/bin/env bash
# Local synthetic stack for E2E: upstream's docker-compose.yml plus fork/stack/compose.yml, built from this
# worktree, under its own project name, loopback ports and volumes. One stack per machine; builds take the
# same lock as fork/tools/test.sh. Synthetic data only (fork/stack/seed.py); never production volumes.
# Usage: fork/tools/stack.sh up|down|seed|status|logs [service]
#   up      build and start (frontend, backend, workers, MCP server), wait until healthy
#   seed    load the synthetic data set into a fresh stack (for a clean reseed: down, up, seed)
#   down    stop and delete the stack and its volumes
#   status  owner worktree, containers, URLs
#   logs    last 200 lines (all services or one)
# Ports: FORK_STACK_FRONTEND_PORT (13000), FORK_STACK_BACKEND_PORT (18000), FORK_STACK_MCP_PORT (18765).
set -euo pipefail
root="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
project="securo-custom-e2e"
state_dir="${XDG_CACHE_HOME:-$HOME/.cache}/securo-custom"
owner_file="$state_dir/stack.owner"
mkdir -p "$state_dir"
export FORK_STACK_FRONTEND_PORT="${FORK_STACK_FRONTEND_PORT:-13000}"
export FORK_STACK_BACKEND_PORT="${FORK_STACK_BACKEND_PORT:-18000}"
export FORK_STACK_MCP_PORT="${FORK_STACK_MCP_PORT:-18765}"

# A scrubbed environment: upstream's compose file interpolates many ${VAR}s (storage, LLM keys, OIDC, SECRET_KEY)
# from the caller's shell, and the stack must run on upstream defaults plus fork/stack/compose.yml only.
compose() {
  env -i PATH="$PATH" HOME="$HOME" ${DOCKER_HOST:+DOCKER_HOST="$DOCKER_HOST"} \
    ${DOCKER_CONTEXT:+DOCKER_CONTEXT="$DOCKER_CONTEXT"} ${XDG_RUNTIME_DIR:+XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR"} \
    FORK_STACK_FRONTEND_PORT="$FORK_STACK_FRONTEND_PORT" FORK_STACK_BACKEND_PORT="$FORK_STACK_BACKEND_PORT" \
    FORK_STACK_MCP_PORT="$FORK_STACK_MCP_PORT" \
    docker compose -p "$project" --project-directory "$root" \
    -f "$root/docker-compose.yml" -f "$root/fork/stack/compose.yml" --profile agents "$@"
}

owner() { [[ -f $owner_file ]] && cat "$owner_file" || true; }
running() { [[ -n "$(docker ps -q --filter "label=com.docker.compose.project=$project")" ]]; }
# Stopped containers or leftover volumes still hold another worktree's database.
exists() {
  [[ -n "$(docker ps -aq --filter "label=com.docker.compose.project=$project")" ||
    -n "$(docker volume ls -q --filter "label=com.docker.compose.project=$project")" ]]
}

guard_owner() {
  local current
  current="$(owner)"
  if exists && [[ $current != "$root" ]]; then
    echo "the stack (containers or volumes) belongs to ${current:-an unknown worktree};" \
      "run 'fork/tools/stack.sh down' there first" >&2
    exit 1
  fi
}

case "${1:-}" in
  up)
    guard_owner
    # The image build copies backend/, Vite reads frontend/.env*, and the frontend mounts the worktree read-only:
    # none of them may carry real configuration. Existence check only; the files are never read.
    found=()
    for f in "$root"/.env* "$root"/backend/.env* "$root"/frontend/.env* "$root"/secrets "$root"/backend/secrets; do
      [[ -e $f && $f != *.example ]] && found+=("$f")
    done
    if ((${#found[@]})); then
      echo "refusing: real configuration in this worktree (${found[*]}); the stack runs without it" >&2
      exit 1
    fi
    echo "$root" >"$owner_file"
    exec 9>"$state_dir/heavy.lock"
    if ! flock -n 9; then echo "another heavy run holds the lock; waiting..." >&2; flock 9; fi
    compose build
    flock -u 9
    compose up -d --wait --wait-timeout 300
    "$0" status
    ;;
  seed)
    running || { echo "stack is not running: fork/tools/stack.sh up" >&2; exit 1; }
    python3 "$root/fork/stack/seed.py" --base-url "http://127.0.0.1:$FORK_STACK_BACKEND_PORT"
    ;;
  down)
    guard_owner
    compose down -v --remove-orphans
    rm -f "$owner_file"
    ;;
  status)
    echo "owner:    $(owner)"
    echo "frontend: http://127.0.0.1:$FORK_STACK_FRONTEND_PORT"
    echo "backend:  http://127.0.0.1:$FORK_STACK_BACKEND_PORT/api"
    echo "mcp:      http://127.0.0.1:$FORK_STACK_MCP_PORT/mcp"
    compose ps --format 'table {{.Service}}\t{{.Status}}'
    ;;
  logs)
    shift
    compose logs --tail 200 "$@"
    ;;
  *)
    sed -n '2,11p' "$0" >&2
    exit 2
    ;;
esac
