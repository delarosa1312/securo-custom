// The update banner compares the running version with this fork's newest release, not upstream's: upstream
// images do not start on a database with fork migrations, and semver sorts v0.22.0-custom.1 below v0.22.0.
export const RELEASE_REPOSITORY = 'delarosa1312/securo-custom'

export const LATEST_RELEASE_URL = `https://api.github.com/repos/${RELEASE_REPOSITORY}/releases/latest`
