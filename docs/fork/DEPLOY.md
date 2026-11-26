# Build, deploy, back up, recover

Matheus operates Coolify; nothing here assumes access to it from a session. Facts were read from upstream
`v0.22.0` and rehearsed locally on synthetic data (#2). Coolify labels below follow its documentation and may
differ slightly between Coolify versions. Approvals: staging first; production only with Matheus's explicit yes
after staging verification and a fresh backup, deployed by him.

## What runs in production

One backend image serves every service that runs backend code, so a custom release replaces all of them at once
with the same tag.

| Service | Image | Command | Notes |
| --- | --- | --- | --- |
| `db` | `pgvector/pgvector:pg16` | | volume `pgdata` |
| `redis` | `redis:8-alpine` | | no volume |
| `migrate` | backend | `alembic upgrade heads` (stock `v0.22.0`: `upgrade head`) | one-shot, runs before the rest |
| `backend` | backend | `uvicorn app.main:app` on 8000 | health: `GET /api/health` |
| `celery-worker` | backend | `celery -A app.worker worker --concurrency=2` | |
| `celery-beat` | backend | `celery -A app.worker beat` | exactly one instance |
| `mcp-server` | backend | `uvicorn mcp_server.main:app` on 8765 | Compose profile `agents` upstream |
| `plugins-runner`, `plugins-egress` | backend | plugin sandbox and proxy | Compose profile `plugins`, only if used |
| `frontend` | frontend | nginx on 8080 | needs `BACKEND_URL` |

Every backend-image service except `migrate` refuses to start unless the database is at the migration heads
(`alembic current --check-heads`), and `migrate` refuses a database that carries a revision its image does not
know. An image and a database that disagree fail closed (rehearsed, step 4 below).

## Images and versions

- Registry: `ghcr.io/delarosa1312/securo-backend` and `ghcr.io/delarosa1312/securo-frontend`, public: no pull
  credentials anywhere. The repository is public too, which serves the AGPL source offer: each image carries
  `org.opencontainers.image.source` and `.revision` labels, the app's sidebar shows the version, and the release
  page links the exact source.
- Version and image tag: `v<upstream>-custom.<n>`, verbatim with the `v`, for example `v0.22.0-custom.1`. `n`
  starts at 1 for each upstream base. Never `latest`: no `latest` tag is ever pushed.
- The frontend is built with `VITE_APP_VERSION=<version>`, so the sidebar shows `v0.22.0-custom.1`.
- Architectures: `linux/amd64` and `linux/arm64`, as upstream.

### Releasing (coordinator)

1. `main` holds what ships; checks, tests and E2E are green on it.
2. Tag the merge commit on `main` and push the tag: `git tag -a v0.22.0-custom.1 -m v0.22.0-custom.1 <commit>`,
   `git push origin v0.22.0-custom.1`.
3. `.github/workflows/fork-release.yml` runs `fork/release/build.sh <version> --push` (refuses a version that is
   not `v<X.Y.Z>-custom.<n>`, a commit not on `main`, or a commit that does not contain upstream tag
   `v<X.Y.Z>`; builds from `git archive` of the commit, so nothing untracked reaches an image), then
   `fork/release/record.py` for the record, then publishes the GitHub release (not a prerelease) with the record
   as notes.
4. First release only: GHCR may create the two packages as private. In github.com > Your profile > Packages >
   `securo-backend` > Package settings > Danger Zone > Change visibility > Public; the same for
   `securo-frontend`. Check from any machine with no login: `docker buildx imagetools inspect
   ghcr.io/delarosa1312/securo-backend:<version>` prints the digests.
5. Commit the record: `fork/release/record.py <version> --registry > docs/fork/releases/<version>.md` (digests
   read anonymously from GHCR), in a small pull request.

Dry run: Actions > Fork release > Run workflow (branch `main`, a version label) builds both images for both
architectures and publishes nothing. Locally: `FORK_IMAGE_SUFFIX=-local fork/release/build.sh <version> --commit
HEAD` builds and loads the host's architecture under `...:<version>-local`.

### Release record

`docs/fork/releases/<version>.md`, also the GitHub release notes: upstream base and its commit, source commit,
migration heads (upstream's and ours), the image digests, the previous release and the exact rollback commands
(or that only the restore applies), and the patches carried (from the inventory in `UPSTREAM.md`). Example of
the rollback section for the first release:

```text
Previous release: stock ghcr.io/securo-finance/securo-backend:0.22.0.
Rollback A (keeps data entered since the deploy), with this release's image, then the previous images:
  alembic downgrade fork@f000; alembic downgrade f000-1
Rollback B: restore the pre-deploy dump and volumes, then deploy the previous images.
```

## Deploy files (`fork/deploy/`)

| File | What |
| --- | --- |
| `production.override.yml` | on top of `docker-compose.prod.yml`: the image of every backend service and the frontend, tag `${SECURO_CUSTOM_TAG:?}` (required: an empty tag stops the deploy instead of pulling something else). Nothing else. |
| `staging.override.yml` | on top of the production override: health checks for `frontend`, `celery-worker`, `mcp-server`; memory and CPU limits; no host ports |
| `coolify/production.yml`, `coolify/staging.yml` | the single files Coolify takes, rendered by `render.py`: anchors expanded, `x-` fields dropped, `mcp-server` without its profile (Coolify starts no profiles), plugin services left out. Never edit them; `check.sh` fails when they are stale |
| `rehearsal.override.yml`, `rehearse.sh`, `rehearsal_probe.py` | the local rehearsal below |

Compose CLI equivalent: `docker compose -f docker-compose.prod.yml -f fork/deploy/production.override.yml up -d`.
With plugins with code: add `--profile plugins`; the override already gives both plugin services our image.
Production does not use them; to add them in Coolify later, paste the two services from
`docker-compose.prod.yml` with our image line.

### Health checks and limits (staging)

Measured on the local rehearsal (synthetic data, idle, then three 24-month Cash Flow requests): backend 182 MiB,
celery-worker 243 MiB, celery-beat 137 MiB, mcp-server 116 MiB, db 21 to 44 MiB, redis 7 to 26 MiB, frontend
15 MiB. Limits assume one user, a few years of transactions and the built-in `native` embeddings (a model of about
0.5 GB loaded on demand by the backend and the worker):

| Service | Memory | CPUs | Health check |
| --- | --- | --- | --- |
| db | 1 GiB | 1.0 | upstream's `pg_isready` |
| redis | 128 MiB | 0.25 | upstream's `redis-cli ping` |
| migrate | 512 MiB | 1.0 | one-shot |
| backend | 1.5 GiB | 1.0 | upstream's `GET /api/health` |
| celery-worker | 1.5 GiB | 1.0 | `celery inspect ping` of itself, every 2 minutes |
| celery-beat | 384 MiB | 0.25 | none (no cheap probe; `restart: unless-stopped`) |
| mcp-server | 512 MiB | 0.5 | `GET /health` on 8765 |
| frontend | 128 MiB | 0.5 | `wget --spider` on 8080 |

A service over its memory limit is killed and restarted by Docker. Production keeps upstream's settings (no
limits) until staging has run on these numbers; adopting them later is a separate change to the production
override.

## Rehearsal (local, synthetic, before every release that changes migrations)

`fork/deploy/rehearse.sh` runs one Compose project, `securo-custom-rehearsal`, whose volumes survive every switch of
images, like a Coolify resource's volumes survive a redeploy (loopback ports 23000, 28000, 28765; never production
data). The #2 rehearsal (main `e4d38e4b`, local images of `v0.22.0-custom.1`), transcript in the pull request:

