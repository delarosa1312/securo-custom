# Upstream synchronization

Upstream: `securo-finance/securo`, AGPL-3.0, releases every few days (eight between 2026-10-21 and 2026-11-24).
Our base: `v0.22.0` (`bde1168f`). The fork must stay cheap to upgrade, so the patch inventory below is the
budget: every row is something to carry through each sync until its removal condition is met.

## Branches

| Branch | Role | Rules |
| --- | --- | --- |
| `upstream-main` | pristine mirror of upstream `main` | fast-forward only, never a fork commit |
| `main` | our integration branch, on a stable upstream tag plus our patches | never rebased or force-pushed; merges only |
| `<type>/<issue>-<slug>` | issue-linked feature branches from `main` | one issue, one pull request |
| `sync/<tag>` | one upstream release merged into a copy of `main` | created per sync, deleted after merge |

## Sync procedure, per upstream stable release

1. `git fetch upstream --tags`; fast-forward `upstream-main` to `upstream/main` and push it.
2. `git switch -c sync/<tag> origin/main`, then `git merge --no-ff <tag>` (the tag, not upstream `main`).
3. Conflicts: resolve only the mechanical ones. A conflict in money logic or in a migration stops the sync and
   becomes a question for Matheus, with both sides shown.
4. Overlap review: read the release notes and the diff of every upstream file in the patch inventory. Where
   upstream now does what a patch does, retire the patch (remove the hook and the fork code, keep the data path
   described in the patch's removal condition).
5. Migration graph: `python3 backend/scripts/check_migration_chain.py` (upstream line), the fork-line check, then
   on a scratch PostgreSQL: fresh `alembic upgrade heads`, and upgrade of a database at the previous custom
   release. Two heads exactly: upstream's number and our `f...`.
6. `fork/tools/check.sh`, the full backend and frontend suites through `fork/tools/test.sh`, then the E2E
   scenarios of every active patch. Compare failures with upstream's own CI for that tag before calling one ours.
7. Pull request `sync/<tag>` to `main` with: the release notes summary, conflicts and how each was resolved,
   patches retired, migration result, test and E2E results. Never merge a failing sync.
8. After merge: tag `v<tag>-custom.<n>`, build images, staging first, production only with Matheus's yes.

Security releases (a GitHub advisory, or a release whose notes mention a vulnerability) are synchronized first,
ahead of feature work.

Automation: `.github/workflows/fork-upstream-sync.yml` checks daily for a new stable upstream tag, does steps 1 and
2 on a `sync/<tag>` branch and opens the pull request (or an issue listing the conflicts when the merge does not
apply cleanly). It never resolves conflicts, merges or deploys.

## Migrations

Upstream keeps one numbered line in `backend/alembic/versions/` (ADR 0010; `131` at our base, `133` already on
upstream `main`). A numbered file of ours would collide with the next upstream release and force us to rewrite
applied migrations. So ours live in `backend/alembic/fork_versions/` as a second Alembic branch (label `fork`,
ids `f001`, `f002`, ...), each depending only on the upstream revision it needs. The migrate job runs
`alembic upgrade heads`. Rules:

- Never edit an applied migration, ours or upstream's. A fix is a new one.
- A fork migration only adds: new tables, nullable or defaulted columns, indexes. It never rewrites upstream data.
- Each has a tested `downgrade()`, so the previous custom image can run after a rollback (see `DEPLOY.md`).
- When upstream ships the same column or table, the retiring migration moves the data and drops ours.

## Contributing back

Generic fixes and features are prepared as upstream-shaped branches (no fork paths, numbered migration, all 20
locale files, an ADR where upstream would want one) and listed here. Nothing is opened on upstream without
Matheus's yes.

| Candidate | Why upstream might want it | State |
| --- | --- | --- |
| Duplicate recurring generation under concurrent workers | data integrity bug at `v0.22.0`: no lock or unique key on (schedule, date) | found 2026-11-26, not reported |
| In-app Apply of `propose_create_recurring_transaction` drops `weekend_adjustment` | preview and result differ | found 2026-11-26, not reported |
| Per-account "include in available balance" | upstream issue #871 item 3 asks for it | F-001 |
| Remaining budget in the cash-flow projection | upstream issue #1103 asks for it | F-002 |
| Recurring transfers | upstream issue #500 | F-004 |

## Upstream work to watch

| Upstream | What | Overlaps |
| --- | --- | --- |
| PR #1375 (open) | account flag excluding mirror accounts from totals | F-001 |
| Issue #1103, PR #1737 (open) | budget linked to projected balance; expected income | F-002 |
| Issue #500 | recurring transfers | F-004 |
| Issue #587, PR #1242, PR #1284 | occurrence to transaction, business-day schedules, recurring matching | F-005 |
| Issues #530, #1379, #1343; PR #1905 | calendar projections, accrual collisions, forecast performance | F-006 |
| Issue #633 | scoped delete for synced installments | F-007 |

## Patch inventory

One row per downstream change. Add the row in the pull request that adds the change.

| Patch | Issue / PR | Upstream files touched (hook) | Depends on | Why we carry it | Remove when |
| --- | --- | --- | --- | --- | --- |
| Fork knowledge base and tools | #1 | none (`CLAUDE.md`, `docs/fork/`, `fork/` are new) | none | project process | never (fork-only) |
