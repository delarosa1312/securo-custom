#!/usr/bin/env python3
"""Propose a sync of the fork with the newest stable upstream Securo release (docs/fork/UPSTREAM.md, D-014).

Finds the newest stable upstream tag already in `main` (the base) and the newest stable upstream release after it
(the target), trial-merges the target tag into a copy of `main` in a temporary worktree, and reports: security
advisories, a release-notes summary, upstream files the fork also changed (cross-checked with the patch
inventory), new upstream migrations and the migration checks of both lines. With `--apply` it also fast-forwards
`upstream-main`, pushes `sync/<tag>` and opens or updates the one proposal for that tag: a pull request (clean
merge) or an issue (conflicts, or a write GitHub refused). It never resolves a conflict, force-pushes, merges a pull
request, tags or deploys.

Dry run by default: fetches and the trial merge are local, nothing is pushed and nothing is written on GitHub.
Stdlib only; needs `git`, and `gh` for GitHub (upstream reads always, fork writes with `--apply`). A missing
`upstream` remote is added, fetch-only.

    python3 fork/tools/sync_upstream.py                 # report what a sync would do
    python3 fork/tools/sync_upstream.py --apply         # do it
    python3 fork/tools/sync_upstream.py --tag v0.23.0 --keep-worktree   # leave a conflicted merge to look at

The workflow splits it in two so upstream code never runs next to a write token: `--checks-only` (read-only job:
trial merge, migration checks, alembic) writes checks.json; `--apply --checks-from checks.json` (write job: git and
gh only, no upstream code) redoes the merge, requires the same tree and publishes.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

UPSTREAM_REPO = "securo-finance/securo"
UPSTREAM_URL = f"https://github.com/{UPSTREAM_REPO}.git"
LABEL = "upstream-sync"
SECURITY_LABEL = "security"
MARKER_RE = re.compile(r"<!-- fork-sync tag=(\S+) kind=(\S+) -->")
BLOCK_RE = re.compile(r"<!-- fork-sync tag=\S+ kind=\S+ -->.*?<!-- fork-sync:end -->\n?", re.S)
STABLE_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
# Release notes rarely name a fix as a security one (v0.19.0 fixed GHSA-jxf3-r7wj-8frj without a word), so
# advisories come first; these words are the second signal. Plain "security" is left out: features use it.
SECURITY_NOTES_RE = re.compile(
    r"vulnerab\w*|\bCVE-\d{4}-\d{4,}\b|\bGHSA(?:-[0-9a-z]{4}){3}\b|\bsecurity (?:fix|issue|advisory|patch|update|release)"
    r"|\b(?:XSS|CSRF|SSRF)\b|remote code execution|privilege escalation",
    re.I,
)
UPSTREAM_MIGRATIONS = "backend/alembic/versions/"
INVENTORY_DOC = "docs/fork/UPSTREAM.md"
BEFORE_UPDATE_LIMIT = 1500
# Tokens never reach a command that runs upstream's code (chain checks, uv, alembic).
TOKEN_VARS = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "FORK_SYNC_TOKEN")


def version(tag: str) -> tuple[int, int, int] | None:
    m = STABLE_RE.match(tag)
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def parse_version(text: str) -> tuple[int, int, int] | None:
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


# ---------------------------------------------------------------- pure helpers (unit tested)


@dataclass
class Release:
    tag: str
    url: str = ""
    published: str = ""
    body: str = ""


def stable_releases(api_releases: list[dict]) -> list[Release]:
    """Published, non-draft, non-prerelease releases with a plain vX.Y.Z tag, oldest first."""
    out = [
        Release(
            r["tag_name"],
            r.get("html_url", ""),
            (r.get("published_at") or "")[:10],
            r.get("body") or "",
        )
        for r in api_releases
        if not r.get("draft") and not r.get("prerelease") and version(r.get("tag_name", ""))
    ]
    return sorted(out, key=lambda r: version(r.tag) or (0, 0, 0))


def pick_target(base: str, releases: list[Release], forced: str | None = None) -> Release | None:
    """Newest stable release after the base, or the forced tag (a published stable release, newer than the base)."""
    newer = [r for r in releases if (version(r.tag) or (0, 0, 0)) > (version(base) or (0, 0, 0))]
    if forced:
        if not version(forced):
            raise SystemExit(f"--tag {forced}: only plain vX.Y.Z tags are synced")
        if version(forced) <= version(base):
            raise SystemExit(f"--tag {forced} is not newer than the base {base}")
        match = next((r for r in newer if r.tag == forced), None)
        if match is None:
            raise SystemExit(f"--tag {forced} is not a published stable upstream release")
        return match
    return newer[-1] if newer else None


def releases_between(base: str, target: str, releases: list[Release]) -> list[Release]:
    lo, hi = version(base), version(target)
    return [r for r in releases if lo < version(r.tag) <= hi]


def security_hits(
    base: str, target: str, releases: list[Release], advisories: list[dict]
) -> list[str]:
    """One line per reason this sync is a security release: an advisory patched in (base, target], or notes."""
    lo, hi = version(base), version(target)
    hits = []
    for adv in advisories:
        if adv.get("state", "published") != "published":
            continue
        for vuln in adv.get("vulnerabilities") or []:
            patched = parse_version(vuln.get("patched_versions") or "")
            if patched and lo < patched <= hi:
                hits.append(
                    f"advisory [{adv.get('ghsa_id')}]({adv.get('html_url', '')}) ({adv.get('severity', '?')}): "
                    f"{defang(adv.get('summary', '').strip())}, patched in {'.'.join(map(str, patched))}"
                )
                break
    for rel in releases_between(base, target, releases):
        words = sorted({m.group(0).lower() for m in SECURITY_NOTES_RE.finditer(rel.body)})
        if words:
            hits.append(f"release notes of {rel.tag} mention: {', '.join(words)}")
    return hits


CODE_SPAN_RE = re.compile(r"(`+)(?:.+?)\1", re.S)


def _defang_plain(text: str) -> str:
    def code(m: re.Match) -> str:
        return f"`{m.group(0)}`"

    # Issue and pull request references would create cross-reference events (upstream's) or link ours.
    text = re.sub(
        r"https?://github\.com/[\w.-]+/[\w.-]+/(?:issues|pull|discussions)/\d+[^\s)\]>]*",
        code,
        text,
    )
    text = re.sub(r"(?<![\w`/.-])[\w.-]+/[\w.-]+#\d+\b", code, text)
    text = re.sub(r"\bGH-\d+\b", code, text)
    text = re.sub(r"(?<![\w`/])@[A-Za-z0-9][A-Za-z0-9-]*", code, text)
    return re.sub(r"(?<![\w`/&])#\d+\b", code, text)


def defang(text: str) -> str:
    """Quoted upstream text must not ping people or reference issues: those go in code; code spans stay as they are."""
    out, pos = [], 0
    for m in CODE_SPAN_RE.finditer(text):
        out += [_defang_plain(text[pos : m.start()]), m.group(0)]
        pos = m.end()
    return "".join([*out, _defang_plain(text[pos:])])


def notes_summary(rel: Release) -> list[str]:
    """Sections with their bullet counts, plus the 'Before you update' section, which carries upgrade steps."""
    sections: list[tuple[str, int]] = []
    before: list[str] = []
    current = None
    for line in rel.body.splitlines():
        if line.startswith("## "):
            current = defang(re.sub(r"^[^\w]+", "", line[3:]).strip())
            sections.append((current, 0))
            continue
        if current and re.match(r"^\s*[-*] ", line):
            sections[-1] = (current, sections[-1][1] + 1)
        if current and current.lower().startswith("before you update") and line.strip():
            before.append(line.rstrip())
    head = f"[{rel.tag}]({rel.url})" if rel.url else rel.tag
    when = f" ({rel.published})" if rel.published else ""
    parts = ", ".join(f"{name} {n}" if n else name for name, n in sections) or "no notes"
    lines = [f"- {head}{when}: {parts}"]
    if before:
        text = "\n".join(before)
        if len(text) > BEFORE_UPDATE_LIMIT:
            text = text[:BEFORE_UPDATE_LIMIT].rsplit("\n", 1)[0] + "\n(cut, read the release notes)"
        lines.append("  Before you update:")
        lines += [f"  > {defang(line)}" for line in text.splitlines()]
    return lines


def inventory_rows(doc: str) -> list[tuple[str, list[str]]]:
    """(patch name, path tokens) from the patch inventory table in UPSTREAM.md."""
    rows = []
    in_table = False
    for line in doc.splitlines():
        if line.startswith("## "):
            in_table = line.strip() == "## Patch inventory"
            continue
        if not in_table or not line.startswith("|") or line.startswith(("| ---", "| Patch |")):
            continue
        # Split on unescaped pipes; the files cell is whatever sits between the first two and the last three
        # columns, so a stray pipe inside it does not shift the patch name.
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
        if len(cells) < 6:
            continue
        files = " ".join(cells[2:-3])
        tokens = [t for t in re.findall(r"`([^`]+)`", files) if "/" in t or re.search(r"\.\w+$", t)]
        rows.append((cells[0], tokens))
    return rows


def inventory_patches_for(path: str, rows: list[tuple[str, list[str]]]) -> list[str]:
    """Inventory rows naming this file; rows abbreviate paths (`models/account.py`), so match by path suffix."""
    return [
        name for name, tokens in rows if any(path == t or path.endswith("/" + t) for t in tokens)
    ]


def migration_ids(files: dict[str, str]) -> list[str]:
    """`revision` ids of the given migration files (path -> source), in file order."""
    out = []
    for path in sorted(files):
        m = re.search(r"^revision(?:\s*:\s*[^=]+)?\s*=\s*['\"]([^'\"]+)['\"]", files[path], re.M)
        out.append(f"`{m[1]}` ({Path(path).name})" if m else Path(path).name)
    return out


def conflict_kind(path: str, rows: list[tuple[str, list[str]]], fork_changed: set[str]) -> str:
    if path.startswith("backend/alembic/"):
        return "migration: stop, ask Matheus"
    patches = inventory_patches_for(path, rows)
    if patches:
        return "fork hook: " + "; ".join(patches)
    if path in fork_changed:
        return "changed by the fork but not in the patch inventory"
    return "upstream only"


def fenced(text: str) -> list[str]:
    body = text.strip().replace("```", "'''")
    return ["```text", *body.splitlines()[-20:], "```"] if body else []


@dataclass
class Proposal:
    number: int
    url: str
    is_pr: bool
    state: str  # OPEN, CLOSED, MERGED
    tag: str
    body: str
    marked: bool  # carries the tool's marker (False: a pull request someone opened from the sync branch)


def classify(prs: list[dict], issues: list[dict]) -> list[Proposal]:
    """The tool's proposals: marked items, plus pull requests from a `sync/<tag>` branch (adopted)."""
    out = []
    for item, is_pr in [*((p, True) for p in prs), *((i, False) for i in issues)]:
        body = item.get("body") or ""
        m = MARKER_RE.search(body)
        head = item.get("headRefName") or ""
        tag = m[1] if m else (head[len("sync/") :] if is_pr and head.startswith("sync/") else "")
        if not version(tag):
            continue
        out.append(
            Proposal(
                item["number"], item["url"], is_pr, item.get("state", "OPEN"), tag, body, bool(m)
            )
        )
    return out