```bash
fork/deploy/rehearse.sh stock 0.22.0                     # 1. upstream's own v0.22.0 compose and images
fork/deploy/rehearse.sh seed && fork/deploy/rehearse.sh snapshot stock
fork/deploy/rehearse.sh backup predeploy                 # 2. pg_dump -Fc + attachments and agent-knowledge volumes
fork/deploy/rehearse.sh custom v0.22.0-custom.1-local    # 3. coolify/production.yml: migrate runs 131 -> f000 -> f001
fork/deploy/rehearse.sh snapshot custom1 && fork/deploy/rehearse.sh compare custom1
fork/deploy/rehearse.sh stock 0.22.0                     # 4. old tag without downgrade: migrate fails, nothing starts
fork/deploy/rehearse.sh custom v0.22.0-custom.1-local    # 5. rollback A
fork/deploy/rehearse.sh alembic downgrade fork@f000 && fork/deploy/rehearse.sh alembic downgrade f000-1
fork/deploy/rehearse.sh stock 0.22.0 && fork/deploy/rehearse.sh snapshot rollback-a && fork/deploy/rehearse.sh compare rollback-a
fork/deploy/rehearse.sh restore predeploy                # 7. rollback B (after upgrading again), then stock
fork/deploy/rehearse.sh down
```

