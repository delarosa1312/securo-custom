#!/usr/bin/env python3
"""Print the release record of one custom release, in Markdown, read from git at the release commit.

The record is the GitHub release notes and, committed after the release, `docs/fork/releases/<version>.md`.
It holds the upstream base, the source commit, both migration heads, the previous release and how to roll
back to it, the patch list from the inventory in `docs/fork/UPSTREAM.md`, and the image digests.

Usage: record.py <version> [--commit REV] [--images-env FILE] [--registry]
       record.py <version> --is-newest   true when no tagged custom version sorts above <version>
  --images-env  the images.env written by build.sh (digests of the images just pushed)
  --registry    read the digests from the registry instead (anonymous; the packages must be public)
Stdlib only.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(
    subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], cwd=Path(__file__).parent, text=True
    ).strip()
)
REPO_URL = "https://github.com/delarosa1312/securo-custom"
UPSTREAM_URL = "https://github.com/securo-finance/securo"
PREFIX = "ghcr.io/delarosa1312"
VERSION_RE = re.compile(r"^v((0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*))-custom\.([1-9]\d*)$")
FORK_ROOT = "f000"


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def git_ok(*args: str) -> bool:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True).returncode == 0


def version_key(version: str) -> tuple[int, ...]:
    m = VERSION_RE.match(version)
    assert m
    return (int(m[2]), int(m[3]), int(m[4]), int(m[5]))


def migration_ids(text: str) -> tuple[str | None, list[str]]:
    """The revision id and its parents (down_revision) of one migration file, read as Python, not by pattern."""
    revision, parents = None, []
    for node in ast.parse(text).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        else:
            continue
        if not isinstance(target, ast.Name) or target.id not in ("revision", "down_revision"):
            continue
        found = ast.literal_eval(value)
        if target.id == "revision":
            revision = found
        elif found is not None:
            parents = [found] if isinstance(found, str) else list(found)
    return revision, parents


def heads(commit: str, folder: str) -> list[str]:
    """Alembic heads of one migration folder at a commit: revisions no other file in it builds on."""
    names = git("ls-tree", "--name-only", f"{commit}:{folder}").splitlines()
    revisions: set[str] = set()
    parents: set[str] = set()
    for name in names:
        if not name.endswith(".py"):
            continue
        revision, down = migration_ids(git("show", f"{commit}:{folder}/{name}"))
        if revision:
            revisions.add(revision)
            parents.update(down)
    return sorted(revisions - parents)


def fork_root_parent(commit: str) -> list[str]:
    """Upstream's revision under the fork root: what stock was at when the fork line was added."""
    names = git("ls-tree", "--name-only", f"{commit}:backend/alembic/fork_versions").splitlines()
    for name in names:
        if name.endswith(".py"):
            revision, down = migration_ids(
                git("show", f"{commit}:backend/alembic/fork_versions/{name}")
            )
            if revision == FORK_ROOT:
                return down
    return []


def fork_head(commit: str) -> str | None:
    if not git_ok("cat-file", "-e", f"{commit}:backend/alembic/fork_versions"):
        return None
    found = heads(commit, "backend/alembic/fork_versions")
    if len(found) > 1:
        raise SystemExit(
            f"record.py: the fork line has several heads at {commit}: {', '.join(found)}"
        )
    return found[0] if found else None


def previous_release(version: str, commit: str) -> str | None:
    tags = [
        t
        for t in git("tag", "--list", "v*-custom.*", "--merged", commit).splitlines()
        if VERSION_RE.match(t)
    ]
    older = [t for t in tags if t != version and version_key(t) < version_key(version)]
    return max(older, key=version_key) if older else None


def patch_list(commit: str) -> list[tuple[str, str]]:
    text = git("show", f"{commit}:docs/fork/UPSTREAM.md")
    if "## Patch inventory" not in text:
        raise SystemExit("record.py: docs/fork/UPSTREAM.md has no '## Patch inventory' section")
    section = text.split("## Patch inventory", 1)[1].split("\n## ", 1)[0]
    rows = []
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if (
            not line.startswith("|")
            or len(cells) < 2
            or cells[0] in ("Patch", "")
            or set(cells[0]) <= {"-", " "}
        ):
            continue
        rows.append((cells[0], cells[1]))
    return rows