def closed_by_hand(props: list[Proposal], tag: str) -> Proposal | None:
    """A proposal for this tag that a person closed: the tool never closes its own tag's pull request, and closes
    its tag's issue only once a pull request for the tag exists."""
    mine = [p for p in props if p.tag == tag]
    for p in mine:
        if p.is_pr and p.state == "CLOSED":
            return p
    if not any(p.is_pr for p in mine):
        return next((p for p in mine if not p.is_pr and p.state == "CLOSED"), None)
    return None


def merge_body(existing: str, block: str) -> str:
    """Replace the tool's block and keep everything a person wrote around it."""
    if BLOCK_RE.search(existing):
        return BLOCK_RE.sub(lambda _m: block, existing, count=1)
    if MARKER_RE.search(existing):  # an older body without the end marker: take it over whole
        return block
    return block + ("\n" + existing if existing.strip() else "")


# ---------------------------------------------------------------- git and gh


class Fail(Exception):
    pass


def run(
    cmd: list[str],
    cwd: Path | None = None,
    check: bool = True,
    env: dict | None = None,
    scrub: bool = False,
) -> subprocess.CompletedProcess:
    base = {k: v for k, v in os.environ.items() if not (scrub and k in TOKEN_VARS)}
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, env={**base, **(env or {})}
        )
    except FileNotFoundError as exc:
        raise Fail(f"{cmd[0]} is not installed: {exc}") from exc
    if check and proc.returncode:
        raise Fail(
            f"{' '.join(cmd)} failed ({proc.returncode}):\n{proc.stderr.strip() or proc.stdout.strip()}"
        )
    return proc