Results: after the upgrade, every one of 70 tables had the same rows and checksum (upstream columns), REST accounts
and Cash Flow equal, the attachment byte-identical, a login token issued on stock still accepted (the generated
`SECRET_KEY` lives in the database and survived), fork features answered, the bundle carried
`v0.22.0-custom.1`. Old tag without downgrade: `Can't locate revision identified by 'f001'`, every service left in
`Created`, API unreachable. Rollback A: `f001 -> f000 -> 131`, stock started, data equal; a transaction entered on
the custom release survived it, the F-001 switch set on it did not (its column is dropped). Rollback B: database and
volumes equal to the backup, the entry made after the deploy gone, as expected. The staging file came up with every
health check healthy.

## Coolify

Production today (Matheus, 2026-11-26): a Coolify Service with a pasted compose ("Edit Compose File"), on Coolify's
own host, with sub-resources `migrate`, `backend`, `frontend`, `celery-worker`, `celery-beat`, `mcp-server`, `db`,
`redis`; no plugin services; status "Running (no healthcheck)". Never create a new resource for production: a new
resource starts on new, empty volumes. Production is always that Service, edited in place.

### Before the first change (a look, no edits)

1. Open the Service > Edit Compose File, copy the whole compose into a private file on your machine (it is the
   stock rollback; it holds variable names, not their values, but keep it out of repositories and chats anyway).
