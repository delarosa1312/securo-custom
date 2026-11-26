#!/usr/bin/env bash
# Static checks with upstream's own commands. Prints one line per check and the output of failures only.
# Usage: fork/tools/check.sh [backend|frontend]   (default: both)
set -u
root="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
what="${1:-all}"
fail=0

run() {
  local name="$1" dir="$2" out
  shift 2
  if out="$(cd "$root/$dir" && "$@" 2>&1)"; then
    echo "ok    $name"
  else
    echo "FAIL  $name"
    echo "$out"
    fail=1
  fi
}

case "$what" in
  all | backend | frontend) ;;
  *) echo "usage: check.sh [backend|frontend]" >&2; exit 2 ;;
esac

if [[ $what == all || $what == backend ]]; then
  run "ruff" backend uv run --no-sync ruff check .
  run "ty" backend uv run --no-sync ty check .
  run "migration chain (upstream line)" . python3 backend/scripts/check_migration_chain.py
  run "migration chain (fork line)" . python3 fork/tools/check_fork_migrations.py
  run "release tooling" . fork/release/test.sh
  run "Coolify files rendered" . python3 fork/deploy/render.py --check
fi
if [[ $what == all || $what == frontend ]]; then
  run "eslint" frontend npm run --silent lint -- --max-warnings=0
  run "tsc" frontend npm run --silent typecheck
fi
exit $fail