class Git:
    def __init__(self, cwd: Path):
        self.cwd = cwd

    def __call__(self, *args: str, check: bool = True) -> str:
        return run(["git", *args], cwd=self.cwd, check=check).stdout.strip()

    def ok(self, *args: str) -> bool:
        return run(["git", *args], cwd=self.cwd, check=False).returncode == 0

    def rev(self, ref: str) -> str | None:
        proc = run(
            ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
            cwd=self.cwd,
            check=False,
        )
        return proc.stdout.strip() or None

    def files(self, *args: str) -> list[str]:
        out = self("diff", "--name-only", "--no-renames", *args)
        return [f for f in out.splitlines() if f]


def gh_json(args: list[str]) -> list | dict:
    return json.loads(run(["gh", *args]).stdout or "null")


def github_repo(url: str) -> str | None:
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?/?$", url)
    return m[1] if m else None


def work_root() -> Path:
    """Temporary worktrees and venvs go to disk: RUNNER_TEMP in CI, the user cache otherwise (/tmp may be RAM)."""
    root = Path(
        os.environ.get("RUNNER_TEMP")
        or Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    )
    path = root / "securo-custom" / "sync-work"
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------- the run


@dataclass
class Report:
    base: str = ""
    target: str = ""
    lines: list[str] = field(default_factory=list)
    security: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    by_hand: list[str] = field(default_factory=list)
    checks_failed: bool = False
    actions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    ci: str = ""

    def add(self, *lines: str) -> None:
        self.lines.extend(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--apply", action="store_true", help="push and write on GitHub (default: dry run)"
    )
    ap.add_argument("--tag", help="sync to this published stable release instead of the newest")
    ap.add_argument("--remote", default="origin", help="the fork's remote (default origin)")
    ap.add_argument(
        "--upstream", default="upstream", help="upstream remote name, added fetch-only if missing"
    )
    ap.add_argument("--base-branch", default="main")
    ap.add_argument("--repo", help="fork on GitHub, owner/name (default: from the remote URL)")
    ap.add_argument(
        "--publish",
        choices=["github", "files"],
        default="github",
        help="files: write the bodies to --out instead of GitHub; rehearsals against a local remote only",
    )
    ap.add_argument("--out", type=Path, help="report folder (default: <git dir>/fork-sync)")
    ap.add_argument(
        "--alembic-db",
        help="empty scratch PostgreSQL URL: `alembic upgrade heads`, main then merge",
    )
    ap.add_argument(
        "--checks-only", action="store_true", help="no writes; save the checks to <out>/checks.json"
    )
    ap.add_argument(
        "--checks-from", type=Path, help="checks.json of a --checks-only run on the same merge tree"
    )
    ap.add_argument(
        "--dispatch-ci", action="store_true", help="dispatch ci.yml on the pushed sync branch"
    )
    ap.add_argument(
        "--keep-worktree", action="store_true", help="leave the merge worktree (and a conflict)"
    )
    args = ap.parse_args(argv)
    if args.checks_only and (args.apply or args.checks_from):
        ap.error("--checks-only writes nothing; it does not combine with --apply or --checks-from")

    git = Git(Path(run(["git", "rev-parse", "--show-toplevel"]).stdout.strip()))
    out = (
        args.out
        or Path(git("rev-parse", "--path-format=absolute", "--git-common-dir")) / "fork-sync"
    )
    out.mkdir(parents=True, exist_ok=True)
    rep = Report()
    try:
        code = sync(git, args, out, rep)
    except Fail as exc:
        rep.warnings.append(str(exc))
        code = 1
    text = render_report(rep, args)
    (out / "report.md").write_text(text)
    print(text)
    print(f"\n(report and bodies in {out})")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as fh:
            fh.write(text + "\n")
    return code