2. Compare it with upstream's, in this repository: `diff -u <(git show v0.22.0:docker-compose.prod.yml)
   ~/securo-compose-before.yml`. Edits of your own (pinned tags, a removed `profiles:` line, ports, labels) stay
   as they are. The lines to change are the ones this prints:
   `grep -nE 'image: ghcr.io/securo-finance/|upgrade head' ~/securo-compose-before.yml`.
3. MCP runs today: if `mcp-server` in that file still has `profiles: [agents]`, this Coolify starts profiled
   services anyway; either way leave that line as it is.
4. The CPU, only if single-architecture builds ever matter: over SSH `uname -m` (`x86_64` is amd64, `aarch64`
   arm64). Releases build both.
5. The Service's UUID (in its Coolify URL) is `U` in the SSH commands below.

### Staging (a new Service, synthetic data only)

1. Project > + New > Docker Compose Empty, paste `fork/deploy/coolify/staging.yml`, Save.
2. Environment Variables (Coolify lists `SECURO_CUSTOM_TAG` as required and will not deploy without it), all new
   values, nothing copied from production: `SECURO_CUSTOM_TAG=<version>`, `POSTGRES_PASSWORD`
   (`openssl rand -hex 24`), `SECRET_KEY` (`openssl rand -hex 32`), `AGENTS_ENABLED=true`,
   `FRONTEND_URL=https://<staging domain>`, `REGISTRATION_ENABLED=true` (only until the seed below), and if the
   Traefik labels are used: `TRAEFIK_RESOURCE_NAME=securo-staging` (must differ from production's).
3. Domain: the `frontend` sub-resource, `https://<staging domain>`, port 8080. Deploy.
4. Seed synthetic data from your machine: `python3 fork/stack/seed.py --base-url https://<staging domain>`
   (login `e2e@example.com`, staging only). Then set `REGISTRATION_ENABLED=false` and restart.
5. Verify as below, plus every sub-resource healthy (staging has health checks on all but `celery-beat`).

Alternative for staging: a Git-based Docker Compose application, public repository
`https://github.com/delarosa1312/securo-custom`, branch `main`, Docker Compose Location
`/fork/deploy/coolify/staging.yml`; it re-reads the file on each deploy.

### Production deploy (the existing Service, edited in place)

1. Backup (next section). Do not continue without a dump that passed its check.
2. Note three numbers on the dashboard (available balance, net worth, this month's spending) and open one
   attachment, to compare afterwards.
3. Edit Compose File, change only the lines step 2 of "Before the first change" printed, keeping every service and
   volume name:
   - every `image: ghcr.io/securo-finance/securo-backend:<tag>` (the `x-backend-base` anchor, which `migrate`,
     `backend`, `celery-worker`, `celery-beat` and `mcp-server` share, plus any service that names the image
     itself) becomes `image: ghcr.io/delarosa1312/securo-backend:${SECURO_CUSTOM_TAG:?}`;
   - `image: ghcr.io/securo-finance/securo-frontend:<tag>` becomes
     `image: ghcr.io/delarosa1312/securo-frontend:${SECURO_CUSTOM_TAG:?}`;
   - in `migrate`, `command: upgrade head` becomes `command: upgrade heads` (needed from the first upstream sync on).
   Save. If your file was identical to upstream's in step 2, pasting `fork/deploy/coolify/production.yml` instead
   is equivalent.
4. Environment Variables: set `SECURO_CUSTOM_TAG=v0.22.0-custom.1`. Change nothing else: not `SECRET_KEY`,
   `AGENTS_MCP_JWT_SECRET`, `POSTGRES_PASSWORD`, the domain, the OIDC, storage or MCP settings.
5. Deploy (Redeploy). Each sub-resource row shows `ghcr.io/delarosa1312/...:v0.22.0-custom.1`. In Logs, `migrate`
   shows `Running upgrade 131 -> f000` and `f000 -> f001` and exits 0; then every other service starts.
6. Verify (below). If anything is wrong: Rollback A.

### Verify

- Every sub-resource running (`migrate` exited 0); `backend` healthy.
- Terminal > `backend` > `alembic current` prints `f001 (head)` (or the fork head in the release record).
- The sidebar shows the release version. You are still logged in (existing sessions survive).
- The three dashboard numbers are unchanged; the attachment opens.
- An external MCP client (Claude Desktop, n8n) still connects with its existing token.
- The release's features: Accounts > edit an account > "include in available balance"; Reports > Cash Flow >
  budget-aware mode.

## Backups

On the Coolify host over SSH, right before every production deploy. The dump holds everything in the database,
password hashes and the instance secrets included: keep it private, on the host and on your own machine, never
in a repository or a chat.

```bash
U=<resource uuid>
mkdir -p ~/securo-backups && cd ~/securo-backups
docker ps --filter "label=com.docker.compose.project=$U" --format '{{.Label "com.docker.compose.service"}}  {{.Names}}'
DB=$(docker ps -q --filter "label=com.docker.compose.project=$U" --filter "label=com.docker.compose.service=db")
F=securo-$(date +%Y%m%d-%H%M)-before-v0.22.0-custom.1
docker exec "$DB" pg_dump -U postgres -Fc securo > "$F.dump"
docker exec -i "$DB" pg_restore --list < "$F.dump" | grep -c "TABLE DATA"    # about 70; 0 or an error: stop
docker volume ls --filter "label=com.docker.compose.project=$U"                # names of attachments, agent_knowledge
for v in attachments agent_knowledge; do
  vol=$(docker volume ls -q --filter "label=com.docker.compose.project=$U" | grep "_$v\$")
  docker run --rm -v "$vol:/src:ro" --entrypoint tar pgvector/pgvector:pg16 -C /src -czf - . > "$F-$v.tgz"
done
ls -l "$F"*
```

If the first `docker ps` lists nothing, this Coolify does not use the UUID as the Compose project name: find the
containers with `docker ps --format '{{.Names}}' | grep "$U"` and the volumes with `docker volume ls | grep "$U"`,
and use those names instead of the filters. Then copy the files to your machine (`scp`). `pgdata` itself is not copied: the dump is the database backup.
`agent_embedding_models` is a download cache and needs no backup.

Where attachments live: the `backend` sub-resource's Persistent Storage tab lists an `attachments` volume when
they are stored locally; copy it as above. To see the storage setting without printing any other value:
`docker exec $(docker ps -q --filter "label=com.docker.compose.project=$U" --filter
"label=com.docker.compose.service=backend") sh -c 'echo "${STORAGE_PROVIDER:-local}"'` prints `local` (the volume
holds them) or `s3` (they are in the bucket: skip `attachments`, the bucket is untouched by a deploy).

Second and third lines, not the rollback path: Coolify's scheduled backups of the `db` component (PostgreSQL in a
compose resource is supported; it reads `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`), and Securo's managed
backup (a ZIP per workspace: about 45 workspace tables and attachments; no users, instance settings, secrets or
agent data, no migration revision). New fork tables that carry `workspace_id` must be added to upstream's backup
coverage list, so managed backups keep including our data.

## Rollback

Reverting the tag alone does not work once a migration ran: the old image's `migrate` stops on the unknown fork
revision and nothing starts (rehearsed). If that happens, put the new tag back, redeploy, then roll back properly.
The release record says which rollback applies and gives the exact commands.

### A: downgrade the fork line, then the previous images (keeps data entered since the deploy)

Only when the release brought no upstream migration (the record says so). Loses what lives only in fork
columns and tables of the removed migrations (for `f001`: the per-account "include in available balance"
switches); everything else entered since the deploy stays.

1. With the new release still running: Terminal > `backend` container:
   `alembic downgrade fork@<previous fork head>`; back to stock additionally `alembic downgrade f000-1` (removes
   the empty fork root, leaving upstream's `131`). Never `alembic downgrade fork@base`: it walks into upstream's
   line (D-003). `alembic current` must then show the previous release's heads. Without a Terminal tab, over
   SSH: `docker exec $(docker ps -q --filter "label=com.docker.compose.project=$U" --filter
   "label=com.docker.compose.service=backend") alembic downgrade fork@<previous fork head>`.
2. Right away (the running new images now see a schema without their columns): previous custom release: set
   `SECURO_CUSTOM_TAG` to it and Redeploy; back to stock: Edit Compose File, paste your private copy of the stock compose, Save,
   remove `SECURO_CUSTOM_TAG`, Redeploy.
3. Verify; `migrate` exits 0 with nothing to do.

### B: restore the pre-deploy dump (anything entered after the dump is lost)

```bash
U=<resource uuid>; F=<the backup name>
for s in frontend mcp-server celery-beat celery-worker backend; do
  docker stop $(docker ps -q --filter "label=com.docker.compose.project=$U" --filter "label=com.docker.compose.service=$s")
done
DB=$(docker ps -q --filter "label=com.docker.compose.project=$U" --filter "label=com.docker.compose.service=db")
docker exec "$DB" dropdb -U postgres --force securo
docker exec "$DB" createdb -U postgres securo
docker exec -i "$DB" pg_restore -U postgres -d securo --exit-on-error < "$F.dump"
for v in attachments agent_knowledge; do
  vol=$(docker volume ls -q --filter "label=com.docker.compose.project=$U" | grep "_$v\$")
  docker run --rm -i -v "$vol:/dst" --entrypoint sh pgvector/pgvector:pg16 -c 'find /dst -mindepth 1 -delete && tar -C /dst -xzf -' < "$F-$v.tgz"
done
```

Then, in Coolify, put back the previous images (previous tag, or the stock compose) and Redeploy. Verify against
the numbers noted before the deploy.

## Upgrade checklist (every release)

1. Read the release record: migrations, which rollback applies, patches.
2. Rehearse locally when the release adds a migration (`rehearse.sh`, the steps above, with the previous and the
   new images).
3. Staging: set its `SECURO_CUSTOM_TAG`, Redeploy, Verify.
4. Matheus's yes for production.
5. Backup and its check; copy off the host; note the dashboard numbers.
6. Production: `SECURO_CUSTOM_TAG`, Redeploy, Verify.
7. Keep the backup until the next release has run a week.

## What stays untouched

The production database, users, workspaces, transactions, accounts, recurring schedules, attachments, every
volume and its name, every service name, the environment and secrets (`SECRET_KEY`, `AGENTS_MCP_JWT_SECRET`,
`POSTGRES_PASSWORD`, OIDC and provider keys), the domain, the MCP configuration and its issued tokens. No data
migration is repeated; the fork's migrations only add.

## Update banner

The sidebar banner and the update dialog read this fork's latest GitHub release
(`delarosa1312/securo-custom/releases/latest`, Matheus 2026-11-26), so they show only when a newer custom release
exists and link to its release notes. That is why releases are published as non-prereleases. Semver orders them
by upstream base, then by custom number: `v0.22.0-custom.2` above `.1`, `v0.23.0-custom.1` above
`v0.22.0-custom.3`. The dialog's upgrade command is still upstream's (`git pull && docker compose up -d --build`);
on Coolify the upgrade is the checklist above.
