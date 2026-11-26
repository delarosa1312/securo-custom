#!/usr/bin/env python3
"""Fail when the fork's Alembic line (backend/alembic/fork_versions/) is not a single straight line.

Upstream's scripts/check_migration_chain.py checks only versions/. Ours: one root f000 labelled `fork` whose
down_revision is an upstream revision, then f001, f002, ... each chained on the previous fork revision (a need
for a newer upstream table goes in `depends_on`), so the database has exactly two heads (D-003).
Reuses upstream's parser; runs without dependencies and never touches a database.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
UPSTREAM_DIR = BACKEND / "alembic" / "versions"
FORK_DIR = BACKEND / "alembic" / "fork_versions"
ROOT = "f000"

_spec = importlib.util.spec_from_file_location("check_migration_chain", BACKEND / "scripts" / "check_migration_chain.py")
assert _spec and _spec.loader
chain = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(chain)

_LABELS_RE = re.compile(r"^branch_labels(?:\s*:\s*[^=]+)?\s*=\s*(.+)$", re.M)
_DEPENDS_RE = re.compile(r"^depends_on(?:\s*:\s*[^=]+)?\s*=\s*(.+)$", re.M)


def check() -> list[str]:
    upstream, errors = chain.load_revisions(UPSTREAM_DIR)
    fork, fork_errors = chain.load_revisions(FORK_DIR)
    errors += fork_errors
    upstream_ids = {rev.revision for rev in upstream}
    by_id: dict[str, list] = {}
    for rev in fork:
        by_id.setdefault(rev.revision, []).append(rev)

    for rev_id, group in sorted(by_id.items()):
        if len(group) > 1:
            errors.append(f"fork revision {rev_id!r} is claimed by {', '.join(r.name for r in group)}")
        if rev_id in upstream_ids:
            errors.append(f"fork revision {rev_id!r} collides with an upstream revision id")
    for rev in fork:
        if not re.fullmatch(r"f\d{3}", rev.revision):
            errors.append(f"{rev.name}: fork ids are f000, f001, ...; got {rev.revision!r}")
        if rev.name.split("_", 1)[0] != rev.revision:
            errors.append(f"{rev.name}: filename must start with its revision id {rev.revision!r}")
        source = rev.path.read_text(encoding="utf-8")
        labels = _LABELS_RE.search(source)
        labels_value = labels.group(1).strip() if labels else "None"
        if rev.revision == ROOT:
            if rev.down_revision not in upstream_ids:
                errors.append(f"{rev.name}: the root must sit on an upstream revision, got {rev.down_revision!r}")
            if "fork" not in labels_value:
                errors.append(f"{rev.name}: the root must carry branch_labels = (\"fork\",)")
        else:
            if rev.down_revision not in by_id:
                errors.append(
                    f"{rev.name}: down_revision {rev.down_revision!r} is not a fork revision; chain on the fork "
                    f"head and put an upstream need in depends_on"
                )
            if labels_value != "None":
                errors.append(f"{rev.name}: only {ROOT} carries a branch label")
            depends = _DEPENDS_RE.search(source)
            for dep in re.findall(r"['\"]([^'\"]+)['\"]", depends.group(1) if depends else ""):
                if dep not in upstream_ids and dep not in by_id:
                    errors.append(f"{rev.name}: depends_on {dep!r} does not exist")

    if ROOT not in by_id:
        errors.append(f"no root revision {ROOT} in {FORK_DIR}")
    parents = {rev.down_revision for rev in fork}
    heads = sorted(rev.revision for rev in fork if rev.revision not in parents)
    if len(heads) != 1:
        errors.append(f"expected exactly one fork head, found {len(heads)}: {', '.join(heads) or 'none'}")
    elif ROOT in by_id:
        # One head is not enough: a loop among non-root revisions also leaves one head. Walk head to root.
        seen: set[str] = set()
        current: str | None = heads[0]
        while current in by_id and current not in seen:
            seen.add(current)
            current = None if current == ROOT else by_id[current][0].down_revision
        if seen != set(by_id):
            missing = ", ".join(sorted(set(by_id) - seen))
            errors.append(f"the fork line from {heads[0]} does not reach {ROOT} through every revision: {missing}")
    return errors


def main() -> int:
    problems = check()
    if problems:
        print("Fork migration line is broken:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    fork, _ = chain.load_revisions(FORK_DIR)
    parents = {rev.down_revision for rev in fork}
    head = next(rev for rev in fork if rev.revision not in parents)
    print(f"Fork migration line is a single line: {len(fork)} migrations, head {head.revision} ({head.name}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