def sync(git: Git, args, out: Path, rep: Report) -> int:
    remote, up, base_ref = args.remote, args.upstream, f"{args.remote}/{args.base_branch}"
    remote_url = git("remote", "get-url", remote)
    repo = args.repo or github_repo(remote_url)
    if UPSTREAM_REPO in {(repo or "").lower(), (github_repo(remote_url) or "").lower()}:
        raise Fail(
            f"refusing to work on upstream {UPSTREAM_REPO}: point --remote and --repo at the fork"
        )
    if args.apply and args.publish == "github" and not repo:
        raise Fail(f"remote {remote} is not on GitHub; pass --repo or --publish files")
    if args.apply and args.publish == "files" and github_repo(remote_url):
        raise Fail("--publish files is for rehearsals: its pushes would still reach GitHub")

    # 1. Fetch. The upstream remote is fetch-only; a missing one (CI checkout) is added with pushes disabled.
    if not git.ok("remote", "get-url", up):
        git("remote", "add", up, UPSTREAM_URL)
        git("remote", "set-url", "--push", up, "DISABLED")
    git("fetch", "--quiet", "--tags", up, f"+refs/heads/main:refs/remotes/{up}/main")
    git("fetch", "--quiet", "--prune", remote)

    stable_tags = sorted(
        (t for t in git("tag", "--list", "v*").splitlines() if version(t)), key=version
    )
    in_base = [t for t in stable_tags if git.ok("merge-base", "--is-ancestor", t, base_ref)]
    if not in_base:
        raise Fail(f"no stable upstream tag is in {base_ref}")
    rep.base = base = in_base[-1]

    api = gh_json(["api", f"repos/{UPSTREAM_REPO}/releases?per_page=100"])
    releases = [r for r in stable_releases(api) if r.tag in stable_tags]
    target_rel = pick_target(base, releases, args.tag)
    up_main = f"{up}/main"
    ahead = git("rev-list", "--count", f"{base}..{up_main}")
    unreleased = git.files("--diff-filter=A", base, up_main, "--", UPSTREAM_MIGRATIONS)
    mirror = git.rev(f"{remote}/upstream-main")
    behind = git("rev-list", "--count", f"{remote}/upstream-main..{up_main}") if mirror else "?"
    if target_rel is None:
        newest = releases[-1].tag if releases else "none"
        rep.add(
            f"Nothing to sync. Base `{base}` (newest stable tag in `{base_ref}`); newest stable upstream release "
            f"`{newest}`.",
            f"Upstream `main` is {ahead} commits past `{base}` ({len(unreleased)} new migrations), not released "
            f"yet. `upstream-main` is {behind} commits behind upstream `main`, left as is until a release.",
        )
        if args.checks_only:
            (out / "checks.json").write_text(
                json.dumps({"target": "", "tree": "", "lines": [], "failed": False})
            )
        return 0
    rep.target = target = target_rel.tag
    if not git.rev(f"refs/tags/{target}"):
        raise Fail(f"tag {target} is not in the upstream remote")
    try:
        advisories = (
            gh_json(["api", f"repos/{UPSTREAM_REPO}/security-advisories?per_page=100"]) or []
        )
    except Fail as exc:  # the release-note signal still runs; the warning shows on the proposal
        advisories = []
        rep.warnings.append(
            f"upstream security advisories could not be read, check them by hand: {exc}"
        )
    rep.security = security_hits(base, target, releases, advisories)

    # 2. Facts from git, independent of the merge result.
    rows = inventory_rows(git("show", f"{base_ref}:{INVENTORY_DOC}", check=False))
    fork_changed = set(git.files(base, base_ref))
    overlap = sorted(set(git.files(base, target)) & fork_changed)
    new_migs = git.files("--diff-filter=A", base, target, "--", UPSTREAM_MIGRATIONS)
    rep.add(
        "### Release notes",
        *[ln for rel in releases_between(base, target, releases) for ln in notes_summary(rel)],
    )
    rep.add("", f"### Upstream changes to files the fork also changed ({len(overlap)})")
    for path in overlap:
        patches = inventory_patches_for(path, rows)
        rep.add(
            f"- `{path}`: " + ("; ".join(patches) if patches else "**not in the patch inventory**")
        )
    if not overlap:
        rep.add("None.")
    rep.add("", f"### New upstream migrations ({len(new_migs)})")
    rep.add(
        ", ".join(migration_ids({p: git("show", f"{target}:{p}") for p in new_migs})) or "None."
    )

    # Proposals already on GitHub decide whether this tag is proposed at all (a person may have closed it).
    branch = f"sync/{target}"
    props: list[Proposal] = []
    if args.apply and args.publish == "github":
        props = load_proposals(repo)
        closed = closed_by_hand(props, target)
        if closed:
            rep.actions.append(
                f"the {target} proposal was closed by hand ({closed.url}): not proposed again; a newer release "
                f"will be, or reopen it"
            )
            return 0

    # 3. Trial merge in a temporary worktree, detached: no local branch is created or moved.
    existing = git.rev(f"{remote}/{branch}")
    tmp = Path(tempfile.mkdtemp(prefix="fork-sync-", dir=work_root()))
    wt = tmp / "merge"
    git("worktree", "add", "--quiet", "--detach", str(wt), existing or base_ref)
    w = Git(wt)
    keep = False
    try:
        for ref, msg in (
            (f"refs/tags/{target}", f"Merge upstream {target} into {args.base_branch}"),
            (base_ref, f"Merge {args.base_branch} into {branch}"),
        ):
            if w.ok("merge-base", "--is-ancestor", ref, "HEAD"):
                continue
            merge = run(
                [
                    "git",
                    "-c",
                    "rerere.enabled=false",
                    "merge",
                    "--no-ff",
                    "--no-edit",
                    "-m",
                    msg,
                    ref,
                ],
                cwd=wt,
                check=False,
            )
            if merge.returncode:
                rep.conflicts = w("diff", "--name-only", "--diff-filter=U").splitlines()
                if not rep.conflicts:
                    raise Fail(f"merge of {ref} failed without conflicts:\n{merge.stderr.strip()}")
                rep.add(
                    "",
                    f"### Conflicts merging `{ref}` ({len(rep.conflicts)})",
                    "The tool resolves none of them.",
                )
                rep.add(
                    *[f"- `{p}`: {conflict_kind(p, rows, fork_changed)}" for p in rep.conflicts]
                )
                if args.keep_worktree:
                    keep = True
                    rep.add(
                        "",
                        f"Conflicted merge left in `{wt}` (remove: `git worktree remove --force {wt}`).",
                    )
                else:
                    w("merge", "--abort")
                break
        head = w("rev-parse", "HEAD")
        tree = w("rev-parse", "HEAD^{tree}") if not rep.conflicts else ""
        changed = existing is None or head != existing

        if not rep.conflicts:
            assert_clean(w, base_ref, target)
            if existing:
                rep.by_hand = by_hand(git, base_ref, target, tree)
                if rep.by_hand:
                    rep.add(
                        "",
                        f"### Changed by hand on `{branch}` ({len(rep.by_hand)})",
                        "These differ from what the tool's own merge of `main` and the tag gives: review each.",
                        *[f"- `{p}`: {conflict_kind(p, rows, fork_changed)}" for p in rep.by_hand],
                    )
            checks = migration_section(rep, git, wt, tmp / "base", base_ref, args, target, tree)
            rep.add("", "### Migration checks on the merge", *checks)
        else:
            checks = []
        if args.keep_worktree and not keep:
            keep = True
            rep.add("", f"Merge left in `{wt}` (remove: `git worktree remove --force {wt}`).")

        if args.checks_only or not args.apply:
            rep.actions.append("dry run: nothing pushed, nothing written on GitHub")
            kind = "conflict" if rep.conflicts else "pr"
            name = "issue-body.md" if rep.conflicts else "pr-body.md"
            (out / name).write_text(proposal_block(rep, args, kind, branch, repo))
            if args.checks_only:
                (out / "checks.json").write_text(
                    json.dumps(
                        {
                            "target": target,
                            "tree": tree,
                            "lines": checks,
                            "failed": rep.checks_failed,
                        }
                    )
                )
                return 0  # the write job publishes the failure and fails
            return 1 if rep.checks_failed else 0

        # 4. Writes. upstream-main only fast-forwards; plain pushes, never tags.
        update_mirror(git, remote, up_main, mirror, rep)
        pushed = refused = False
        if not rep.conflicts:
            if changed:
                push = run(
                    ["git", "push", "--no-follow-tags", remote, f"HEAD:refs/heads/{branch}"],
                    cwd=wt,
                    check=False,
                )
                if push.returncode:
                    refused = True
                    rep.warnings.append(f"push of `{branch}` refused: {push.stderr.strip()}")
                else:
                    pushed = True
                    rep.actions.append(f"pushed `{branch}` at {head[:10]}")
            else:
                rep.actions.append(f"`{branch}` already up to date at {head[:10]}")
        kind = "conflict" if rep.conflicts else ("manual" if refused else "pr")
        publish(rep, args, repo, out, branch, kind, pushed, head, props)
        return 1 if rep.checks_failed else 0
    finally:
        if not keep:
            run(["git", "worktree", "remove", "--force", str(wt)], cwd=git.cwd, check=False)
            shutil.rmtree(tmp, ignore_errors=True)


