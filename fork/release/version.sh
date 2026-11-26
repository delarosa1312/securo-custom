# Release version helpers, sourced by fork/release/*.sh.
# A release version is v<upstream X.Y.Z>-custom.<n>, n from 1, no leading zeros, nothing after it.

# Prints the upstream base tag (v0.22.0 for v0.22.0-custom.1); fails for anything else.
release_upstream_base() {
  [[ ${1:-} =~ ^(v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*))-custom\.([1-9][0-9]*)$ ]] || return 1
  echo "${BASH_REMATCH[1]}"
}
