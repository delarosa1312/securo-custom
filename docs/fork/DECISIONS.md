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

## D-011 F-004: an occurrence ledger and a row lock for every schedule; a transfer occurrence is one pair (2026-11-26, #6)

Upstream's `generate_pending` reads due schedules without a lock and advances each pointer in memory, so the
hourly job and the Generate button (or two workers) can write the same occurrence twice. The fork adds
`recurring_occurrences` (migration `f002`): one row per schedule and nominal occurrence date, unique, with a
state (`generated`; `skipped` and `overridden` are reserved for F-005, #7) and the transaction it produced.
Generation takes the schedules in id order, locks each row (`SELECT ... FOR UPDATE`, re-reading its pointer) and
claims the occurrence in a savepoint before writing; a claim that loses the unique key advances the pointer and
writes nothing. This covers every schedule, not only transfers, and is tested on PostgreSQL with two racing runs,
with the lock and with the unique key alone.

A transfer schedule is an upstream `recurring_transactions` row with `destination_account_id`, and
`destination_amount` between currencies (the only rate policy: the user fixes the amount received; without it a
cross-currency schedule is refused). The source is `account_id`, the type debit, no category. Both legs of an
occurrence carry `recurring_transaction_id`, so upstream's "already generated" checks and projection suppression
see each account's leg; the month summary, which expects one row per occurrence, counts transfer schedules apart.
Projections expand a transfer schedule into two leg proxies in fork code, and views that count income and
expense drop them. Upstream's on-demand FX sync commits the session, so it runs before the pair's savepoint and
the legs are stamped from stored rates only: the pair stays one unit. That commit releases the schedule lock
early; the unique claim still keeps the occurrence single. Bank rows never match a transfer schedule or leg: on a
bank-connected account the generated pending leg stays beside the bank's own row, and on a manual account a
statement import lands beside the posted leg, as an unpaired debit, until the user resolves it (deleting a leg
deletes both legs of the pair). Upstream does upgrade an ordinary generated placeholder in place; letting it do so
for a transfer leg could rewrite one leg's amount under a tolerance and leave the pair unequal, so no matching was
added here (coordinator, 2026-11-26).

An edit that leaves a transfer's fields alone (deactivating, an end date, a new description or amount) is not
judged again, so a schedule whose account was closed can still be stopped; changing an account or the amount
received is judged in full and refused while an account is closed.