def registry_digest(image: str) -> str:
    out = subprocess.run(
        ["docker", "buildx", "imagetools", "inspect", image, "--format", "{{json .Manifest}}"],
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        return ""
    return json.loads(out.stdout).get("digest", "")


def rollback(version: str, commit: str, previous: str | None) -> list[str]:
    head = fork_head(commit)
    upstream_now = heads(commit, "backend/alembic/versions")
    restore = "restore the pre-deploy dump and volumes, then deploy the previous images (`docs/fork/DEPLOY.md`, Rollback B)."
    if previous:
        lines = [f"Previous release: `{previous}`."]
        upstream_then = heads(previous, "backend/alembic/versions")
    else:
        # Back to stock: possible by downgrade only while upstream's line is still where the fork root was added.
        lines = [
            "Previous release: stock upstream images (what production ran before its first custom release)."
        ]
        upstream_then = fork_root_parent(commit)
    if upstream_now != upstream_then:
        lines.append(
            f"This release moves upstream's migration line ({', '.join(upstream_then)} to {', '.join(upstream_now)}): "
            f"a downgrade cannot take it back. Rollback: {restore}"
        )
        return lines
    previous_head = fork_head(previous) if previous else None
    if previous and previous_head == head:
        lines.append("No migration since the previous release. Rollback: deploy the previous tag.")
        return lines
    if previous_head:
        steps = [f"alembic downgrade fork@{previous_head}"]
    else:
        # Remove the whole fork line, then its empty root. `fork@` keeps both on our branch: a bare `f000-1` would
        # also downgrade upstream migrations above f000's parent. Never fork@base (D-003).
        steps = [f"alembic downgrade fork@{FORK_ROOT}"] if head and head != FORK_ROOT else []
        steps.append(f"alembic downgrade fork@{FORK_ROOT}-1")
    lines.append(
        "Rollback A (keeps data entered since the deploy), with this release's image, then the previous images: "
        + "; ".join(f"`{s}`" for s in steps)
        + "."
    )
    lines.append(f"Rollback B: {restore}")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("version")
    parser.add_argument("--commit")
    parser.add_argument("--images-env", type=Path)
    parser.add_argument("--registry", action="store_true")
    parser.add_argument(
        "--is-newest",
        action="store_true",
        help="print true when no tagged custom version sorts above it",
    )
    args = parser.parse_args()

    m = VERSION_RE.match(args.version)
    if not m:
        print(
            f"record.py: version must look like v0.22.0-custom.1, got {args.version!r}",
            file=sys.stderr,
        )
        return 2
    version = args.version
    if args.is_newest:
        tags = [t for t in git("tag", "--list", "v*-custom.*").splitlines() if VERSION_RE.match(t)]
        print(str(all(version_key(t) <= version_key(version) for t in tags)).lower())
        return 0
    upstream_base = f"v{m[1]}"
    commit = git("rev-parse", "--verify", f"{args.commit or 'refs/tags/' + version}^{{commit}}")
    previous = previous_release(version, commit)

    images = {name: (f"{PREFIX}/securo-{name}:{version}", "") for name in ("backend", "frontend")}
    if args.images_env:
        env = dict(
            line.split("=", 1) for line in args.images_env.read_text().splitlines() if "=" in line
        )
        for name in images:
            key = name.upper()
            images[name] = (env.get(f"{key}_IMAGE", images[name][0]), env.get(f"{key}_DIGEST", ""))
    if args.registry:
        images = {name: (ref, registry_digest(ref)) for name, (ref, _) in images.items()}

    out = [
        f"# Securo Custom {version}",
        "",
        f"- Upstream base: [`{upstream_base}`]({UPSTREAM_URL}/releases/tag/{upstream_base}) "
        f"(`{git('rev-parse', '--short=12', upstream_base + '^{commit}')}`)",
        f"- Source: [`{commit[:12]}`]({REPO_URL}/tree/{commit}) on `main`, "
        f"committed {git('show', '-s', '--format=%cs', commit)}; the source of these images (AGPL-3.0)",
        f"- Migration heads: upstream `{', '.join(heads(commit, 'backend/alembic/versions'))}`, "
        f"fork `{fork_head(commit) or 'none'}`; the migrate job runs `alembic upgrade heads`",
        "- Deploy: set `SECURO_CUSTOM_TAG` to this version (`docs/fork/DEPLOY.md`); never `latest`",
        "",
        "## Images",
        "",
        "| Image | Digest |",
        "| --- | --- |",
    ]
    for ref, digest in images.values():
        out.append(f"| `{ref}` | {f'`{digest}`' if digest else 'not published'} |")
    out += ["", "## Rollback", ""]
    out += [f"- {line}" for line in rollback(version, commit, previous)]
    out += ["", "## Patches carried", "", "| Patch | Issue / PR |", "| --- | --- |"]
    out += [f"| {patch} | {issue} |" for patch, issue in patch_list(commit)]
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
