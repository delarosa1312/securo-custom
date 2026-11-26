#!/usr/bin/env bash
# Tests for fork/release: version parsing, build.sh refusals (all before any Docker build) and the rollback
# section of record.py, in a throwaway clone so no test tag ever lands in the shared repository.
# Usage: fork/release/test.sh
set -uo pipefail
src="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
tmp="$(mktemp -d "${XDG_CACHE_HOME:-$HOME/.cache}/securo-custom-release-test.XXXXXX")"
trap 'rm -rf "$tmp"' EXIT
fail=0
ok() { echo "ok    $1"; }
bad() { echo "FAIL  $1"; fail=1; }
expect() { # expect <name> <expected exit: 0|nonzero> <grep pattern or -> -- command...
  local name="$1" want="$2" pattern="$3" out code
  shift 4
  out="$("$@" 2>&1)"
  code=$?
  if [[ $want == 0 && $code != 0 ]] || [[ $want != 0 && $code == 0 ]]; then
    bad "$name (exit $code): $out"
  elif [[ $pattern != - ]] && ! grep -qE -- "$pattern" <<<"$out"; then
    bad "$name: no /$pattern/ in: $out"
  else
    ok "$name"
  fi
}

source "$src/fork/release/version.sh"
for v in v0.22.0-custom.1 v1.0.10-custom.12; do expect "version $v accepted" 0 - -- release_upstream_base "$v"; done
for v in 0.22.0-custom.1 v0.22.0-custom.0 v0.22.0-custom.01 v0.22-custom.1 v0.22.0-custom.1-rc v0.22.0 latest; do
  expect "version $v refused" 1 - -- release_upstream_base "$v"
done
expect "base of v0.22.0-custom.3" 0 '^v0\.22\.0$' -- release_upstream_base v0.22.0-custom.3

# A clone that shares objects with the real repository; its tags and branches are its own.
git clone -q --shared --no-checkout "$src" "$tmp/repo"
cd "$tmp/repo"
git checkout -q --detach "$(git -C "$src" rev-parse HEAD)"
git update-ref refs/remotes/origin/main HEAD
git tag -l 'v*-custom.*' | xargs -r git tag -d >/dev/null  # real releases would change the "previous release"
head="$(git rev-parse HEAD)"
build="$tmp/repo/fork/release/build.sh"
record="$tmp/repo/fork/release/record.py"
# The scripts under test come from the working tree, which may be ahead of HEAD.
mkdir -p fork/release && cp "$src"/fork/release/{build.sh,record.py,version.sh} fork/release/

expect "build refuses a bad version" 1 "version must look like" -- "$build" v0.22.0
expect "build refuses a missing tag" 1 "tag v0.22.0-custom.7 does not exist" -- "$build" v0.22.0-custom.7
off_main="$(git commit-tree -p HEAD -m "synthetic: not on main" "HEAD^{tree}")"
expect "build refuses a commit off main" 1 "is not on origin/main" -- "$build" v0.22.0-custom.7 --commit "$off_main"
expect "build refuses the wrong base" 1 "does not contain upstream v0.23.0|upstream tag v0.23.0 is missing" -- "$build" v0.23.0-custom.1 --commit HEAD
expect "build --push needs the tag" 1 "--push needs the tag" -- "$build" v0.22.0-custom.7 --commit HEAD --push
git tag v0.22.0-custom.7 "$head~1" 2>/dev/null
expect "build --push refuses a tag on another commit" 1 "points to" -- "$build" v0.22.0-custom.7 --commit HEAD --push
git tag -d v0.22.0-custom.7 >/dev/null

# Rollback section. First release: back to stock removes the whole fork line, then its root.
expect "record: first release rolls back to stock" 0 'downgrade fork@f000`; `alembic downgrade fork@f000-1`' -- \
  "$record" v0.22.0-custom.1 --commit HEAD
# A first release cut after a sync moved upstream past the fork root's parent: only the restore applies.
printf 'revision = "998"\ndown_revision = "131"\n' >backend/alembic/versions/998_synthetic.py
git add -f backend/alembic/versions/998_synthetic.py && git commit -q -m "synthetic: a sync before the first release"
expect "record: first release after a sync is restore only" 0 "a downgrade cannot take it back" -- \
  "$record" v0.22.0-custom.1 --commit HEAD
git checkout -q --detach "$head"
# A previous release at the same fork head: no migration, switch the tag.
git tag v0.22.0-custom.1 HEAD
git tag v0.23.0-custom.1 HEAD
expect "record: an older base is not the newest" 0 '^false$' -- "$record" v0.22.0-custom.5 --is-newest
expect "record: a newer base is the newest" 0 '^true$' -- "$record" v0.23.0-custom.2 --is-newest
git tag -d v0.23.0-custom.1 >/dev/null
git commit -q --allow-empty -m "synthetic: a release without a migration"
git update-ref refs/remotes/origin/main HEAD
expect "record: no migration since the previous release" 0 'No migration since the previous release' -- \
  "$record" v0.22.0-custom.2 --commit HEAD
# A release that adds a fork migration: downgrade to the previous release's fork head.
prev_head="$(python3 -c 'import sys; sys.path.insert(0, "fork/release"); import record; print(record.fork_head("HEAD"))')"
cat >"backend/alembic/fork_versions/f999_synthetic.py" <<EOF
revision = "f999"
down_revision = "$prev_head"
branch_labels = None
depends_on = None
EOF
git add -f backend/alembic/fork_versions/f999_synthetic.py && git commit -q -m "synthetic: a fork migration"
expect "record: downgrade to the previous fork head" 0 "downgrade fork@$prev_head\`" -- "$record" v0.22.0-custom.3 --commit HEAD
expect "record: previous release is the newest older tag" 0 'Previous release: `v0.22.0-custom.1`' -- \
  "$record" v0.22.0-custom.3 --commit HEAD
expect "record: annotated and multi-line ids parse" 0 "^f999 \\['a', 'b'\\]$" -- python3 -c '
import sys; sys.path.insert(0, "fork/release"); import record
print(*record.migration_ids("revision: str = \"f999\"\ndown_revision: tuple = (\n    \"a\",  # \"x\"\n    \"b\",\n)\n"))'
# A release that moves upstream's line (a sync): only the restore applies.
cat >"backend/alembic/versions/999_synthetic.py" <<EOF
revision = "999"
down_revision = "131"
branch_labels = None
depends_on = None
EOF
git add -f backend/alembic/versions/999_synthetic.py && git commit -q -m "synthetic: an upstream migration"
expect "record: upstream migration means restore only" 0 "a downgrade cannot take it back" -- \
  "$record" v0.22.0-custom.4 --commit HEAD
expect "record: patch list read from UPSTREAM.md" 0 '\| Fork migration line \(D-003\) \| #1 \|' -- \
  "$record" v0.22.0-custom.4 --commit HEAD

exit $fail