def by_hand(git: Git, base_ref: str, target: str, tree: str) -> list[str]:
    """Files where the sync branch differs from the tool's own merge of main and the tag (resolved or edited by
    hand). The fresh merge is computed without a worktree; a conflicted file holds markers, so it shows up."""
    fresh = run(
        ["git", "merge-tree", "--write-tree", "--no-messages", base_ref, f"refs/tags/{target}"],
        cwd=git.cwd,
        check=False,
    )
    if fresh.returncode not in (0, 1):
        raise Fail(f"git merge-tree failed: {fresh.stderr.strip()}")
    fresh_tree = fresh.stdout.split()[0]
    return git.files(fresh_tree, tree)


def assert_clean(w: Git, base_ref: str, target: str) -> None:
    """Refuse to go on unless HEAD contains both sides and no changed file carries a conflict marker."""
    for ref in (base_ref, f"refs/tags/{target}"):
        if not w.ok("merge-base", "--is-ancestor", ref, "HEAD"):
            raise Fail(f"merge result does not contain {ref}")
    changed = w.files(base_ref, "HEAD")
    if not changed:
        return
    grep = run(
        [
            "git",
            "--literal-pathspecs",
            "grep",
            "-nE",
            "^(<<<<<<<|>>>>>>>) ",
            "HEAD",
            "--",
            *changed,
        ],
        cwd=w.cwd,
        check=False,
    )
    if grep.returncode not in (0, 1):
        raise Fail(f"could not check the merge for conflict markers: {grep.stderr.strip()}")
    if grep.returncode == 0:
        raise Fail("conflict markers in the merge result, nothing pushed:\n" + grep.stdout[:2000])


