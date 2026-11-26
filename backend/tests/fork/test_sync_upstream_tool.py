"""fork/tools/sync_upstream.py, the upstream release sync (D-014): selection, security, report, and the git writes.

The git tests build tiny local repositories (an "upstream", a bare "origin" and a clone) and fake the two GitHub
reads, so nothing leaves the machine. Synthetic content only.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[3] / "fork" / "tools" / "sync_upstream.py"
_spec = importlib.util.spec_from_file_location("fork_sync_upstream", TOOL)
assert _spec and _spec.loader
sync = importlib.util.module_from_spec(_spec)
sys.modules["fork_sync_upstream"] = sync  # dataclasses resolve their module through sys.modules
_spec.loader.exec_module(sync)


def _rel(tag, **kw):
    return {
        "tag_name": tag,
        "html_url": f"https://example.test/{tag}",
        "published_at": "2026-01-02T00:00:00Z",
        "body": "",
        "draft": False,
        "prerelease": False,
        **kw,
    }


# ---------------------------------------------------------------- selection


def test_stable_releases_skip_drafts_prereleases_and_suffixed_tags():
    api = [
        _rel("v1.2.0"),
        _rel("v1.3.0", draft=True),
        _rel("v1.4.0", prerelease=True),
        _rel("v1.5.0-rc.1"),
        _rel("v1.10.0"),
        _rel("v1.9.0"),
    ]
    assert [r.tag for r in sync.stable_releases(api)] == ["v1.2.0", "v1.9.0", "v1.10.0"]


def test_pick_target_is_the_newest_release_after_the_base():
    rels = sync.stable_releases([_rel("v1.0.0"), _rel("v1.1.0"), _rel("v1.2.0")])
    assert sync.pick_target("v1.0.0", rels).tag == "v1.2.0"
    assert sync.pick_target("v1.2.0", rels) is None
    assert sync.pick_target("v1.0.0", rels, forced="v1.1.0").tag == "v1.1.0"
    with pytest.raises(SystemExit):
        sync.pick_target("v1.1.0", rels, forced="v1.0.0")
    with pytest.raises(SystemExit):
        sync.pick_target("v1.0.0", rels, forced="v1.2.0-rc.1")


# ---------------------------------------------------------------- security


ADVISORY = {
    "ghsa_id": "GHSA-aaaa-bbbb-cccc",
    "html_url": "https://example.test/adv",
    "severity": "high",
    "state": "published",
    "summary": "Stored XSS reported by @someone",
    "vulnerabilities": [{"patched_versions": "1.1.0", "vulnerable_version_range": "<= 1.0.0"}],
}


def test_security_from_an_advisory_patched_inside_the_sync_range():
    rels = sync.stable_releases([_rel("v1.0.0"), _rel("v1.1.0"), _rel("v1.2.0")])
    hits = sync.security_hits("v1.0.0", "v1.2.0", rels, [ADVISORY])
    assert len(hits) == 1 and "GHSA-aaaa-bbbb-cccc" in hits[0] and "`@someone`" in hits[0]
    assert (
        sync.security_hits("v1.1.0", "v1.2.0", rels, [ADVISORY]) == []
    )  # already patched in the base
    draft = {**ADVISORY, "state": "draft"}
    assert sync.security_hits("v1.0.0", "v1.2.0", rels, [draft]) == []


def test_security_from_release_notes_but_not_from_feature_words():
    rels = sync.stable_releases(
        [
            _rel("v1.0.0"),
            _rel(
                "v1.1.0",
                body="## Features\n- Row-level security for shared workspaces\n- Better security settings",
            ),
            _rel(
                "v1.2.0",
                body="## Fixes\n- Fix a vulnerability in the attachment preview (CVE-2026-12345)",
            ),
        ]
    )
    assert sync.security_hits("v1.0.0", "v1.1.0", rels, []) == []
    hits = sync.security_hits("v1.0.0", "v1.2.0", rels, [])
    assert hits == ["release notes of v1.2.0 mention: cve-2026-12345, vulnerability"]


# ---------------------------------------------------------------- report pieces


def test_notes_summary_counts_sections_and_keeps_before_you_update_defanged():
    rel = sync.Release(
        "v1.1.0",
        "https://example.test/v1.1.0",
        "2026-01-02",
        (
            "Intro\n## ✨ Highlight\n- one\n## Before you update\n- Set `X=1` first, by @dev (#12)\n"
            "## Features\n- a\n- b\n## Fixes\n- c\n"
        ),
    )
    lines = sync.notes_summary(rel)
    assert lines[0] == (
        "- [v1.1.0](https://example.test/v1.1.0) (2026-01-02): Highlight 1, Before you update 1, "
        "Features 2, Fixes 1"
    )
    assert lines[1:] == ["  Before you update:", "  > - Set `X=1` first, by `@dev` (`#12`)"]


def test_defang_leaves_emails_paths_and_code_alone():
    assert (
        sync.defang("by @a-b (#7), mail x@y.test, a/b#3, `#4`")
        == "by `@a-b` (`#7`), mail x@y.test, a/b#3, `#4`"
    )


INVENTORY = """# Upstream
## Patch inventory

