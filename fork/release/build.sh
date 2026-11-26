#!/usr/bin/env bash
# Build (and with --push publish) the custom backend and frontend images for one release.
# The images are built from `git archive` of the commit, never from the working tree, so nothing untracked
# (configuration, caches, node_modules) can reach an image.
#
# Usage: fork/release/build.sh <version> [--commit <rev>] [--push | --load | --check] [--platforms <list>]
#   <version>    v<upstream X.Y.Z>-custom.<n>, for example v0.22.0-custom.1 (also the image tag, verbatim)
#   --commit     what to build; default the commit the tag <version> points to. With --push the tag must
#                exist and point to it.
#   --push       build every platform and push to the registry (CI, on a release tag)
#   --load       build one platform and load it into the local Docker (default; local proof and rehearsal)
#   --check      build every platform, keep nothing (CI dry run)
#   --platforms  default linux/amd64,linux/arm64 for --push and --check, the host platform for --load
# Environment: FORK_IMAGE_PREFIX (default ghcr.io/delarosa1312), FORK_IMAGE_SUFFIX (appended to the local tag
# with --load, for example -local), FORK_RELEASE_OUT (where images.env is written, default the current folder),
# FORK_RELEASE_BASE_REF (the branch releases come from, default origin/main).
# Takes the heavy lock of fork/tools/test.sh and stack.sh, so it never builds alongside another heavy run.
set -euo pipefail
root="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
repo_url="https://github.com/delarosa1312/securo-custom"
prefix="${FORK_IMAGE_PREFIX:-ghcr.io/delarosa1312}"
base_ref="${FORK_RELEASE_BASE_REF:-origin/main}"
out_dir="${FORK_RELEASE_OUT:-$PWD}"

die() { echo "build.sh: $*" >&2; exit 1; }

version="${1:-}"
[[ -n $version ]] || { sed -n '2,19p' "$0" >&2; exit 2; }
shift
commit_arg="" mode="load" platforms=""
while (($#)); do
  case "$1" in
    --commit) commit_arg="${2:?--commit needs a revision}"; shift 2 ;;
    --push) mode="push"; shift ;;
    --load) mode="load"; shift ;;
    --check) mode="check"; shift ;;
    --platforms) platforms="${2:?--platforms needs a list}"; shift 2 ;;
    *) die "unknown argument $1" ;;
  esac
done

source "$root/fork/release/version.sh"
upstream_base="$(release_upstream_base "$version")" || die "version must look like v0.22.0-custom.1, got '$version'"

if [[ -n $commit_arg ]]; then
  commit="$(git -C "$root" rev-parse --verify "$commit_arg^{commit}")" || die "no commit $commit_arg"
else
  commit="$(git -C "$root" rev-parse --verify -q "refs/tags/$version^{commit}")" ||
    die "tag $version does not exist; pass --commit to build another revision"
fi
if [[ $mode == push ]]; then
  tagged="$(git -C "$root" rev-parse --verify -q "refs/tags/$version^{commit}")" || die "--push needs the tag $version"
  [[ $tagged == "$commit" ]] || die "tag $version points to $tagged, not $commit"
fi
git -C "$root" merge-base --is-ancestor "$commit" "$base_ref" || die "$commit is not on $base_ref"
git -C "$root" rev-parse --verify -q "refs/tags/$upstream_base^{commit}" >/dev/null ||
  die "upstream tag $upstream_base is missing (git fetch upstream --tags)"
git -C "$root" merge-base --is-ancestor "$upstream_base" "$commit" ||
  die "$commit does not contain upstream $upstream_base: the version names the wrong base"

case "$mode" in
  load)
    platforms="${platforms:-linux/$(docker version --format '{{.Server.Arch}}')}"
    [[ $platforms != *,* ]] || die "--load takes one platform"
    output=(--load)
    tag_suffix="${FORK_IMAGE_SUFFIX:-}"
    ;;
  push) platforms="${platforms:-linux/amd64,linux/arm64}"; output=(--push); tag_suffix="" ;;
  check) platforms="${platforms:-linux/amd64,linux/arm64}"; output=(--output type=cacheonly); tag_suffix="" ;;
esac

state_dir="${XDG_CACHE_HOME:-$HOME/.cache}/securo-custom"
tmp_root="${FORK_RELEASE_TMP:-${RUNNER_TEMP:-$state_dir}}"
mkdir -p "$out_dir" "$state_dir" "$tmp_root"
ctx="$(mktemp -d "$tmp_root/release-ctx.XXXXXX")"
trap 'rm -rf "$ctx"' EXIT
git -C "$root" archive "$commit" backend frontend | tar -x -C "$ctx"

exec 9>"$state_dir/heavy.lock"
if ! flock -n 9; then echo "another heavy run holds the lock; waiting..." >&2; flock 9; fi

created="$(git -C "$root" show -s --format=%cI "$commit")"
: >"$out_dir/images.env"
build() {
  local name="$1" image="$prefix/securo-$1:$version$tag_suffix" meta="$ctx/$1.metadata.json"
  shift
  echo "building $image ($platforms) from $commit" >&2
  docker buildx build "$ctx/$name" -f "$ctx/$name/Dockerfile" \
    --platform "$platforms" "${output[@]}" --metadata-file "$meta" --provenance=false \
    -t "$image" \
    --label "org.opencontainers.image.source=$repo_url" \
    --label "org.opencontainers.image.revision=$commit" \
    --label "org.opencontainers.image.version=$version" \
    --label "org.opencontainers.image.created=$created" \
    --label "org.opencontainers.image.licenses=AGPL-3.0" \
    --label "org.opencontainers.image.title=securo-$name" \
    --label "org.opencontainers.image.description=Securo Custom $name ($version, upstream $upstream_base)" \
    --label "io.github.delarosa1312.securo-custom.upstream-base=$upstream_base" \
    "$@"
  # With --push this is the registry digest of the image index; with --load it is the local image id.
  local digest
  digest="$(python3 -c 'import json,sys; m=json.load(open(sys.argv[1])); print(m.get("containerimage.digest",""))' "$meta")"
  local key="${name^^}"
  printf '%s_IMAGE=%s\n%s_DIGEST=%s\n' "$key" "$image" "$key" "$digest" >>"$out_dir/images.env"
}
build backend
build frontend --build-arg "VITE_APP_VERSION=$version"
printf 'VERSION=%s\nCOMMIT=%s\nUPSTREAM_BASE=%s\nMODE=%s\nPLATFORMS=%s\n' \
  "$version" "$commit" "$upstream_base" "$mode" "$platforms" >>"$out_dir/images.env"
cat "$out_dir/images.env"