def migration_section(
    rep: Report, git: Git, wt: Path, base_wt: Path, base_ref: str, args, target: str, tree: str
) -> list[str]:
    if args.checks_from:
        saved = json.loads(args.checks_from.read_text())
        if saved.get("target") != target or saved.get("tree") != tree:
            raise Fail(
                f"the checks were run on {saved.get('target')} tree {str(saved.get('tree'))[:10]}, this merge is "
                f"{target} tree {tree[:10]} (main or the branch moved between the jobs): nothing written; next run"
            )
        lines, failed = saved.get("lines", []), bool(saved.get("failed"))
    else:
        lines, failed = migration_checks(git, wt, base_wt, base_ref, args.alembic_db)
    rep.checks_failed = failed
    return lines


def alembic(backend: Path, db: str, *cmd: str) -> tuple[bool, str]:
    proc = run(
        ["uv", "run", "--quiet", "--frozen", "alembic", *cmd],
        cwd=backend,
        check=False,
        env={"DATABASE_URL": db},
        scrub=True,
    )
    return proc.returncode == 0, (proc.stdout + proc.stderr).strip()


def heads_line(text: str) -> str:
    return ", ".join(f"`{ln}`" for ln in text.splitlines() if re.match(r"^\w+ \(", ln))


def migration_checks(
    git: Git, wt: Path, base_wt: Path, base_ref: str, db: str | None
) -> tuple[list[str], bool]:
    # The two checks of `fork/tools/check.sh migrations`, run directly so an older main without that target works.
    lines, failed = [], False
    for name, script in (
        ("upstream line", "backend/scripts/check_migration_chain.py"),
        ("fork line", "fork/tools/check_fork_migrations.py"),
    ):
        proc = run([sys.executable, script], cwd=wt, check=False, scrub=True)
        lines.append(f"- migration chain ({name}): {'ok' if proc.returncode == 0 else 'FAILED'}")
        if proc.returncode:
            failed = True
            lines += fenced(proc.stdout + proc.stderr)
    if not db:
        lines.append(
            "- `alembic upgrade heads`: not run here (no scratch database); CI's PostgreSQL tests run it."
        )
        return lines, failed
    # An empty scratch database is brought to main's heads first, then upgraded with the merge: the deploy path.
    git("worktree", "add", "--quiet", "--detach", str(base_wt), base_ref)
    try:
        steps = [
            (
                base_wt,
                "upgrade",
                f"`alembic upgrade heads` with `{base_ref}` on an empty scratch database",
            ),
            (base_wt, "current", "heads before the sync"),
            (wt, "upgrade", "`alembic upgrade heads` with the merge on that database"),
            (wt, "heads", "`alembic heads` of the merge"),
            (wt, "current", "database after the sync"),
        ]
        for tree, cmd, label in steps:
            ok, text = alembic(
                tree / "backend", db, *(["upgrade", "heads"] if cmd == "upgrade" else [cmd])
            )
            if not ok:
                lines.append(f"- {label}: FAILED")
                lines += fenced(text)
                return lines, True
            lines.append(
                f"- {label}: " + ("ok" if cmd == "upgrade" else heads_line(text) or "(none)")
            )
    finally:
        run(["git", "worktree", "remove", "--force", str(base_wt)], cwd=git.cwd, check=False)
    return lines, failed


def update_mirror(git: Git, remote: str, up_main: str, mirror: str | None, rep: Report) -> None:
    target = git.rev(up_main)
    if mirror == target:
        rep.actions.append("`upstream-main` already at upstream `main`")
        return
    if mirror and not git.ok("merge-base", "--is-ancestor", mirror, up_main):
        rep.warnings.append(
            "`upstream-main` is not an ancestor of upstream `main`: not touched, look at it by hand"
        )
        return
    push = run(
        ["git", "push", "--no-follow-tags", remote, f"{up_main}:refs/heads/upstream-main"],
        cwd=git.cwd,
        check=False,
    )
    if push.returncode:
        rep.warnings.append(f"fast-forward of `upstream-main` refused: {push.stderr.strip()}")
    else:
        rep.actions.append(f"fast-forwarded `upstream-main` to {target[:10]}")


def title(rep: Report, kind: str) -> str:
    marks = (" [security]" if rep.security else "") + (
        " [checks failed]" if rep.checks_failed else ""
    )
    if kind == "pr":
        return f"chore(sync): merge upstream {rep.target}{marks}"
    what = {
        "conflict": "merge conflicts",
        "manual": "run the sync locally",
        "open-pr": "open the pull request",
    }
    return f"Upstream sync {rep.target}{marks}: {what[kind]}"


