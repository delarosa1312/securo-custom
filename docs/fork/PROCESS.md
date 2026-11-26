# Process

The Dashing V2 method, adapted to this repository. None of Dashing's tools (`sessions.py`, `check.py`, `jest.py`,
`ship.py`, the E2E harness, its skills) run here: they carry Dashing assumptions. The equivalents are below.

## Roles

- **Coordinator session**: owns the backlog, briefs, the question ledger, integration into `main`, releases. It
  stays small: it does not read issue sessions' transcripts or do their work.
- **Issue session**: one per issue, in its own worktree, on the issue-linked branch. Plans, builds, reviews,
  tests, opens one scoped pull request to `main`, reports to the coordinator.
- **Helpers inside a session**: `Explore` for searching upstream code, `Plan` for architecture, `deep-review` for
  money, access and stored-data changes. Every helper brief has a tool-call ceiling and the decisions that touch
  its work. A helper's report is a claim: check it against the code before repeating it.

## Order of work for an issue

A brief's "done" never drops a step unless Matheus said so.

1. **Branch**: `fork/tools/new-branch.sh <issue> <slug>` in the session's worktree. Never `git checkout -b`: a
   branch is linked to its issue only when GitHub creates it.
   The worktree lives at `.worktrees/<issue>-<slug>` inside the main checkout (ignored by upstream's
   `.gitignore`); the main checkout itself stays on `main`.
2. **Investigate, then plan before coding**, posted as one comment on the issue:
   - what upstream already does (reproduce the limitation at `v0.22.0`, cite code) and what is missing;
   - the approach by the preference order in `CLAUDE.md`, with each hook into an upstream file named;
   - the financial semantics as a worked example with synthetic numbers;
   - for a screen change: placement, the closest existing screen, wording in English and pt-BR;
   - the tests to write, the E2E scenario, and the two or three adjacent paths someone would try next;
   - real choices only, gathered in one round (see Questions).
3. **Build** in small commits on the issue branch. Push without asking.
4. **Static checks**: `fork/tools/check.sh`. Then tests: `fork/tools/test.sh backend tests/fork/<area>` plus the
   upstream test files for every upstream file touched.
5. **Review, sized to the change, before E2E**:
   - light: one pattern-fit review (does it follow upstream conventions, is the hook minimal);
   - deep: the `deep-review` agent, independent of the author, for anything that computes money, checks access or
     changes stored data. Fix or answer every finding in the pull request.
6. **E2E in the running app** on the local synthetic stack (`fork/tools/stack.sh`, see E2E): the planned scenario
   and the adjacent paths, through the screen, REST and MCP. With the feature off, the upstream response is
   unchanged.
7. **Docs and evidence**: patch inventory row in `UPSTREAM.md`, a `DECISIONS.md` entry for any architectural
   choice, user-facing notes where behavior needs explaining. Evidence goes in the pull request: what was run,
   results, screenshots of screen changes. Files live in `~/scratch/securo-custom/<issue>/`.
8. **Pull request** to `main`, titled as a conventional commit, body = acceptance checklist with each item ticked
   or explained. Report `READY` to the coordinator.
9. **Coordinator**: reads the diff, reruns checks and the relevant tests on the merge result, confirms the
   evidence, merges (merge commit, never squash-rebase of `main`), updates the issue and `active-work.md`.

Not complete on a helper's word or a green compile: an issue is done when implementation, reviews, automated
tests, real-app E2E, adjacent paths, docs and evidence all exist.

## Financial test matrix

Each money feature covers the rows that apply and says in the pull request which do not apply and why.

Decimal precision and currency conversion; workspace isolation and roles (owner, editor, viewer, access token);
posted versus pending; refunds, reimbursements, category splits; transfer pairing and recurrence idempotency;
concurrent updates; month ends, leap years, time zones (`app_today()`); double counting across forecast layers;
fresh install and upgrade of an existing database; REST and MCP parity; feature off equals upstream.

## Briefs

The coordinator starts a session with a short brief:

```
#<n> <title>                         issue: <url>
Goal: <what Matheus wants, in his words>
Decisions: <answered questions that touch this issue, his no's first, files they put off limits>
Acceptance: the issue's checklist is the bar
Paths: <upstream files to read first, fork paths to create>; fork migration id: <f00n or none>
Shared files: <which and who else touches them>
Order of work: docs/fork/PROCESS.md, steps 1 to 8, none dropped
Permissions: the list under "Permissions" below, this repository only
Report to: coordinator session <id>, first line "#<n> STATE: ..." (REGISTERED, QUESTION, READY, BLOCKED, DONE)
```

## Questions

- Decide without asking when the answer is clear, nothing a user sees changes, it is easy to undo and costs no
  quality or meter. Report it in one line.
- Everything else goes to the ledger: `questions.md` in the memory folder, numbered `Q1, Q2, ...`, never reused or
  renumbered. An issue session sends `#<n> QUESTION` to the coordinator with the facts, the options (each with a
  concrete example) and its recommendation; the coordinator numbers it and asks Matheus in rounds.
