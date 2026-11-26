# Decisions

Short records of fork-level choices. Upstream's own records are in `docs/adr/`; ours never go there.
Format: context, decision, consequence. Newest last. A changed decision gets a new entry that names the old one.

## D-001 Base on v0.22.0, not on upstream main (2026-11-26)

Production runs upstream `v0.22.0` (`bde1168f`). Upstream `main` was 38 commits ahead, including migrations `132`
and `133`. Starting from the production version keeps the first custom release free of an unrelated upstream
upgrade. `main` follows stable tags only; `upstream-main` mirrors upstream `main`.

## D-002 Fork code in fork-only paths, minimal hooks in upstream files (2026-11-26)

Upstream releases every few days. Each line we change in an upstream file is a possible conflict per release.
Logic goes in `backend/app/fork/`, `backend/mcp_server/tools/fork_*.py`, `frontend/src/fork/`, `fork/`; upstream
files get a call, an import or a column. Every hook is a row in the patch inventory (`UPSTREAM.md`).

## D-003 Fork migrations are a second Alembic branch (2026-11-26, verified on PostgreSQL in #1)

Upstream ADR 0010 keeps one numbered line and renumbers on collision. A numbered migration of ours would collide
with every upstream release and force edits to applied migrations. Ours live in
`backend/alembic/fork_versions/` with branch label `fork` and ids `f001...`; deploy runs `alembic upgrade heads`.
Cost: `alembic.ini` gains `version_locations`, the migrate command and the schema test use `heads`, and we keep a
check of our own line.

