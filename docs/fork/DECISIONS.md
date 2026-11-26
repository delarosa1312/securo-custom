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

## D-003 Fork migrations are a second Alembic branch (2026-11-26, to be verified on PostgreSQL in #1)

Upstream ADR 0010 keeps one numbered line and renumbers on collision. A numbered migration of ours would collide
with every upstream release and force edits to applied migrations. Ours live in
`backend/alembic/fork_versions/` with branch label `fork` and ids `f001...`; deploy runs `alembic upgrade heads`.
Cost: `alembic.ini` gains `version_locations`, the migrate command and the schema test use `heads`, and we keep a
check of our own line. Open point for #1: confirm `alembic current --check-heads` passes with two heads and that
upstream's chain check stays green.

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

## D-008 F-001: the dashboard keeps computing Available balance in the browser (2026-11-26)

Upstream sums the headline from `GET /api/accounts` in the browser. F-001 keeps that and adds one predicate to the
existing filter, instead of making the dashboard call the new server route: no new request, loading state or
layout change in an upstream page. The server figure (`app/fork/available_balance.py`) uses the same inputs and
the same rule (converted balance, or the native one with `fx_fallback` when no rate exists), and the fork tests
pin both to the issue's worked example so they cannot drift apart unnoticed. The route lives under
`/api/accounts` so the existing `accounts` token scope covers it; it is mounted before upstream's accounts
router because `/{account_id}` would otherwise take the path.