def compare_url(repo: str | None, base_branch: str, branch: str) -> str:
    return f"https://github.com/{repo}/compare/{base_branch}...{quote(branch, safe='/')}?expand=1"


def proposal_block(rep: Report, args, kind: str, branch: str, repo: str | None) -> str:
    """The part of a proposal's body the tool owns, between its markers. Text outside it is never touched."""
    lines = [f"<!-- fork-sync tag={rep.target} kind={kind} -->"]
    if rep.security:
        lines += [
            "## Security release: high priority, sync before feature work",
            *[f"- {s}" for s in rep.security],
            "",
        ]
    lines.append(
        f"Upstream `{rep.base}` to `{rep.target}`, prepared by `fork/tools/sync_upstream.py` (D-014)."
    )
    if kind == "pr":
        lines.append(
            f"Branch `{branch}`: `{args.base_branch}` with the tag merged by the tool"
            + (
                ", plus the changes by hand listed below."
                if rep.by_hand
                else ", no conflict, nothing changed by hand."
            )
        )
        if rep.ci:
            lines.append(rep.ci)
        lines += [
            "",
            "Before merging, `docs/fork/UPSTREAM.md` sync procedure steps 5 to 8: overlap review of each file below, "
            "the migration results below, full suites, E2E of every active patch, and the results added below this "
            "block. Never merge a failing sync.",
        ]
    elif kind == "conflict":
        lines += [
            "The trial merge conflicts, so nothing was pushed. Resolve by hand, mechanical conflicts only; a "
            "conflict in money logic or a migration is a question for Matheus with both sides shown. Push the "
            f"resolved merge to `{branch}`; the next run lists what was changed by hand and opens the pull request.",
            "```bash",
            f"python3 fork/tools/sync_upstream.py --tag {rep.target} --keep-worktree",
            "```",
        ]
    elif kind == "manual":
        lines += [
            "The trial merge is clean, but GitHub refused the push with this workflow's token (usually because the "
            "release changes upstream workflow files, which the default token may not push). Run it locally with a "
            "`gh` login that has the `workflow` scope:",
            "```bash",
            f"python3 fork/tools/sync_upstream.py --tag {rep.target} --apply",
            "```",
        ]
    else:  # open-pr
        lines += [
            f"`{branch}` is pushed, but this workflow's token may not open pull requests. Open it here: "
            f"{compare_url(repo, args.base_branch, branch)} ; the next run fills in its description and closes "
            "this issue.",
        ]
    lines += ["", *rep.lines]
    if rep.warnings:
        lines += ["", "### Warnings", *[f"- {w}" for w in rep.warnings]]
    lines += ["<!-- fork-sync:end -->"]
    return "\n".join(lines) + "\n"


def load_proposals(repo: str) -> list[Proposal]:
    gh = ["-R", repo]
    prs = gh_json(
        [
            "pr",
            "list",
            *gh,
            "--state",
            "all",
            "--limit",
            "200",
            "--json",
            "number,url,body,headRefName,state",
        ]
    )
    issues = gh_json(
        [
            "issue",
            "list",
            *gh,
            "--state",
            "all",
            "--label",
            LABEL,
            "--limit",
            "200",
            "--json",
            "number,url,body,state",
        ]
    )
    return classify(prs or [], issues or [])


def ci_runs_line(repo: str, branch: str, run_url: str) -> str:
    runs = f"https://github.com/{repo}/actions/workflows/ci.yml?query=branch%3A{quote(branch, safe='')}"
    exact = f"[this commit's run]({run_url}), " if run_url else ""
    return f"CI (dispatched, since a pull request opened by the workflow token starts none): {exact}[all runs on `{branch}`]({runs})."


def dispatch_ci(rep: Report, repo: str, branch: str, head: str) -> str:
    """Dispatch ci.yml on the sync branch and return the body line linking its run (looked up for about 30 s)."""
    gh = ["-R", repo]
    ci = run(["gh", "workflow", "run", "ci.yml", *gh, "--ref", branch], check=False)
    if ci.returncode:
        rep.warnings.append(f"CI dispatch failed: {ci.stderr.strip()}")
        return ""
    rep.actions.append("dispatched CI on the sync branch")
    for _ in range(10):
        time.sleep(3)
        found = run(
            [
                "gh",
                "run",
                "list",
                *gh,
                "--workflow",
                "ci.yml",
                "--branch",
                branch,
                "--event",
                "workflow_dispatch",
                "--limit",
                "5",
                "--json",
                "url,headSha",
            ],
            check=False,
        )
        try:
            listed = json.loads(found.stdout or "[]")
        except json.JSONDecodeError:
            listed = []
        match = next((r["url"] for r in listed if r.get("headSha") == head), "")
        if match:
            return ci_runs_line(repo, branch, match)
    return ci_runs_line(repo, branch, "")