- Decisions are relayed by quoting him. Approvals for an action a session must take itself are typed by him in
  that session, never relayed.

## State

- GitHub issue: the spec and the public status (plan comment, pull request link).
- Memory folder (`~/.claude/projects/-home-Unified-Documents-Projects-Personal-securo-custom/memory/`):
  `active-work.md` (one line per issue), `issue-<n>-<slug>.md` (decisions, current step, values that matter),
  `coordinator-state.md`, `questions.md`. Update at milestones; re-read after a compaction.

## Permissions (Matheus, 2026-11-26, this repository only)

Authorized without asking: creating the fork and its infrastructure, the issues the specification requires,
issue sessions, linked branches and worktrees, local commits, pushes to feature branches, scoped pull requests
and issue status updates in the fork, merging reviewed feature pull requests into `main` after the quality checks,
release tags and verified GHCR images within free allowances, the upstream synchronization workflow.

Needs his yes: material scope changes, ambiguous financial behavior, user-visible changes the issue does not name,
quality trade-offs, metered costs, credential access, destructive operations (force pushes, deleting branches or
data), anything sent to upstream Securo, starting the Docker daemon when it is not running, production deployment.

Tool-enforced approvals and permission prompts stay as they are: a refusal is reported, never worked around.

## Resource limits

20 cores and 31 GB are shared by every session.

- Tests only through `fork/tools/test.sh` (4 workers by default, one heavy run at a time through a lock).
- One Docker build or stack per machine at a time: `fork/tools/stack.sh` takes the same lock for builds.
- Long output goes to `~/scratch/securo-custom/<issue>/`, never `/tmp` (it is RAM) and never the repository.

## Tools

| Command | What it does |
| --- | --- |
| `fork/tools/new-branch.sh <issue> <slug>` | creates the issue-linked branch on GitHub (only GitHub can link it) and switches this worktree to it; `FORK_BRANCH_TYPE` sets the prefix (default `feat`) |
| `fork/tools/check.sh [backend\|frontend]` | ruff, ty, upstream migration chain, fork migration chain, eslint, tsc; prints only failures |
| `fork/tools/test.sh backend\|frontend [args]` | pytest or vitest with 4 workers, one heavy run per machine (a lock) |
| `fork/tools/stack.sh up\|seed\|status\|logs\|down` | the local synthetic stack for E2E (below) |

PostgreSQL-only tests (`backend/tests/*_postgres.py`) need a pgvector database: start a throwaway
`pgvector/pgvector:pg16` container on a free loopback port, run
`POSTGRES_TEST_URL=postgresql+asyncpg://<user>:<password>@127.0.0.1:<port>/<db> fork/tools/test.sh backend tests -k postgres`,
then remove the container.

## E2E

Upstream has no end-to-end suite. Ours is a local Docker Compose stack built from the worktree, with its own
project name, ports and volumes, seeded with synthetic data. It never uses production volumes, secrets or data
(D-008).

```bash
fork/tools/stack.sh up        # build (under the heavy lock) and start: http://127.0.0.1:13000, API :18000, MCP :18765
fork/tools/stack.sh seed      # synthetic data through the REST API, into a fresh stack only
cd fork/e2e && npm ci         # first time; uses /usr/bin/chromium (FORK_E2E_CHROMIUM to change), no browser download
FORK_E2E_OUT=~/scratch/securo-custom/<issue>/e2e npx playwright test [scenarios/<file>]
fork/tools/stack.sh down      # stop and delete the stack and its volumes
```

- One stack per machine (project `securo-custom-e2e`). `up` and `down` refuse while another worktree owns the
  running stack; the owner runs `down` first. The backend and frontend mount the worktree, so code edits reload
  without a rebuild; a dependency or migration change needs `down` and `up`.
- `up` refuses when a `.env` exists in the worktree: the stack runs on upstream defaults plus
  `fork/stack/compose.yml`, never on real configuration.
- Seed (`fork/stack/seed.py`): login `e2e@example.com` / `Synthetic-E2E-2026` (local stack only), three accounts
  (checking, savings, credit card), three months of posted history, pending rows in the past and the future, an
  installment series of ten parcels, transfers in the past and the future, four monthly recurring schedules,
  recurring and month-specific budgets. Dates are relative to today. A feature that needs more adds it there.
- Scenario format: one Playwright spec per scenario in `fork/e2e/scenarios/`, named after the issue
  (`f001-available-balance.spec.ts`). Helpers in `fork/e2e/lib/stack.ts`: `loginInBrowser`, `restToken` and
  `rest` for the API, `mcpClient(...).call(tool, args)` for MCP as an external client (token minted by the app),
  `screenshot` and `saveEvidence` into `FORK_E2E_OUT`. A scenario checks the screen, REST and MCP against the same
  expected numbers and, for a switchable feature, that with the switch off the responses equal upstream's.
- Evidence for the pull request: the screenshots and JSON files from `FORK_E2E_OUT`, never committed.
- `scenarios/smoke-cash-flow.spec.ts` is the reference: Cash Flow report on screen, over REST and over MCP.
