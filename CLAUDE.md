# Securo Custom

A small downstream fork of [Securo](https://github.com/securo-finance/securo) (AGPL-3.0) that adds forward-looking
financial planning. We extend Securo; we do not build another finance app. Success is a shrinking patch set.

Read first: `AGENTS.md` (upstream source map and the rules the code keeps, never edit it), then the file below
that matches your task. GitHub issues on `delarosa1312/securo-custom` are the spec for each feature.

| Need | File |
| --- | --- |
| What we are building and what the money words mean | `docs/fork/PRODUCT.md` |
| How upstream works, where fork code goes, shared-file map | `docs/fork/ARCHITECTURE.md` |
| Order of work for an issue, briefs, reviews, permissions | `docs/fork/PROCESS.md` |
| Syncing with upstream, patch inventory | `docs/fork/UPSTREAM.md` |
| Images, Coolify, backups, rollback | `docs/fork/DEPLOY.md` |
| Why we decided what | `docs/fork/DECISIONS.md` |

## Layout

- Base: upstream `v0.22.0`. `main` = our integration branch. `upstream-main` = pristine mirror, never commit to it.
- Remotes: `origin` = the fork, `upstream` = official Securo (push URL disabled on purpose).
- Stack: FastAPI, SQLAlchemy async, Alembic, Celery on Redis in `backend/`; React 19, TypeScript, Vite in
  `frontend/`; MCP server in `backend/mcp_server/`; PostgreSQL with pgvector.
- Fork-only paths: `docs/fork/`, `fork/`, `backend/app/fork/`, `backend/alembic/fork_versions/`,
  `backend/mcp_server/tools/fork_*.py`, `backend/tests/fork/`, `frontend/src/fork/`, `.github/workflows/fork-*.yml`.
- `CLAUDE.md` and `.claude/` are in upstream's `.gitignore`: commit them with `git add -f`, do not edit `.gitignore`.

## Fork rules

1. Prefer, in order: native feature, plugin or extension point, isolated fork module, small upstream-compatible
   hook, larger core change (needs a line in `DECISIONS.md`). If upstream already does it, document it instead.
2. Logic lives in the fork-only paths. An upstream file gets the smallest hook that calls into them, and every
   hook is listed in the patch inventory in `docs/fork/UPSTREAM.md` in the same pull request.
3. Upstream defaults stay. A fork feature that changes a number someone sees is off until switched on, and with
   it off the response is identical to upstream's.
4. Money is `Decimal`, never `float`; "today" is `app_today()`; every query filters by workspace (see `AGENTS.md`).
5. Migrations: never edit an applied one and never add a numbered file to `backend/alembic/versions/` (that line
   is upstream's). Ours go in `backend/alembic/fork_versions/`; the coordinator assigns the id in the brief.
6. Forecast figures are virtual: no feature writes ledger rows to represent an estimate.
7. Every new capability has REST and MCP access; MCP writes use the existing `propose_*` plus `apply` pattern.
8. Strings: English and pt-BR, in the fork locale bundles (`frontend/src/fork/locales/`).
9. Synthetic data only, everywhere: tests, fixtures, screenshots, issues, commits. Never personal accounts,
   categories, amounts, production configuration or employer information. The repository is public.
10. No unrelated refactoring, formatting, dependency upgrades or renames in upstream files.
11. Never rebase or force-push `main` or `upstream-main`. Never push to `upstream`. No upstream issue, comment or
    pull request without Matheus's yes.
12. Never read `.env`, `secrets/` or any credential file. Never mount production volumes.

## Commands

```bash
fork/tools/check.sh [backend|frontend|migrations]  # ruff, ty, both migration chains, eslint, tsc; only failures
fork/tools/test.sh backend [pytest args]    # capped workers, one heavy run at a time across sessions
fork/tools/test.sh frontend [vitest args]
fork/tools/new-branch.sh <issue> <slug>     # issue-linked branch from origin/main, switched to in this worktree
fork/tools/stack.sh up|seed|status|logs|down  # local synthetic stack: :13000 app, :18000 API, :18765 MCP
python3 fork/tools/sync_upstream.py [--apply]  # upstream release sync proposal (UPSTREAM.md); dry run by default
cd fork/e2e && FORK_E2E_OUT=~/scratch/securo-custom/<issue>/e2e npx playwright test   # E2E (PROCESS.md)
```

Migrations: `alembic upgrade heads` (two heads after a sync); undo ours only with `alembic downgrade fork@<rev>`,
never `fork@base` (it walks into upstream's line).

Never run bare `pytest -n auto`, `npm test` or `docker compose up --build` in parallel with other sessions: the
machine has 20 cores and 31 GB shared by every session. Use the wrappers; they queue on one lock.
First time in a checkout: `cd backend && uv sync --all-extras`, `cd frontend && npm ci`, `cd fork/e2e && npm ci`.

## Sessions

- One issue, one session, one worktree, one scoped pull request to `main`. The coordinator session integrates.
  Worktrees: `.worktrees/<issue>-<slug>` in the main checkout, which stays on `main`.
- Follow the order of work in `docs/fork/PROCESS.md`; a brief's "done" never drops a step.
- Notes live in the memory folder `~/.claude/projects/-home-Unified-Documents-Projects-Personal-securo-custom/memory/`
  (`MEMORY.md`, `active-work.md`, one `issue-<n>-<slug>.md` per issue). Keep your issue note current at each
  milestone so another session can resume from it.
- Deep review (the `deep-review` agent) before merge for anything that computes money, checks access or changes
  stored data. Lighter changes get one pattern-fit review.
- Authorized without asking, in this repository only: linked branches and worktrees, local commits, pushes to
  feature branches, scoped pull requests and issue status updates here. Ask Matheus for: scope changes, ambiguous
  financial behavior, user-visible changes the issue does not name, quality trade-offs, metered costs, credential
  access, destructive operations, anything upstream, production.