Outcome (#1, scratch `pgvector/pgvector:pg16`, upstream `132` and `133` brought in on a throwaway branch):
- Root `f000` is empty, `down_revision = "131"`, `branch_labels = ("fork",)`. Until upstream adds a migration
  after `131` there is one head (`f000`); from the first sync on there are exactly two (`133` and `f000`).
- With two heads, `alembic upgrade head` fails ("Multiple head revisions"); `upgrade heads` works on a fresh
  database, on one at `131`, on one at upstream `133` and on one built before the sync. So the migrate command and
  the two PostgreSQL tests that migrate (`test_schema_matches_migrations_postgres.py`,
  `test_agent_tool_name_lengths_postgres.py`) use `heads` now, not at the first sync.
- `alembic current --check-heads` passes with both rows in `alembic_version` and fails while either line is
  behind, so upstream's startup gate keeps working unchanged.
- Upstream's `check_migration_chain.py` reads only `versions/` and stays green; `fork/tools/check_fork_migrations.py`
  checks our line (in `check.sh`).
- Downgrade our line only: `alembic downgrade fork@<target>` (for example `fork@f000` removes every fork migration).
  Never `fork@base`: the branch's base is upstream's first migration, and it starts downgrading upstream's line.
- Not changed: the Helm chart (`charts/securo`) still runs `upgrade head`; we deploy with Compose. A Helm deploy of
  the fork would need the same one-word change.

## D-004 Fork strings in fork locale bundles (2026-11-26)

Upstream requires every key in all 20 locale files and tests it. Adding our keys there means 20 conflict-prone
files per feature. Fork strings live in `frontend/src/fork/locales/en.json` and `pt-BR.json`, merged into the
`translation` namespace by one hook in `lib/i18n.ts`; other languages fall back to English. A feature prepared
for upstream moves its keys into the 20 files at that point.

## D-005 Projections are virtual (2026-11-26)

Budget-derived and historical estimates are computed at read time and never stored as transactions. Existing
"provision" recurring expenses are a workaround to retire by a guided, previewed transition, never automatically.

## D-006 CLAUDE.md is force-added (2026-11-26)

Upstream ignores `CLAUDE.md` and `.claude/`. We track ours with `git add -f` and leave `.gitignore` identical to
upstream.

## D-007 No Dashing tooling here (2026-11-26)

Dashing's coordinator and quality tools carry Dashing assumptions. This repository has four small scripts in
`fork/tools/` and a written process (`PROCESS.md`); state lives in GitHub issues and the memory folder. No
registry or orchestration framework until a concrete need shows up twice.

## D-008 E2E runs on a local Compose stack with a REST seed and a Playwright package of our own (2026-11-26)

Upstream has no end-to-end suite. `fork/tools/stack.sh` runs upstream's `docker-compose.yml` plus
`fork/stack/compose.yml` (own project name, loopback ports, project volumes, no host secrets or backup folder,
agents profile on for MCP, a scrubbed environment) built from the worktree. The backend runs from its image, not
upstream's `./backend:/app` bind mount: with the mount, root-owned folders and caches landed in the worktree and
35 host backend tests then failed on permissions. `fork/stack/seed.py` loads a synthetic data set through the
public REST API (stdlib only), so the seed follows upstream's validation instead of writing rows directly;
`backend/scripts/seed_perf.py` was not enough (no budgets, pending rows, installments or transfers). Scenarios
live in `fork/e2e/` with their own `package.json` (Playwright, the machine's Chromium), so `frontend/package.json`
stays upstream's. A scenario drives the screen, calls REST and calls MCP as an external client, and saves
screenshots and JSON responses as evidence.

## D-009 Fork fields on an upstream response only when a fork option is on (2026-11-26, #5)

The budget-aware Cash Flow mode adds a trend figure, a summary figure and an explanation block to
`GET /api/reports/cash-flow`. Adding optional fields to upstream's `ReportResponse` would put `null` keys in every
response with the mode off. Instead the fork returns `CashFlowBudgetResponse`, a subclass of `ReportResponse`, only
when `budget_forecast=true`, and the route's response model is the union of both; with the option off the body is
upstream's byte for byte (tested). Query limits stay upstream's without the option (`months` 12, upstream's exact
422), wider only with it (24). Later fork options on upstream reports (F-003, F-006) follow the same pattern.
Known edges: the route's OpenAPI advertises `months` up to 24 and a union response for every caller, and when
`months` is out of range together with another invalid parameter the 422 lists only the other one (upstream lists
both); a request with only `months` out of range gets upstream's exact body.

## D-010 F-001: the dashboard keeps computing Available balance in the browser (2026-11-26)

Upstream sums the headline from `GET /api/accounts` in the browser. F-001 keeps that and adds one predicate to the
existing filter, instead of making the dashboard call the new server route: no new request, loading state or
layout change in an upstream page. The server figure (`app/fork/available_balance.py`) uses the same inputs and
the same rule (converted balance, or the native one with `fx_fallback` when no rate exists), and the fork tests
pin both to the issue's worked example so they cannot drift apart unnoticed. The route lives under
`/api/accounts` so the existing `accounts` token scope covers it; it is mounted before upstream's accounts
router because `/{account_id}` would otherwise take the path.

## D-013 Releases: our own images from a script, one tag variable, two rollbacks (2026-11-26, #2)

Production runs upstream's prebuilt images on Coolify. Our images replace every backend-image service and the
frontend at once, because they must agree on the migration heads. Choices:
- The release logic lives in `fork/release/` (`build.sh`, `record.py`, tested by `test.sh`), and
  `.github/workflows/fork-release.yml` only calls it: the fork had never run a workflow, and the same script
  proves itself locally. Images build from `git archive` of a commit on `main` that contains the upstream base
  tag, so nothing untracked reaches them. The image tag is the version verbatim (`v0.22.0-custom.1`); upstream's
  `type=semver` would treat `-custom.1` as a prerelease, and no `latest` is pushed.
- The production override changes images only, with one required variable `SECURO_CUSTOM_TAG` (`${...:?}`): a
  missing tag stops the deploy, and moving between custom releases is one variable. Coolify takes one compose
  file per resource, so `render.py` merges upstream's file with our overrides without interpolating into
  `fork/deploy/coolify/*.yml`, and `check.sh` fails when they are stale after an upstream sync.
- Health checks and limits start in staging only; production keeps upstream's settings until staging has run on
  them.
- Rollback A (keeps data): `alembic downgrade fork@<previous fork head>` with the new image, then the previous
  images; back to stock `alembic downgrade fork@f000` then `alembic downgrade fork@f000-1`, which removes the
  empty root. Always `fork@`: a bare `f000-1` resolves to `131` and Alembic then also downgrades every upstream
  migration above `131` (checked against Alembic's revision logic in the #2 deep review). Rollback B: restore the
  pre-deploy `pg_dump -Fc` (read to the end before anything is stopped) into a side database, swap it in, restore
  the volumes. `record.py` says which applies: a release that moved upstream's line since the previous release
  (or, for a return to stock, past the fork root's parent) can only be restored.
Rehearsed on synthetic data in #2 (DEPLOY.md, Rehearsal).

## D-014 Upstream sync: one script, a thin workflow, proposals only (2026-11-26, #3)

Upstream releases every few days and the fork must stay cheap to upgrade. `fork/tools/sync_upstream.py` (stdlib,
`git` and `gh`) holds all the logic and runs the same way by hand and in `.github/workflows/fork-upstream-sync.yml`,
which only schedules it. It merges stable release tags only, never upstream `main`; fast-forwards `upstream-main`
only with a plain push when a release is synced; trial-merges in a temporary detached worktree; and proposes a pull
request (clean) or an issue (conflicts, or a push GitHub refused). It never resolves a conflict, force-pushes,
merges, tags or deploys, and a dry run is the default. Overlaps are computed from git (files both upstream and
`main` changed since the base tag) and matched to the patch inventory, so a hook missing from the inventory shows up
as drift instead of being missed. The migration check upgrades a scratch database with `main` first and the merge
second, the deploy path. Security comes from upstream advisories first and release-note words second, since
`v0.19.0` fixed an advisory its notes do not mention.
The workflow runs in two jobs (review of #3): the release's own code (chain checks, uv, alembic) runs only in a
read-only job, and the write job runs git and `gh` only, redoing the merge and refusing unless its tree equals the
tested one, so a compromised release cannot use the write token before a person reviews it. Residual: the
migration results the write job publishes come from the job that ran the release's code; tree equality guards what
is pushed, not that text (the tool keeps it inert: mentions and markers defanged, fences closed). The read-only job
uses no cache, so the release's code cannot plant one that `main`'s CI restores. A dry run with failed checks fails
in the read-only job, since it has no write job. The tool owns only a
marked block of a proposal's body, so notes added by hand survive the daily run, and it lists files a person changed
on the sync branch instead of calling the merge untouched.
Consequences: with the default token most syncs need one local command, because GitHub refuses that token's pushes
of workflow-file changes (an optional `FORK_SYNC_TOKEN` removes the step; it is a secret of the `fork-sync`
environment limited to `main`, because a repository secret would reach the release's own workflows on the pushed
sync branch); upstream workflow files run in the fork once merged, so the report lists them for review; while "Allow GitHub Actions to create
and approve pull requests" is off, a clean sync needs one click to open its pull request; a pull request opened by
the default token starts no CI, so the tool dispatches `ci.yml` (`actions: write`) and links the run; the closed
proposals' branches stay until Matheus deletes them.