| Patch | Issue / PR | Upstream files touched (hook) | Depends on | Why we carry it | Remove when |
| --- | --- | --- | --- | --- | --- |
| Hooks A | #4 | `models/account.py` (column), `frontend/src/pages/x.tsx` | none | why | when |
| Hooks B | #5 | `backend/app/api/reports.py` (model `A | B`), `docker-compose.yml` | none | why | when |
| Hooks C | #6 | `backend/app/api/other.py` (model `A \\| B`) | none | why | when |

## Next section

| Patch | Issue / PR | `backend/app/models/account.py` | x | y | z |
"""


def test_inventory_rows_survive_pipes_inside_code_and_stop_at_the_next_section():
    rows = sync.inventory_rows(INVENTORY)
    assert [name for name, _ in rows] == ["Hooks A", "Hooks B", "Hooks C"]
    assert sync.inventory_patches_for("backend/app/models/account.py", rows) == ["Hooks A"]
    assert sync.inventory_patches_for("backend/app/api/reports.py", rows) == ["Hooks B"]
    assert sync.inventory_patches_for("backend/app/api/other.py", rows) == ["Hooks C"]
    assert sync.inventory_patches_for("docker-compose.yml", rows) == ["Hooks B"]
    assert sync.inventory_patches_for("backend/app/models/account_extra.py", rows) == []


def test_conflict_kind():
    rows = sync.inventory_rows(INVENTORY)
    assert sync.conflict_kind("backend/alembic/versions/140_x.py", rows, set()).startswith(
        "migration"
    )
    assert sync.conflict_kind("docker-compose.yml", rows, set()) == "fork hook: Hooks B"
    assert "not in the patch inventory" in sync.conflict_kind(
        "backend/app/x.py", rows, {"backend/app/x.py"}
    )
    assert sync.conflict_kind("backend/app/y.py", rows, set()) == "upstream only"


def test_find_proposals_reads_only_marked_bodies():
    items = [
        {"number": 1, "body": "<!-- fork-sync tag=v1.1.0 kind=pr -->\ntext"},
        {"number": 2, "body": "other"},
    ]
    assert sync.find_proposals(items) == [{**items[0], "tag": "v1.1.0", "kind": "pr"}]


def test_security_goes_first_in_the_body_and_the_title():
    rep = sync.Report(
        base="v1.0.0", target="v1.1.0", lines=["### Release notes"], security=["advisory X"]
    )
    args = type("A", (), {"base_branch": "main"})()
    body = sync.proposal_body(rep, args, "pr", "sync/v1.1.0")
    assert body.splitlines()[0] == "<!-- fork-sync tag=v1.1.0 kind=pr -->"
    assert body.splitlines()[1].startswith("## Security release")
    assert sync.title(rep, "pr") == "chore(sync): merge upstream v1.1.0 [security]"
    assert sync.title(rep, "conflict") == "Upstream sync v1.1.0 [security]: merge conflicts"


# ---------------------------------------------------------------- git writes, on local repositories


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(repo, path, text, msg):
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text(text)
    _git(repo, "add", path)
    _git(repo, "commit", "-qm", msg)


@pytest.fixture
def repos(tmp_path, monkeypatch):
    """upstream with v1.0.0 and v1.1.0; origin (bare) with main = v1.0.0 plus a fork commit; a clone of origin."""
    for key, value in {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.test",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.test",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
    }.items():
        monkeypatch.setenv(key, value)
    up = tmp_path / "upstream"
    up.mkdir()
    _git(up, "init", "-q", "-b", "main")
    _commit(up, "app.py", "a = 1\nb = 2\n", "upstream base")
    _git(up, "tag", "-a", "v1.0.0", "-m", "v1.0.0")
    _commit(up, "app.py", "a = 1\nb = 3\n", "upstream change")
    _git(up, "tag", "-a", "v1.1.0", "-m", "v1.1.0")
    _commit(up, "later.py", "unreleased = True\n", "upstream unreleased")

    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", str(origin))
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(up), str(clone))
    _git(clone, "remote", "rename", "origin", "upstream")
    _git(clone, "remote", "add", "origin", str(origin))
    _git(clone, "checkout", "-q", "--detach", "v1.0.0")
    _commit(clone, "fork/feature.py", "fork = True\n", "fork feature")
    _git(
        clone,
        "push",
        "-q",
        "origin",
        "HEAD:refs/heads/main",
        "v1.0.0^{commit}:refs/heads/upstream-main",
    )
    _git(clone, "fetch", "-q", "origin")

    releases = [_rel("v1.0.0"), _rel("v1.1.0", body="## Fixes\n- b is 3 now")]
    monkeypatch.setattr(sync, "gh_json", lambda args: releases if "releases" in args[1] else [])
    monkeypatch.chdir(clone)
    return {"up": up, "origin": origin, "clone": clone, "out": tmp_path / "out"}


def _heads(origin):
    return dict(
        line.split()
        for line in _git(
            origin, "for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads"
        ).splitlines()
    )


def test_dry_run_writes_nothing(repos):
    before = _heads(repos["origin"])
    assert sync.main(["--publish", "files", "--out", str(repos["out"])]) == 0
    assert _heads(repos["origin"]) == before
    report = (repos["out"] / "report.md").read_text()
    assert "Base `v1.0.0`, target `v1.1.0`: clean merge." in report
    assert (repos["out"] / "pr-body.md").exists()
    assert (
        _git(repos["clone"], "worktree", "list").count("\n") == 0
    )  # the temporary worktree is gone


def test_clean_merge_pushes_the_sync_branch_and_fast_forwards_the_mirror(repos):
    main_before = _heads(repos["origin"])["main"]
    assert sync.main(["--apply", "--publish", "files", "--out", str(repos["out"])]) == 0
    heads = _heads(repos["origin"])
    assert heads["main"] == main_before
    assert heads["upstream-main"] == _git(repos["up"], "rev-parse", "main")
    parents = _git(repos["origin"], "rev-list", "--parents", "-n1", "sync/v1.1.0").split()[1:]
    assert parents == [main_before, _git(repos["up"], "rev-parse", "v1.1.0^{commit}")]
    assert _git(repos["origin"], "show", "sync/v1.1.0:app.py") == "a = 1\nb = 3"
    # A second run finds everything in place and pushes nothing new.
    assert sync.main(["--apply", "--publish", "files", "--out", str(repos["out"])]) == 0
    assert _heads(repos["origin"]) == heads
    assert "already up to date" in (repos["out"] / "report.md").read_text()


def test_conflict_pushes_nothing_and_writes_an_issue_body(repos):
    clone = repos["clone"]
    _git(clone, "checkout", "-q", "--detach", "origin/main")
    _commit(clone, "app.py", "a = 1\nb = 4\n", "fork edits the same line")
    _git(clone, "push", "-q", "origin", "HEAD:refs/heads/main")
    _git(clone, "fetch", "-q", "origin")
    assert sync.main(["--apply", "--publish", "files", "--out", str(repos["out"])]) == 0
    assert "sync/v1.1.0" not in _heads(repos["origin"])
    body = (repos["out"] / "issue-body.md").read_text()
    assert "<!-- fork-sync tag=v1.1.0 kind=conflict -->" in body
    assert "- `app.py`: upstream only" not in body and "`app.py`" in body
    assert _git(clone, "status", "--porcelain") == ""


def test_nothing_to_sync_reports_and_writes_nothing(repos):
    clone = repos["clone"]
    _git(clone, "checkout", "-q", "--detach", "origin/main")
    _git(clone, "merge", "-q", "--no-ff", "--no-edit", "v1.1.0")
    _git(clone, "push", "-q", "origin", "HEAD:refs/heads/main")
    _git(clone, "fetch", "-q", "origin")
    before = _heads(repos["origin"])
    assert sync.main(["--apply", "--publish", "files", "--out", str(repos["out"])]) == 0
    assert _heads(repos["origin"]) == before
    assert "Nothing to sync. Base `v1.1.0`" in (repos["out"] / "report.md").read_text()
