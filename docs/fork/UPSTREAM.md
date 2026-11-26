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

`fork/tools/sync_upstream.py` does steps 1 to 3 and the checks of 5, and writes the report for 4 and 7 (D-014).
The daily workflow runs it; by hand, `python3 fork/tools/sync_upstream.py` reports and `--apply` writes. Rehearsed
in a scratch clone on 2026-11-26 (#3): a base of `v0.21.0` plus our patches synced to `v0.22.0`, a deliberate
conflict, its resolution by hand, and today's state.

1. Base and target: the base is the newest stable tag (`vX.Y.Z`, no suffix) already in `origin/main`; the target is
   the newest published upstream release after it (drafts and prereleases skipped). No newer release: report and
   change nothing, even when upstream `main` has moved (it holds unreleased work, often migrations).
2. Mirror: `git fetch upstream --tags`, then fast-forward `upstream-main` to `upstream/main` with a plain push
   (only when `origin/upstream-main` is an ancestor; a plain push refuses anything else). Done only when a release
   is synced.
3. Trial merge: `sync/<tag>` from `origin/main`, `git merge --no-ff <tag>` (the tag, never upstream `main`). The tool
   merges in a temporary detached worktree, so no local branch moves. When `sync/<tag>` already exists it builds on
   it and merges the newer `main` in, so the branch only moves forward.
4. Conflicts: the tool resolves none and pushes nothing (its merges run with `rerere` off, so a recorded resolution
   is never replayed); it opens an issue, or writes into the tag's open pull request, listing each file as fork hook
   (patch inventory row), migration, changed by the fork but not inventoried, or upstream only. Resolve only the
   mechanical ones by hand (`python3 fork/tools/sync_upstream.py --tag <tag> --keep-worktree` leaves the conflicted
   merge in a worktree) and push the result to `sync/<tag>`. The next run opens the pull request and lists every
   file where the branch differs from the tool's own merge as "changed by hand", for review. A conflict in money
   logic or in a migration stops the sync and becomes a question for Matheus, with both sides shown.
5. Overlap review: the report lists every upstream file changed in the range that `main` also changed since the
   base tag (from git, so it cannot miss a hook), with its patch inventory row, or "not in the patch inventory"
   when the inventory has drifted. Read the release notes and the diff of each. Where upstream now does what a patch
   does, retire the patch (remove the hook and the fork code, keep the data path described in its removal
   condition). The report also lists upstream workflow files the range adds or changes: once merged they run in the
   fork (a new one is enabled), so read each and disable any that does not apply (table below).
6. Migration graph: both chain checks (`fork/tools/check.sh migrations`), then on a scratch PostgreSQL: `alembic
   upgrade heads` with `main` on an empty database, then `upgrade heads` with the merge on that same database (the
   deploy path), `alembic heads` and `current`. After a sync that brings upstream migrations there are exactly two
   heads, upstream's number and our `f...` (rehearsal: `131` and `f001`). The workflow runs this on a PostgreSQL
   service; by hand pass `--alembic-db postgresql+asyncpg://...` of a throwaway, empty database.
7. `fork/tools/check.sh`, the full backend and frontend suites through `fork/tools/test.sh`, then the E2E scenarios
   of every active patch. Compare failures with upstream's own CI for that tag before calling one ours. A pull
   request opened with the workflow's default token starts no workflow run, so the tool dispatches `ci.yml` on the
   sync branch. When the release changed `docker-compose.prod.yml`, `check.sh` reports `Coolify files rendered`
   as stale: run `python3 fork/deploy/render.py` on the sync branch and read the diff of `fork/deploy/coolify/*.yml`.
   A new upstream service on the backend image goes into `fork/deploy/production.override.yml` (every
   backend-image service runs the custom tag) and gets limits in `staging.override.yml`; render again and commit
   the result to `sync/<tag>` (`DEPLOY.md`, Deploy files).
8. Pull request `sync/<tag>` to `main` (the tool opens it): security callout first, release notes summary (sections
   with item counts and the "Before you update" section of every release in the range), overlaps, new upstream
   migrations, upstream workflow files changed, migration results, files changed by hand. The tool owns only the block between its markers and
   rewrites it when something changed; add the conflicts resolved, patches retired, and the test and E2E results
   below it, where they are kept. A failed migration check puts `[checks failed]` in the title and fails the run.
   Never merge a failing sync.
9. After merge: tag `v<tag>-custom.<n>`, build images, staging first, production only with Matheus's yes.

One open proposal at a time, marked `upstream-sync` and by a hidden `fork-sync` marker in its body:
- one per tag: the tag's pull request when it has one (a later conflict or refused push is written into it, with
  `[conflicts]` or `[run locally]` in the title), else its issue. A pull request someone opens from `sync/<tag>` is
  adopted: the tool adds its block above that text.