def publish(
    rep: Report,
    args,
    repo: str | None,
    out: Path,
    branch: str,
    kind: str,
    pushed: bool,
    head: str,
    props: list[Proposal],
) -> None:
    """One open proposal per sync: this tag's pull request if it has one (a conflict or a refused push is written
    into it), else its issue; every other open proposal is for an older tag and is closed with a link."""
    labels = [LABEL] + ([SECURITY_LABEL] if rep.security else [])
    if args.publish == "files":
        block = proposal_block(rep, args, kind, branch, repo)
        name = "pr-body.md" if kind == "pr" else "issue-body.md"
        (out / name).write_text(block)
        what = "a pull request" if kind == "pr" else "an issue"
        rep.actions.append(
            f"would open or update {what}: {title(rep, kind)} [{', '.join(labels)}] (body in {name})"
        )
        return

    gh = ["-R", repo]
    for name, color, desc in (
        (LABEL, "0e8a16", "Upstream Securo release sync proposal"),
        (SECURITY_LABEL, "d93f0b", "Security fix, sync first"),
    ):
        if name in labels:
            run(
                ["gh", "label", "create", name, "--color", color, "--description", desc, *gh],
                check=False,
            )
    open_props = [p for p in props if p.state == "OPEN"]
    own_pr = next((p for p in open_props if p.is_pr and p.tag == rep.target), None)
    own_issue = next((p for p in open_props if not p.is_pr and p.tag == rep.target), None)
    if kind == "pr" and args.dispatch_ci:
        rep.ci = dispatch_ci(rep, repo, branch, head) if pushed else ci_runs_line(repo, branch, "")

    target: Proposal | None = None
    if own_pr:  # a conflict or refused push after the pull request exists is written into it, not a new issue
        state = {"pr": "", "conflict": " [conflicts]", "manual": " [run locally]"}[kind]
        update(
            rep,
            gh,
            out,
            own_pr,
            title(rep, "pr") + state,
            proposal_block(rep, args, kind, branch, repo),
            labels,
        )
        target = own_pr
    elif kind == "pr":
        block = proposal_block(rep, args, kind, branch, repo)
        body_file = out / "pr-body.md"
        body_file.write_text(block)
        create = run(
            [
                "gh",
                "pr",
                "create",
                *gh,
                "--base",
                args.base_branch,
                "--head",
                branch,
                "--title",
                title(rep, kind),
                "--body-file",
                str(body_file),
                *[a for name in labels for a in ("--label", name)],
            ],
            check=False,
        )
        if create.returncode == 0:
            url = create.stdout.strip()
            rep.actions.append(f"opened pull request {url}")
            target = Proposal(0, url, True, "OPEN", rep.target, block, True)
        else:
            rep.warnings.append(f"opening the pull request was refused: {create.stderr.strip()}")
            kind = "open-pr"
    if target is None:
        block = proposal_block(rep, args, kind, branch, repo)
        if own_issue:
            update(rep, gh, out, own_issue, title(rep, kind), block, labels)
            target = own_issue
        else:
            body_file = out / "issue-body.md"
            body_file.write_text(block)
            url = run(
                [
                    "gh",
                    "issue",
                    "create",
                    *gh,
                    "--title",
                    title(rep, kind),
                    "--body-file",
                    str(body_file),
                    *[a for name in labels for a in ("--label", name)],
                ]
            ).stdout.strip()
            rep.actions.append(f"opened issue {url}")
            target = Proposal(0, url, False, "OPEN", rep.target, block, True)

    for item in open_props:
        if item.url == target.url:
            continue
        older = (version(item.tag) or (0, 0, 0)) < (version(rep.target) or (0, 0, 0))
        own_issue_replaced = item.tag == rep.target and not item.is_pr and target.is_pr
        if not (older or own_issue_replaced):
            continue
        close = run(
            [
                "gh",
                "pr" if item.is_pr else "issue",
                "close",
                str(item.number),
                *gh,
                "--comment",
                f"Superseded by the {rep.target} sync: {target.url}",
            ],
            check=False,
        )
        if close.returncode == 0:
            rep.actions.append(f"closed superseded proposal {item.url} (branch kept)")
        else:
            rep.warnings.append(f"could not close {item.url}: {close.stderr.strip()}")


def update(
    rep: Report,
    gh: list[str],
    out: Path,
    item: Proposal,
    new_title: str,
    block: str,
    labels: list[str],
) -> None:
    body = merge_body(item.body, block)
    if body == item.body:
        rep.actions.append(f"{item.url} already up to date")
        return
    body_file = out / "edit-body.md"
    body_file.write_text(body)
    run(
        [
            "gh",
            "pr" if item.is_pr else "issue",
            "edit",
            str(item.number),
            *gh,
            "--title",
            new_title,
            "--body-file",
            str(body_file),
            *[a for name in labels for a in ("--add-label", name)],
        ]
    )
    rep.actions.append(f"updated {item.url} (text outside the tool's block kept)")


def render_report(rep: Report, args) -> str:
    mode = "checks only" if args.checks_only else ("apply" if args.apply else "dry run")
    lines = [f"## Upstream sync report ({mode})"]
    if rep.target:
        lines.append(
            f"Base `{rep.base}`, target `{rep.target}`: "
            + (f"{len(rep.conflicts)} conflicting files." if rep.conflicts else "clean merge.")
            + (" A migration check FAILED." if rep.checks_failed else "")
        )
    if rep.security:
        lines += ["", "### Security release", *[f"- {s}" for s in rep.security]]
    lines += ["", *rep.lines] if rep.lines else []
    if rep.actions:
        lines += ["", "### Actions", *[f"- {a}" for a in rep.actions]]
    if rep.warnings:
        lines += ["", "### Warnings", *[f"- {w}" for w in rep.warnings]]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
