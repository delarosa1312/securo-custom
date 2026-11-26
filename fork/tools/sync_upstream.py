#!/usr/bin/env python3
"""Propose a sync of the fork with the newest stable upstream Securo release (docs/fork/UPSTREAM.md, D-014).

Finds the newest stable upstream tag already in `main` (the base) and the newest stable upstream release after it
(the target), trial-merges the target tag into a copy of `main` in a temporary worktree, and reports: security
advisories, a release-notes summary, upstream files the fork also changed (cross-checked with the patch
inventory), new upstream migrations and the migration checks of both lines. With `--apply` it also fast-forwards
`upstream-main`, pushes `sync/<tag>` and opens or updates the pull request (clean merge) or an issue (conflicts, or
a push GitHub refused). It never resolves a conflict, force-pushes, merges a pull request, tags or deploys.

Dry run by default: fetches and the trial merge are local, nothing is pushed and nothing is written on GitHub.
Stdlib only; needs `git`, and `gh` for GitHub (upstream reads always, fork writes with `--apply`).

    python3 fork/tools/sync_upstream.py                 # report what a sync would do
    python3 fork/tools/sync_upstream.py --apply         # do it (the workflow runs this)
    python3 fork/tools/sync_upstream.py --tag v0.23.0 --keep-worktree   # leave a conflicted merge to look at
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
from dataclasses import dataclass, field
from pathlib import Path

UPSTREAM_REPO = "securo-finance/securo"
UPSTREAM_URL = f"https://github.com/{UPSTREAM_REPO}.git"
LABEL = "upstream-sync"
SECURITY_LABEL = "security"
MARKER_RE = re.compile(r"<!-- fork-sync tag=(\S+) kind=(\S+) -->")
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
BEFORE_UPDATE_LIMIT = 700


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
    """Newest stable release after the base, or the forced tag (which must be stable and newer)."""
    newer = [r for r in releases if (version(r.tag) or (0, 0, 0)) > (version(base) or (0, 0, 0))]
    if forced:
        if not version(forced):
            raise SystemExit(f"--tag {forced}: only plain vX.Y.Z tags are synced")
        if version(forced) <= version(base):
            raise SystemExit(f"--tag {forced} is not newer than the base {base}")
        return next((r for r in newer if r.tag == forced), Release(forced))
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


def defang(text: str) -> str:
    """Quoted upstream text must not ping people or link our own issue numbers: `@user` and `#123` go in code."""
    text = re.sub(r"(?<![\w`/])@([A-Za-z0-9][A-Za-z0-9-]*)", r"`@\1`", text)
    return re.sub(r"(?<![\w`/&])#(\d+)\b", r"`#\1`", text)


def notes_summary(rel: Release) -> list[str]:
    """Sections with their bullet counts, plus the 'Before you update' section, which carries upgrade steps."""
    sections: list[tuple[str, int]] = []
    before: list[str] = []
    current = None
    for line in rel.body.splitlines():
        if line.startswith("## "):
            current = defang(re.sub(r"^[^\w]+", "", line[3:]).strip())
            sections.append((current, 0))
        elif current and re.match(r"^\s*[-*] ", line):
            sections[-1] = (current, sections[-1][1] + 1)
        if (
            current
            and current.lower().startswith("before you update")
            and not line.startswith("## ")
            and line.strip()
        ):
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
        if (
            not in_table
            or not line.startswith("|")
            or line.startswith("| ---")
            or line.startswith("| Patch |")
        ):
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


def find_proposals(items: list[dict]) -> list[dict]:
    """Open pull requests and issues this tool made, read from the marker in their body."""
    out = []
    for item in items:
        m = MARKER_RE.search(item.get("body") or "")
        if m:
            out.append({**item, "tag": m[1], "kind": m[2]})
    return out


# ---------------------------------------------------------------- git and gh


class Fail(Exception):
    pass


def run(
    cmd: list[str], cwd: Path | None = None, check: bool = True, env: dict | None = None
) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, env={**os.environ, **(env or {})}
    )
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


# ---------------------------------------------------------------- the run


@dataclass
class Report:
    base: str = ""
    target: str = ""
    lines: list[str] = field(default_factory=list)
    security: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add(self, *lines: str) -> None:
        self.lines.extend(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--apply", action="store_true", help="push and write on GitHub (default: dry run)"
    )
    ap.add_argument("--tag", help="sync to this stable tag instead of the newest release")
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
        help="files: write pull request and issue bodies to --out instead of GitHub (rehearsals)",
    )
    ap.add_argument("--out", type=Path, help="report folder (default: <git dir>/fork-sync)")
    ap.add_argument(
        "--alembic-db", help="scratch PostgreSQL URL: run `alembic upgrade heads` on the merge"
    )
    ap.add_argument(
        "--dispatch-ci", action="store_true", help="dispatch ci.yml on the pushed sync branch"
    )
    ap.add_argument(
        "--keep-worktree",
        action="store_true",
        help="leave the merge worktree (and a conflict) in place",
    )
    args = ap.parse_args(argv)

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
    repo = args.repo or github_repo(git("remote", "get-url", remote))
    if args.apply and args.publish == "github" and not repo:
        raise Fail(f"remote {remote} is not on GitHub; pass --repo or --publish files")

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

    api = gh_json(["api", f"repos/{UPSTREAM_REPO}/releases?per_page=50"])
    releases = [r for r in stable_releases(api) if r.tag in stable_tags]
    target_rel = pick_target(base, releases, args.tag)
    up_main = f"{up}/main"
    ahead = git("rev-list", "--count", f"{base}..{up_main}")
    unreleased_migrations = git.files("--diff-filter=A", base, up_main, "--", UPSTREAM_MIGRATIONS)
    mirror = git.rev(f"{remote}/upstream-main")
    behind = git("rev-list", "--count", f"{remote}/upstream-main..{up_main}") if mirror else "?"
    if target_rel is None:
        newest = releases[-1].tag if releases else "none"
        rep.add(
            f"Nothing to sync. Base `{base}` (newest stable tag in `{base_ref}`); newest stable upstream release "
            f"`{newest}`.",
            f"Upstream `main` is {ahead} commits past `{base}` ({len(unreleased_migrations)} new migrations), not "
            f"released yet. `upstream-main` is {behind} commits behind upstream `main`, left as is until a release.",
        )
        return 0
    rep.target = target = target_rel.tag
    if not git.rev(f"refs/tags/{target}"):
        raise Fail(f"tag {target} is not in the upstream remote")
    advisories = gh_json(["api", f"repos/{UPSTREAM_REPO}/security-advisories?per_page=100"]) or []
    rep.security = security_hits(base, target, releases, advisories)

    # 2. Facts from git, independent of the merge result.
    rows = inventory_rows(git("show", f"{base_ref}:{INVENTORY_DOC}", check=False))
    upstream_changed = set(git.files(base, target))
    fork_changed = set(git.files(base, base_ref))
    new_migs = git.files("--diff-filter=A", base, target, "--", UPSTREAM_MIGRATIONS)
    rep.add(
        "### Release notes",
        *[ln for rel in releases_between(base, target, releases) for ln in notes_summary(rel)],
    )
    overlap = sorted(upstream_changed & fork_changed)
    rep.add("", f"### Upstream changes to files the fork also changed ({len(overlap)})")
    if overlap:
        for path in overlap:
            patches = inventory_patches_for(path, rows)
            rep.add(
                f"- `{path}`: "
                + ("; ".join(patches) if patches else "**not in the patch inventory**")
            )
    else:
        rep.add("None.")
    rep.add("", f"### New upstream migrations ({len(new_migs)})")
    rep.add(
        ", ".join(migration_ids({p: git("show", f"{target}:{p}") for p in new_migs})) or "None."
    )

    # 3. Trial merge in a temporary worktree, detached: no local branch is created or moved.
    branch = f"sync/{target}"
    existing = git.rev(f"{remote}/{branch}")
    tmp = Path(tempfile.mkdtemp(prefix="fork-sync-"))
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
                ["git", "merge", "--no-ff", "--no-edit", "-m", msg, ref], cwd=wt, check=False
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
                        f"Conflicted merge left in `{wt}` (remove with `git worktree remove --force {wt}`).",
                    )
                else:
                    w("merge", "--abort")
                break
        head = w("rev-parse", "HEAD")
        changed = existing is None or head != existing

        if not rep.conflicts:
            assert_clean(w, base_ref, target)
            rep.add(
                "",
                "### Migration checks on the merge",
                *migration_checks(git, wt, tmp / "base", base_ref, args.alembic_db),
            )
        if args.keep_worktree and not keep:
            keep = True
            rep.add("", f"Merge left in `{wt}` (remove with `git worktree remove --force {wt}`).")

        if not args.apply:
            rep.actions.append("dry run: nothing pushed, nothing written on GitHub")
            if rep.conflicts:
                (out / "issue-body.md").write_text(proposal_body(rep, args, "conflict", None))
            else:
                (out / "pr-body.md").write_text(proposal_body(rep, args, "pr", branch))
            return 0

        # 4. Writes. upstream-main only fast-forwards; a plain push refuses anything else anyway.
        update_mirror(git, remote, up_main, mirror, rep)
        pushed = False
        if not rep.conflicts:
            if changed:
                push = run(
                    ["git", "push", remote, f"HEAD:refs/heads/{branch}"], cwd=wt, check=False
                )
                if push.returncode:
                    rep.warnings.append(f"push of {branch} refused: {push.stderr.strip()}")
                else:
                    pushed = True
                    rep.actions.append(f"pushed `{branch}` at {head[:10]}")
            else:
                rep.actions.append(f"`{branch}` already up to date at {head[:10]}")
        publish(
            rep,
            args,
            repo,
            out,
            branch,
            can_pr=not rep.conflicts and (pushed or not changed),
            pushed=pushed,
        )
        return 0
    finally:
        if not keep:
            run(["git", "worktree", "remove", "--force", str(wt)], cwd=git.cwd, check=False)
            shutil.rmtree(tmp, ignore_errors=True)


def assert_clean(w: Git, base_ref: str, target: str) -> None:
    """Refuse to go on unless HEAD contains both sides and no changed file carries a conflict marker."""
    for ref in (base_ref, f"refs/tags/{target}"):
        if not w.ok("merge-base", "--is-ancestor", ref, "HEAD"):
            raise Fail(f"merge result does not contain {ref}")
    changed = w.files(base_ref, "HEAD")
    if changed:
        grep = run(
            ["git", "grep", "-nE", "^(<<<<<<<|>>>>>>>) ", "HEAD", "--", *changed],
            cwd=w.cwd,
            check=False,
        )
        if grep.stdout.strip():
            raise Fail(
                "conflict markers in the merge result, nothing pushed:\n" + grep.stdout[:2000]
            )


def alembic(backend: Path, db: str, *cmd: str) -> tuple[bool, str]:
    proc = run(
        ["uv", "run", "--quiet", "--frozen", "alembic", *cmd],
        cwd=backend,
        check=False,
        env={"DATABASE_URL": db},
    )
    return proc.returncode == 0, (proc.stdout + proc.stderr).strip()


def heads_line(text: str) -> str:
    return ", ".join(f"`{ln}`" for ln in text.splitlines() if re.match(r"^\w+ \(", ln))


def migration_checks(git: Git, wt: Path, base_wt: Path, base_ref: str, db: str | None) -> list[str]:
    # The two checks of `fork/tools/check.sh migrations`, run directly so an older main without that target works.
    lines = []
    for name, script in (
        ("upstream line", "backend/scripts/check_migration_chain.py"),
        ("fork line", "fork/tools/check_fork_migrations.py"),
    ):
        proc = run([sys.executable, script], cwd=wt, check=False)
        lines.append(f"- migration chain ({name}): {'ok' if proc.returncode == 0 else 'FAILED'}")
        if proc.returncode:
            lines += [f"    {ln}" for ln in (proc.stdout + proc.stderr).strip().splitlines()[-15:]]
    if not db:
        lines.append(
            "- `alembic upgrade heads`: not run here (no scratch database); CI's PostgreSQL tests run it."
        )
        return lines
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
                lines += [f"    {ln}" for ln in text.splitlines()[-15:]]
                break
            lines.append(
                f"- {label}: " + ("ok" if cmd == "upgrade" else heads_line(text) or "(none)")
            )
    finally:
        run(["git", "worktree", "remove", "--force", str(base_wt)], cwd=git.cwd, check=False)
    return lines


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
        ["git", "push", remote, f"{up_main}:refs/heads/upstream-main"], cwd=git.cwd, check=False
    )
    if push.returncode:
        rep.warnings.append(f"fast-forward of `upstream-main` refused: {push.stderr.strip()}")
    else:
        rep.actions.append(f"fast-forwarded `upstream-main` to {target[:10]}")


def title(rep: Report, kind: str) -> str:
    sec = " [security]" if rep.security else ""
    if kind == "pr":
        return f"chore(sync): merge upstream {rep.target}{sec}"
    what = "merge conflicts" if kind == "conflict" else "run the sync locally"
    return f"Upstream sync {rep.target}{sec}: {what}"


def proposal_body(rep: Report, args, kind: str, branch: str | None) -> str:
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
        lines += [
            f"Branch `{branch}`: `{args.base_branch}` with the tag merged, no conflict. Nothing was resolved by hand.",
            "",
            "Before merging, `docs/fork/UPSTREAM.md` sync procedure steps 4 to 7: overlap review of each file below, "
            "migration upgrade of a database at the previous custom release, full suites, E2E of every active patch. "
            "Never merge a failing sync.",
        ]
    elif kind == "conflict":
        lines += [
            "The trial merge conflicts, so nothing was pushed. Resolve by hand, mechanical conflicts only; a "
            "conflict in money logic or a migration is a question for Matheus with both sides shown:",
            "```bash",
            f"python3 fork/tools/sync_upstream.py --tag {rep.target} --keep-worktree",
            "```",
        ]
    else:
        lines += [
            "The trial merge is clean, but GitHub refused the push with this workflow's token (usually because the "
            "release changes upstream workflow files, which the default token may not push). Run it locally:",
            "```bash",
            f"python3 fork/tools/sync_upstream.py --tag {rep.target} --apply",
            "```",
        ]
    lines += ["", *rep.lines]
    if rep.warnings:
        lines += ["", "### Warnings", *[f"- {w}" for w in rep.warnings]]
    return "\n".join(lines) + "\n"


def publish(
    rep: Report, args, repo: str | None, out: Path, branch: str, can_pr: bool, pushed: bool
) -> None:
    kind = "pr" if can_pr else ("conflict" if rep.conflicts else "manual")
    body = proposal_body(rep, args, kind, branch)
    body_file = out / ("pr-body.md" if kind == "pr" else "issue-body.md")
    body_file.write_text(body)
    labels = [LABEL] + ([SECURITY_LABEL] if rep.security else [])
    if args.publish == "files":
        rep.actions.append(
            f"would {'open or update a pull request' if kind == 'pr' else 'open or update an issue'}: "
            f"{title(rep, kind)} [{', '.join(labels)}] (body in {body_file.name})"
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
    prs = find_proposals(
        gh_json(
            [
                "pr",
                "list",
                *gh,
                "--state",
                "open",
                "--label",
                LABEL,
                "--json",
                "number,url,body,headRefName",
            ]
        )
    )
    issues = find_proposals(
        gh_json(
            ["issue", "list", *gh, "--state", "open", "--label", LABEL, "--json", "number,url,body"]
        )
    )
    label_args = [a for name in labels for a in ("--add-label", name)]
    if kind == "pr":
        same = next((p for p in prs if p["tag"] == rep.target), None)
        if same:
            run(
                [
                    "gh",
                    "pr",
                    "edit",
                    str(same["number"]),
                    *gh,
                    "--title",
                    title(rep, kind),
                    "--body-file",
                    str(body_file),
                    *label_args,
                ]
            )
            url = same["url"]
            rep.actions.append(f"updated pull request {url}")
        else:
            url = run(
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
                ]
            ).stdout.strip()
            rep.actions.append(f"opened pull request {url}")
        if pushed and args.dispatch_ci:
            ci = run(["gh", "workflow", "run", "ci.yml", *gh, "--ref", branch], check=False)
            rep.actions.append(
                "dispatched CI on the sync branch"
                if ci.returncode == 0
                else f"CI dispatch failed: {ci.stderr.strip()}"
            )
        stale = [p for p in prs if p["tag"] != rep.target] + issues
    else:
        same = next((i for i in issues if i["tag"] == rep.target), None)
        if same:
            run(
                [
                    "gh",
                    "issue",
                    "edit",
                    str(same["number"]),
                    *gh,
                    "--title",
                    title(rep, kind),
                    "--body-file",
                    str(body_file),
                    *label_args,
                ]
            )
            url = same["url"]
            rep.actions.append(f"updated issue {url}")
        else:
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
        # An older clean pull request stays open: merging it first shrinks this conflict.
        stale = [i for i in issues if i["tag"] != rep.target]
    for item in stale:
        is_pr = "headRefName" in item
        older = (version(item["tag"]) or (0, 0, 0)) < (version(rep.target) or (0, 0, 0))
        # A clean pull request also replaces this tag's own conflict or manual-run issue.
        if not older and not (kind == "pr" and not is_pr and item["tag"] == rep.target):
            continue
        run(
            [
                "gh",
                "pr" if is_pr else "issue",
                "close",
                str(item["number"]),
                *gh,
                "--comment",
                f"Superseded by the {rep.target} sync: {url}",
            ],
            check=False,
        )
        rep.actions.append(f"closed superseded proposal {item['url']} (branch kept)")


def render_report(rep: Report, args) -> str:
    mode = "apply" if args.apply else "dry run"
    head = f"## Upstream sync report ({mode})"
    lines = [head]
    if rep.target:
        lines.append(
            f"Base `{rep.base}`, target `{rep.target}`: "
            + (f"{len(rep.conflicts)} conflicting files." if rep.conflicts else "clean merge.")
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