- a newer tag replaces older proposals, pull requests and issues alike: the tool marks each `kind=superseded` and
  closes it with a link (branches stay; deleting is Matheus's call). One a person reopens stays open, for example
  an older security release to merge before a conflicting newer one.
- a proposal for a tag that a person closed is not opened again: the run reports it and waits for a newer
  release (reopen it to resume). Closing the tag's issue once its pull request is open does not count.
- once a tag reaches `main` (the sync pull request merged), the next run closes any proposal still open for that
  tag or an older one.
- only proposals by Matheus or the workflow, from branches of this repository, count; anything else is ignored.

Security releases are synchronized first, ahead of feature work. The signal is a published upstream advisory whose
patched version is inside the sync range, or release notes naming a vulnerability, a CVE or GHSA id, XSS, CSRF or
SSRF. Advisories come first because notes may say nothing: `v0.19.0` fixed GHSA-jxf3-r7wj-8frj (high, stored XSS)
and its notes do not mention it. The proposal gets the `security` label, `[security]` in its title and the callout
at the top.

Automation: `.github/workflows/fork-upstream-sync.yml` runs the tool daily (07:17 UTC) and on manual dispatch
(inputs: a tag, dry run). It contains no merge, tag, release or deploy step, and runs in two jobs so that the
release's own code never runs next to a write token. `check` has a read-only token, no persisted credentials and
the PostgreSQL service: it trial-merges and runs the chain checks and alembic (`--checks-only`), and hands
`checks.json` on. `propose` has the write permissions and runs only git and `gh` from `main`'s copy of the tool: it
redoes the merge, refuses unless the tree equals the one `check` tested, then pushes and proposes
(`--apply --checks-from`). The tool also keeps tokens out of the environment of every command that runs upstream
code, and its pushes never carry tags (`--no-follow-tags`).

Pull requests by the workflow: the default token cannot open one here (see Repository settings below), so a clean
sync pushes `sync/<tag>` and opens an issue with the link that opens the pull request in one click; the next run
adopts that pull request, fills in its description and closes the issue.

Token for pushes: GitHub refuses a push by the
default `GITHUB_TOKEN` that creates or updates a file under `.github/workflows/`, and upstream changed its workflow
files in three of the four releases before `v0.22.0`. Then the job opens an issue asking for a local run
(`python3 fork/tools/sync_upstream.py --tag <tag> --apply`, with a `gh` login that has the `workflow` scope).
With the default token the tool dispatches `ci.yml` on the sync branch and links that run in the pull request body,
because a pull request opened by that token starts no workflow run.

Optional, not set up (2026-11-26, Matheus decides later): a secret `FORK_SYNC_TOKEN` removes the local step. The
workflow already uses it when it exists, and then pushes and pull requests appear as made by Matheus and CI starts
on the pull request by itself (no dispatch). It must be a secret of the `fork-sync` environment, never a repository
secret: once a sync branch is pushed, the release's own workflows run on it in the fork, and a repository secret
would reach them. An environment secret limited to `main` reaches only the sync's write job. To create it:

1. github.com, Settings, Developer settings, Personal access tokens, Fine-grained tokens, Generate new token.
2. Resource owner `delarosa1312`; Repository access: Only select repositories, `securo-custom`.
3. Repository permissions, Read and write: Contents, Pull requests, Issues, Workflows (Metadata read-only is added
   by GitHub). Nothing else, no account permissions.
4. Expiration of his choice (GitHub mails before it expires; an expired token makes the job fail at checkout, so
   renew it or delete the secret).
5. In the repository: Settings, Environments, `fork-sync` (GitHub creates it on the workflow's first run; New
   environment with that name before then). Deployment branches and tags: Selected branches and tags, add `main`.
   Environment secrets, Add environment secret, name `FORK_SYNC_TOKEN`, value the token. Deleting the secret
   returns the workflow to the default token.

Quoted upstream text in a proposal has `@user`, `#123`, `owner/repo#123`, `GH-123` and issue or pull request URLs
put in code (code spans stay as they are), so it pings nobody, links none of our issues and creates no
cross-reference on upstream. Tool output is quoted in fenced blocks. The tool refuses to run against
`securo-finance/securo`. Nothing is ever written to upstream.

### Repository settings for the sync (Matheus's clicks; the tool works with either choice)

| Setting | State (2026-11-26) | Decision |
| --- | --- | --- |
| Allow GitHub Actions to create and approve pull requests | off | stays off |
| Ruleset on `main` requiring a pull request | none | recommended, Matheus clicks it when he likes |

**Allow GitHub Actions to create and approve pull requests.** On, it lets the default token of every workflow in
the repository open pull requests and approve them; the sync job would then open its pull request with no click.
Kept off (coordinator, 2026-11-26) because the same switch allows approvals by workflows. Where it lives: Settings,
Actions, General, section "Workflow permissions", checkbox "Allow GitHub Actions to create and approve pull
requests", Save. With an optional `FORK_SYNC_TOKEN` the setting does not matter: that token acts as Matheus.

**Ruleset on `main`.** Today `main` is unprotected: any token with `contents: write` can push to it or force-push
it. The sync's write job never does, but a ruleset makes it a rule instead of a property of our code, so even a
compromised release that reached a write token could not change `main`. Clicks:

1. Settings, Rules, Rulesets, New ruleset, New branch ruleset.
2. Ruleset name `main`; Enforcement status Active; Bypass list empty.
3. Target branches: Add target, Include default branch.
4. Branch rules: keep "Restrict deletions" and "Block force pushes" ticked; tick "Require a pull request before
   merging" with Required approvals 0 (Matheus is the only maintainer and cannot approve his own pull request).
5. Create.

With the ruleset, `main` changes only by merging a pull request: the Merge button or `gh pr merge <n> --merge -R
delarosa1312/securo-custom` (merge commit, as in `PROCESS.md` step 9); a direct `git push` to `main` is refused.
`upstream-main` and `sync/<tag>` are not covered, so the sync works the same.

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

## GitHub workflows in the fork (checked 2026-11-26)

| Workflow | State | Why |
| --- | --- | --- |
| `ci.yml` (CI) | active | backend shards, frontend, checks on pull requests to `main` and pushes to `main` |
| `approve-fork-runs`, `fast-lane`, `label-guard`, `pr-labels`, `stale-queue`, `downloads-badge` | disabled in the Actions settings (`gh workflow disable`) | upstream maintainer automation; files stay identical to upstream |
| `prepare-release`, `release` (Build and Push Docker Images) | disabled | upstream release and image publishing; ours is `fork-release.yml` |
| `fork-release.yml` (Fork release, ours) | runs on `v*-custom.*` tags; manual run = dry run | calls `fork/release/build.sh` and `record.py` (`DEPLOY.md`) |
| `fork-upstream-sync.yml` (Fork upstream sync) | fork workflow, active once Actions are enabled | daily upstream release check and sync proposal (#3, D-014) |

Repository variables `APPROVE_FORK_RUNS`, `FAST_LANE`, `STALE_QUEUE` are `off`. The repository is a GitHub fork, and
GitHub does not run any workflow in a fork until the owner enables them once: Actions tab, button "I understand my
workflows, go ahead and enable them". Before that click the fork had zero workflow runs (not even for the push that
merged pull request #11 into `main`).

## Contributing back

Generic fixes and features are prepared as upstream-shaped branches (no fork paths, numbered migration, all 20
locale files, an ADR where upstream would want one) and listed here. Nothing is opened on upstream without
Matheus's yes.

| Candidate | Why upstream might want it | State |
| --- | --- | --- |
| Duplicate recurring generation under concurrent workers | data integrity bug at `v0.22.0`: no lock or unique key on (schedule, date) | found 2026-11-26, fixed in the fork by F-004 (D-011, #6), not reported |
| In-app Apply of `propose_create_recurring_transaction` drops `weekend_adjustment` | preview and result differ | found 2026-11-26, not reported |
| Dashboard Income and Expenses drill-downs list projected rows of transfer-like categories | the panel disagrees with the card, which leaves them out | found 2026-11-26 (#6 review), not reported; the fork leaves out only F-004 legs |
| Per-account "include in available balance" | upstream issue #871 item 3 asks for it | F-001 |
| Remaining budget in the cash-flow projection | upstream issue #1103 asks for it | F-002 |
| Recurring transfers | upstream issue #500 | F-004 |
| Projection views disagree on the same day (F-006 fixes in `report_service`, `dashboard_service`, `transaction_calendar_service`, `cashflow_forecast_service`) | Cash Flow walk misses transfer legs and rows excluded from P&L; chart misses occurrences between today and a future month and transfer-like rules; calendar counts a materialized occurrence twice and overdue pending rows on past days, and with the reporting date basis drops rows billed after the visible month; rules on closed accounts project; predictive ignores pending and future rows | F-006, #8 |
| Scheduled refunds net against budget spending | a pending or recurring refund already nets once posted; before that the budget looks spent; projected spending floors once, so a refund larger than posted spending moves nothing when it posts | F-006 (Q9), #8 |
| Recurring occurrences dated today or earlier count before they are rows | a rule with automatic generation off counts its missed occurrences in month totals, budgets and the calendar (the predictive counts today's), so one paid by a hand-typed row counts twice; the card and Cash Flow already skip them | F-006 (Q12), #8 |
| Upcoming widget asks with the browser's date | wrong window near midnight in another time zone | F-006, #8 |
| Net-worth report lists accounts and assets in database order | the list reorders after an edit | F-006, #8 |

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
| Fork migration line (D-003) | #1 | `backend/alembic.ini` (one `version_locations` line); `docker-compose.yml`, `docker-compose.prod.yml` (`migrate`: `upgrade heads`); `backend/tests/test_schema_matches_migrations_postgres.py`, `backend/tests/test_agent_tool_name_lengths_postgres.py` (`upgrade heads`) | none | our migrations never collide with upstream's numbered line | we carry no fork migration, or upstream supports a second line |
| Fork locale bundles (D-004) | #1 | `frontend/src/lib/i18n.ts` (two imports, the `fork` key in the `en` and `pt-BR` resources) | none | fork strings outside upstream's 20 locale files | we carry no fork strings |
| Backend, test and frontend skeleton, local stack, E2E | #1 | none (`backend/app/fork/`, `backend/tests/fork/`, `backend/alembic/fork_versions/`, `frontend/src/fork/`, `fork/stack/`, `fork/e2e/` are new) | none | where features land, how they are exercised | never (fork-only) |
| Budget-aware Cash Flow mode | #5 | `backend/app/services/report_service.py` (`get_cash_flow_report`: `budget_forecast` argument, one block before the day walk, one at the return); `backend/app/api/reports.py` (`/cash-flow`: `budget_forecast` query, `months` up to 24 only with it, response model `CashFlowBudgetResponse \| ReportResponse`, `guard_cash_flow_request`); `backend/mcp_server/tools/reports.py` (`get_cash_flow`: one parameter); `frontend/src/pages/reports.tsx` (toggle, 24M range, query, bar series, tooltip, panel) | `budget_service.get_budget_vs_actual` semantics | upstream projections have no budget layer (upstream issue #1103) | upstream counts remaining budgets in the cash-flow projection |
| F-001 available balance setting (backend) | #4 | `models/account.py` (column line), `schemas/account.py` (field on `AccountRead`, `AccountUpdate`), `services/account_service.py` (key in `serialize_account`, connected-account allow-list, `check_available_setting` call in `update_account`), `services/cashflow_forecast_service.py` (switched-off accounts left out of the starting balance, manual rules and burn, and passed to the detector), `services/recurring_detector_service.py` (optional `exclude_account_ids` argument and one skip in the clustering loop), `app/main.py` (router include before `accounts_router`), `mcp_server/tools/accounts.py` (field in `list_accounts`), `mcp_server/tools/__init__.py` (import), `tests/api_contract/baseline.json` (one route); migration `f001` | `f000` | upstream has no way to keep reserved money out of "available" (upstream issue #871 item 3) | upstream ships an equivalent account flag: migrate `include_in_available` into it, drop `f001`'s column, remove the hooks |
| F-001 available balance setting (frontend) | #4 | `pages/dashboard.tsx` (cash accounts memo split so the predicate filters only the available list; the net-worth cash fallback sums the unfiltered list), `components/dashboard-widgets.tsx` (predicate in the existing type filter), `pages/accounts.tsx` (switch in `AccountDialog`, marker in the two row subtitles), `components/agents/proposal-card.tsx` and `lib/agent-proposals.ts` (one kind), `types/index.ts` (optional field) | backend row | same | same |
| F-004 recurring transfers and idempotent generation (backend) | #6 | `models/recurring_transaction.py` (`destination_account_id`, `destination_amount` columns; `account` relationship names its foreign key), `models/__init__.py` (fork model import), `schemas/recurring_transaction.py` (the two fields on Create, Update, Read), `services/recurring_transaction_service.py` (`create_recurring_transaction` and `update_recurring_transaction`: `check_create` / `check_update` calls and the two fields; `get_month_summary`: transfer schedules counted apart; `generate_pending`: schedules in id order, `lock_schedule`, the `occurrence` hook, `record` in the two write branches), `services/dashboard_service.py` (`_get_recurring_projections`, `get_projected_transactions`: account filter `touches_accounts`, `expand_legs`, transfer flag), `services/transaction_calendar_service.py` (`_project_recurring_items`: the same three), `services/cashflow_forecast_service.py` (manual recurring loop: `predictive_view`), `services/recurring_match_service.py` (`find_bill_for_incoming` and the two `find_placeholder_for_incoming` queries: transfer schedules excluded), `services/reconciliation_service.py` (`_suggest_recurring`: transfer schedules excluded), `services/account_service.py` (`delete_account`: schedules whose destination it is), `app/main.py` (router include before `recurring_router`), `mcp_server/tools/__init__.py` (import), `mcp_server/tools/lifecycle.py` (`list_recurring_transactions`: two fields), `tests/api_contract/baseline.json` (two routes); migration `f002` | `f001` | upstream has no transfer schedule (upstream issue #500) and can generate an occurrence twice (D-011) | upstream ships recurring transfers and idempotent generation: map `destination_account_id` into theirs, drop `f002`, remove the hooks |
| F-004 recurring transfers (frontend) | #6 | `pages/recurring.tsx` (Transfer option in the type select, `TransferAccountsFields` in place of category and account when selected, currency from the source account, transfer fields in the save payload, a transfer's refusal shown in the error toast, route line and neutral amount in the list), `components/agents/proposal-card.tsx` (title, summary, apply for three kinds), `lib/agent-proposals.ts` (three kinds), `types/index.ts` (two optional fields), `components/transaction-drill-down.tsx` (projected transfer legs left out, like the posted rows), list keys with the account in `pages/dashboard.tsx`, `components/dashboard-widgets.tsx` (Upcoming) and `components/transaction-calendar-view.tsx` (both legs share schedule and date) | backend row | same | same |
| Release images and Coolify deploy files | #2 | none (`.github/workflows/fork-release.yml`, `fork/release/`, `fork/deploy/` are new); `fork/deploy/coolify/*.yml` are rendered from `docker-compose.prod.yml` | `docker-compose.prod.yml` service names and the `migrate` command | our images and rehearsed rollback (D-013) | never (fork-only); re-render after each sync (`check.sh` fails until then) |
| Update banner reads this fork's releases | #2 | `frontend/src/hooks/use-latest-release.ts` (the URL and the query key come from `frontend/src/fork/release/release-source.ts`: one import, two uses) | fork releases published as non-prereleases (`fork-release.yml`) | upstream's releases do not run on our schema, and semver sorts `v0.22.0-custom.1` below `v0.22.0`, so upstream's banner would always offer them | never while we ship our own images |
| F-006 projection consistency | #8 | `services/report_service.py` (`get_cash_flow_report`: posted query selects a P&L flag and keeps non-P&L rows for the balance walk, pending loop at full amount, transfer-like rules in the walk; `_net_worth_at` and `_get_report_assets` ordering), `services/dashboard_service.py` (`_get_recurring_projections`: `on_open_account`, occurrences dated today or earlier skipped (Q12); `get_projected_transactions`: `on_open_account`; `get_balance_history`: `scheduled_balance_moves` for the gap before a future month and inside the month, connected placeholder skip), `services/transaction_calendar_service.py` (`get_transaction_calendar`: forecast rows move the balance on their own date through `calendar_row_moves`, claims through `calendar_claim_moves`, listed as invoice items; `_project_recurring_items`, `_count_occurrences_before`: materialized occurrences and occurrences dated today or earlier skipped), `schemas/transaction_calendar.py` (`invoice_id`, `invoice_number`), `services/invoice_forecast_service.py` (`Claim.invoice_id`), `services/cashflow_forecast_service.py` (`generate_cashflow_forecast`: covered accounts, scheduled rows, materialized skip, manual rules from tomorrow, detected occurrences yield to rows, detector limited to covered accounts), `services/budget_service.py` (`get_budget_vs_actual`: scheduled credits net; projection from the unfloored posted net), `frontend/src/components/dashboard-widgets.tsx` (`UpcomingWidget`: workspace date), `frontend/src/components/transaction-calendar-view.tsx` and `frontend/src/types/index.ts` (invoice row label), `mcp_server/tools/forecast.py` (`get_cashflow_forecast` description: what it covers); fork code in `app/fork/projection_consistency.py`, `app/fork/predictive_scheduled.py` | F-001 (`counts_as_available`), #5 (budget forecast) | the views must agree on the same day (D-012) | upstream fixes the same differences: drop each hook whose harness case passes without it |
| F-005 recurring lifecycle (backend) | #7 | `models/recurring_transaction.py` (`predecessor_id`, `deleted_at`, `active_before_delete` columns), `schemas/recurring_transaction.py` (`predecessor_id`, `deleted_at` on `RecurringTransactionRead`), `services/recurring_transaction_service.py` (`get_recurring_transactions`: `status` argument through `with_status`; `create_recurring_transaction`: duplicate message names a deleted twin; `update_recurring_transaction`: a deleted schedule refused; `_charged_occurrences`: deleted schedules left out; `get_month_summary`: skipped occurrences left out), `api/recurring_transactions.py` (list: `status` query), `services/dashboard_service.py` (`_materialized_recurring_occurrences`: occurrences the ledger covers added; `get_projected_transactions`: occurrences dated today or earlier left out, Q12), `services/backup_service.py` (`recurring_occurrences` backed up: import, `MODEL_SPECS`, the order lists; restore: `predecessors_first` on `recurring_transactions`), `app/main.py` (router include before `recurring_router`), `mcp_server/tools/__init__.py` (import), `mcp_server/tools/lifecycle.py` (`list_recurring_transactions`: `status` parameter, two fields), `tests/api_contract/cases.py` (`nominal_date` path value), `tests/api_contract/baseline.json` (twelve routes), `tests/test_dashboard_service.py`, `tests/test_dashboard_service_coverage.py` (five tests pin today before the month, Q12); migration `f003` | F-004 (`f002`, the ledger) | upstream has no skip, per-occurrence edit, change from a date, overdue list or recoverable delete (D-015) | upstream ships an equivalent lifecycle: map successors, skips and deleted schedules into it, drop `f003`, remove the hooks |

Not patched on purpose: the Helm chart (`charts/securo`) still runs `alembic upgrade head`, which fails from the
first sync that brings an upstream migration (two heads). We deploy with Compose; a Helm deploy of the fork needs
the same change in `charts/securo/templates/backend/job-migration.yaml`.
