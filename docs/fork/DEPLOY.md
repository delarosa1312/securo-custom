# Build, deploy, back up, recover

Status: the facts below were read from upstream `v0.22.0`. The release workflow, the Coolify files and the
rehearsed procedures are delivered by the release issue; sections marked "pending" are filled in there. Matheus
operates Coolify: nothing here assumes access to it.

## What runs in production

One backend image serves every service that runs backend code, so a custom release must replace all of them at
once with the same tag.

| Service | Image | Command | Notes |
| --- | --- | --- | --- |
| `db` | `pgvector/pgvector:pg16` | | volume `pgdata` |
| `redis` | `redis:8-alpine` | | no volume |
| `migrate` | backend | `alembic upgrade head` (ours: `upgrade heads`) | one-shot, runs before the rest |
| `backend` | backend | `uvicorn app.main:app` on 8000 | health: `GET /api/health` |
| `celery-worker` | backend | `celery -A app.worker worker --concurrency=2` | |
| `celery-beat` | backend | `celery -A app.worker beat` | exactly one instance |
| `mcp-server` | backend | `uvicorn mcp_server.main:app` on 8765 | Compose profile `agents` |
| `plugins-runner`, `plugins-egress` | backend | plugin sandbox and proxy | Compose profile `plugins`, only if used |
| `frontend` | frontend | nginx on 8080 | needs `BACKEND_URL` |

Every backend-image service except `migrate` refuses to start unless the database is at the migration heads
(`alembic current --check-heads`), so an image and a database that disagree fail closed.

## Images and versions

- Registry: `ghcr.io/delarosa1312/securo-backend` and `ghcr.io/delarosa1312/securo-frontend` (public, built from
  this public repository, which also satisfies the AGPL source offer: the running version links to its tag).
- Tag: `v<upstream>-custom.<n>`, for example `v0.22.0-custom.1`. Deploy by tag or digest, never `latest`.
- The frontend image is built with `VITE_APP_VERSION=<tag>`; without it the build asks upstream for a version.
- Each release records: upstream base tag, source commit, patch list (from `UPSTREAM.md`), image digests.

Release record: pending (first release).

## Local stack with synthetic data

Pending (`fork/tools/stack.sh`): Compose project of its own, own ports and volumes, seeded by
`backend/scripts/seed_perf.py` or a fork seed. Never production volumes, secrets or data.

## Coolify

Pending: staging configuration, the minimal production override (image names and tags only), health checks,
resource limits, registry access, and the exact clicks or API calls for Matheus.

What must be preserved untouched: the production database, users, workspaces, transactions, accounts, recurring
schedules, attachments, volumes, secrets, domain, encryption keys (`SECRET_KEY`, `AGENTS_MCP_JWT_SECRET`), and the
MCP configuration. The financial migration is not repeated.

## Backups

- Securo's managed backup is a logical export per workspace (JSON in a ZIP, optional password): about 45
  workspace tables and attachments. It does not contain users, instance settings, secrets or agent data, and it
  does not record the migration revision. It is a second line, not the rollback path.
- The rollback path is a PostgreSQL dump of the whole database (`pg_dump -Fc`) plus a copy of the attachments
  volume, taken right before each deploy. Procedure and restore rehearsal: pending.
- New fork tables that carry `workspace_id` must be added to the backup coverage list (upstream's guard fails
  otherwise), so managed backups keep including our data.

## Rollback

Reverting image tags alone is not enough once a migration ran: the older image checks the database heads and will
not start against a newer schema. A rollback is one of:

1. `alembic downgrade` of the fork migrations added by the release, run with the new image, then the old tag
   (possible because fork migrations are additive and their downgrades are tested); or
2. restore the pre-deploy dump, then the old tag (loses whatever was entered after the dump).

Each release note states which applies and the exact commands. Rehearsal on isolated test data: pending.

## Approvals

Staging first. Production deployment needs Matheus's explicit yes after staging verification and a fresh backup.
