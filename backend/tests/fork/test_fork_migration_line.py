"""The fork's Alembic line sits beside upstream's (docs/fork/DECISIONS.md, D-003)."""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

import app.fork

BACKEND = Path(__file__).resolve().parents[2]


def _script() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini")))


def test_fork_package_imports():
    assert app.fork.__doc__


def test_fork_line_hangs_off_upstream_under_its_own_label():
    script = _script()
    root = script.get_revision("f000")
    assert root is not None
    assert root.branch_labels == {"fork"}
    assert root.down_revision is not None and not str(root.down_revision).startswith("f")

    rev = script.get_revision("fork@head")
    while rev is not None and rev.revision != "f000":
        assert isinstance(rev.down_revision, str), "fork migrations chain one parent at a time"
        rev = script.get_revision(rev.down_revision)
    assert rev is root


def test_at_most_one_upstream_head_and_one_fork_head():
    heads = _script().get_heads()
    fork_heads = [h for h in heads if h.startswith("f")]
    upstream_heads = [h for h in heads if not h.startswith("f")]
    assert len(fork_heads) == 1
    assert len(upstream_heads) <= 1  # 0 until upstream adds a migration after our root's parent
