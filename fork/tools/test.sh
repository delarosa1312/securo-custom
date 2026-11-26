#!/usr/bin/env bash
# Resource-capped test runs. One heavy run at a time on this machine, across every session and worktree.
# Usage: fork/tools/test.sh backend [pytest args]     e.g. fork/tools/test.sh backend tests/fork -k budget
#        fork/tools/test.sh frontend [vitest args]    e.g. fork/tools/test.sh frontend src/fork
# FORK_TEST_WORKERS (default 4) caps parallel workers. Bare `pytest -n auto` takes all 20 cores.
set -euo pipefail
root="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
lock_dir="${XDG_CACHE_HOME:-$HOME/.cache}/securo-custom"
mkdir -p "$lock_dir"
workers="${FORK_TEST_WORKERS:-4}"
suite="${1:-}"
[[ $# -gt 0 ]] && shift

exec 9>"$lock_dir/heavy.lock"
if ! flock -n 9; then
  echo "another heavy run holds the lock; waiting..." >&2
  flock 9
fi

case "$suite" in
  backend)
    cd "$root/backend"
    uv run --no-sync pytest -n "$workers" --dist loadfile -q "$@"
    ;;
  frontend)
    cd "$root/frontend"
    npx vitest run --maxWorkers="$workers" "$@"
    ;;
  *)
    echo "usage: test.sh backend|frontend [args]" >&2
    exit 2
    ;;
esac
