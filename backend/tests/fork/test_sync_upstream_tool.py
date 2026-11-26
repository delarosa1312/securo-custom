"""fork/tools/sync_upstream.py, the upstream release sync (D-014): selection, security, report, proposals, git writes.

The git tests build tiny local repositories (an "upstream", a bare "origin" and a clone) and fake the GitHub reads;
the proposal tests fake `gh` and record its calls. Nothing leaves the machine. Synthetic content only.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

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
    rels = sync.stable_releases(
        [_rel("v1.0.0"), _rel("v1.1.0"), _rel("v1.2.0"), _rel("v1.3.0", prerelease=True)]
    )
    assert sync.pick_target("v1.0.0", rels).tag == "v1.2.0"
    assert sync.pick_target("v1.2.0", rels) is None
    assert sync.pick_target("v1.0.0", rels, forced="v1.1.0").tag == "v1.1.0"
    for forced in (
        "v1.0.0",
        "v1.2.0-rc.1",
        "v1.3.0",
        "v9.9.9",
    ):  # older, suffixed, prerelease, unpublished
        with pytest.raises(SystemExit):
            sync.pick_target("v1.0.0" if forced != "v1.0.0" else "v1.1.0", rels, forced=forced)


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
    assert sync.security_hits("v1.0.0", "v1.2.0", rels, [{**ADVISORY, "state": "draft"}]) == []


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
        "Intro\n## ✨ Highlight\n- one\n## Before you update\n- Set `X=1` first, by @dev (#12)\n"
        "## Features\n- a\n- b\n## Fixes\n- c\n",
    )
    lines = sync.notes_summary(rel)
    assert lines[0] == (
        "- [v1.1.0](https://example.test/v1.1.0) (2026-01-02): Highlight 1, Before you update 1, Features 2, Fixes 1"
    )
    assert lines[1:] == ["  Before you update:", "  > - Set `X=1` first, by `@dev` (`#12`)"]


@pytest.mark.parametrize(
    ("raw", "safe"),
    [
        ("by @a-b (#7), mail x@y.test", "by `@a-b` (`#7`), mail x@y.test"),
        (
            "run `npm i @tanstack/query` and `SELECT 1 #2`",
            "run `npm i @tanstack/query` and `SELECT 1 #2`",
        ),
        ("see securo-finance/securo#871 and GH-12", "see `securo-finance/securo#871` and `GH-12`"),
        (
            "[fix](https://github.com/securo-finance/securo/pull/1742)",
            "[fix](`https://github.com/securo-finance/securo/pull/1742`)",
        ),
        ("a/b/c.py#L3 and &#39;", "a/b/c.py#L3 and &#39;"),
    ],
)
def test_defang_neutralises_mentions_and_references_but_not_code(raw, safe):
    assert sync.defang(raw) == safe


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


def _args(**kw):
    return SimpleNamespace(
        **{"base_branch": "main", "publish": "github", "dispatch_ci": False, **kw}
    )


def test_security_goes_first_in_the_block_and_the_title():
    rep = sync.Report(
        base="v1.0.0", target="v1.1.0", lines=["### Release notes"], security=["advisory X"]
    )
    block = sync.proposal_block(rep, _args(), "pr", "sync/v1.1.0", "o/fork")
    assert block.splitlines()[0] == "<!-- fork-sync tag=v1.1.0 kind=pr -->"
    assert block.splitlines()[1].startswith("## Security release")
    assert block.rstrip().endswith("<!-- fork-sync:end -->")
    assert sync.title(rep, "pr") == "chore(sync): merge upstream v1.1.0 [security]"
    rep.checks_failed = True
    assert (
        sync.title(rep, "conflict")
        == "Upstream sync v1.1.0 [security] [checks failed]: merge conflicts"
    )


def test_changes_by_hand_are_called_out_not_hidden():
    rep = sync.Report(base="v1.0.0", target="v1.1.0", by_hand=["app.py"])
    block = sync.proposal_block(rep, _args(), "pr", "sync/v1.1.0", "o/fork")
    assert (
        "plus the changes by hand listed below" in block and "nothing changed by hand" not in block
    )


def test_merge_body_keeps_text_written_by_hand():
    old_block = "<!-- fork-sync tag=v1.1.0 kind=pr -->\nold\n<!-- fork-sync:end -->\n"
    new_block = "<!-- fork-sync tag=v1.1.0 kind=conflict -->\nnew\n<!-- fork-sync:end -->\n"
    notes = "\n## Results\n- suites green, E2E green\n"
    assert sync.merge_body(old_block + notes, new_block) == new_block + notes
    assert sync.merge_body("Opened by hand.", new_block) == new_block + "\nOpened by hand."
    assert sync.merge_body(new_block + notes, new_block) == new_block + notes


def test_the_dispatched_ci_run_is_linked_in_the_pull_request_block():
    line = sync.ci_runs_line(
        "owner/fork", "sync/v1.1.0", "https://github.com/owner/fork/actions/runs/9"
    )
    assert "[this commit's run](https://github.com/owner/fork/actions/runs/9)" in line
    assert "actions/workflows/ci.yml?query=branch%3Async%2Fv1.1.0" in line
    rep = sync.Report(base="v1.0.0", target="v1.1.0", ci=line)
    assert line in sync.proposal_block(rep, _args(), "pr", "sync/v1.1.0", "owner/fork").splitlines()


# ---------------------------------------------------------------- proposals on GitHub (gh faked)


def _p(number, tag, is_pr, state="OPEN", body=None, marked=True, kind="pr"):
    body = (
        body
        if body is not None
        else f"<!-- fork-sync tag={tag} kind={kind} -->\nx\n<!-- fork-sync:end -->\n"
    )
    return sync.Proposal(
        number,
        f"https://github.com/o/fork/{'pull' if is_pr else 'issues'}/{number}",
        is_pr,
        state,
        tag,
        body,
        marked,
        kind,
    )


def test_classify_reads_markers_and_adopts_pull_requests_from_sync_branches():
    prs = [
        {
            "number": 1,
            "url": "u1",
            "body": "<!-- fork-sync tag=v1.1.0 kind=pr -->",
            "headRefName": "sync/v1.1.0",
            "state": "OPEN",
        },
        {
            "number": 2,
            "url": "u2",
            "body": "Opened by hand",
            "headRefName": "sync/v1.2.0",
            "state": "OPEN",
        },
        {"number": 3, "url": "u3", "body": "feature", "headRefName": "feat/3-x", "state": "OPEN"},
    ]
    issues = [{"number": 4, "url": "u4", "body": "no marker", "state": "OPEN"}]
    got = sync.classify(prs, issues)
    assert [(p.number, p.tag, p.marked) for p in got] == [(1, "v1.1.0", True), (2, "v1.2.0", False)]


def test_closed_by_hand():
    assert sync.closed_by_hand([_p(1, "v1.1.0", True, "CLOSED")], "v1.1.0").number == 1
    assert sync.closed_by_hand([_p(2, "v1.1.0", False, "CLOSED")], "v1.1.0").number == 2
    # What the tool closed itself carries kind=superseded: that is not a person's veto.
    closed_by_tool = _p(2, "v1.1.0", False, "CLOSED", kind="superseded")
    assert sync.closed_by_hand([closed_by_tool], "v1.1.0") is None
    assert sync.closed_by_hand([_p(1, "v1.0.0", True, "CLOSED")], "v1.1.0") is None


@pytest.fixture
def fake_gh(monkeypatch, tmp_path):
    calls = []
    refuse = set()

    def run(cmd, cwd=None, check=True, env=None, scrub=False):
        calls.append(cmd)
        verb = " ".join(cmd[1:3])
        body_file = cmd[cmd.index("--body-file") + 1] if "--body-file" in cmd else None
        if body_file:
            calls[-1] = [*cmd, "BODY=" + Path(body_file).read_text()]
        if verb in refuse:
            return subprocess.CompletedProcess(cmd, 1, "", "refused")
        out = {
            "pr create": "https://github.com/o/fork/pull/50\n",
            "issue create": "https://github.com/o/fork/issues/51\n",
        }
        return subprocess.CompletedProcess(cmd, 0, out.get(verb, ""), "")

    monkeypatch.setattr(sync, "run", run)
    return SimpleNamespace(calls=calls, refuse=refuse, out=tmp_path)


def _verbs(calls):
    return [" ".join(c[1:4]) for c in calls if c[0] == "gh" and c[1] != "label"]


def _publish(fake_gh, kind, props, **rep_kw):
    rep = sync.Report(base="v1.0.0", target="v1.1.0", **rep_kw)
    sync.publish(rep, _args(), "o/fork", fake_gh.out, "sync/v1.1.0", kind, True, "abc", props)
    for call in fake_gh.calls:
        if call[0] == "gh":
            assert call[call.index("-R") + 1] == "o/fork"  # every write targets the fork
    return rep


def test_first_clean_sync_opens_a_pull_request(fake_gh):
    _publish(fake_gh, "pr", [])
    assert _verbs(fake_gh.calls) == ["pr create -R"]


def test_rerun_keeps_notes_by_hand_and_skips_an_unchanged_body(fake_gh):
    rep = sync.Report(base="v1.0.0", target="v1.1.0")
    block = sync.proposal_block(rep, _args(), "pr", "sync/v1.1.0", "o/fork")
    _publish(fake_gh, "pr", [_p(7, "v1.1.0", True, body=block + "\nResults: green.\n")])
    assert _verbs(fake_gh.calls) == []
    _publish(
        fake_gh,
        "pr",
        [
            _p(
                7,
                "v1.1.0",
                True,
                body="<!-- fork-sync tag=v1.1.0 kind=pr -->\nold\n"
                "<!-- fork-sync:end -->\nResults: green.\n",
            )
        ],
    )
    edit = fake_gh.calls[-1]
    assert edit[1:4] == ["pr", "edit", "7"] and edit[-1].endswith("Results: green.\n")


def test_a_conflict_after_the_pull_request_opened_goes_into_it_not_a_new_issue(fake_gh):
    _publish(fake_gh, "conflict", [_p(7, "v1.1.0", True)], conflicts=["app.py"])
    assert _verbs(fake_gh.calls) == ["pr edit 7"]
    edit = fake_gh.calls[-1]
    assert edit[edit.index("--title") + 1].endswith("[conflicts]") and "kind=conflict" in edit[-1]


def test_a_newer_pull_request_replaces_older_proposals_and_its_own_issue(fake_gh):
    props = [
        _p(3, "v1.0.5", True),
        _p(4, "v1.0.5", False),
        _p(5, "v1.1.0", False),
        _p(6, "v1.2.0", False),
        _p(8, "v1.0.1", True, state="CLOSED"),
    ]
    rep = _publish(fake_gh, "pr", props)
    assert _verbs(fake_gh.calls) == [
        "pr create -R",
        "pr edit 3",
        "pr close 3",
        "issue edit 4",
        "issue close 4",
        "issue edit 5",
        "issue close 5",
    ]
    marked = [c for c in fake_gh.calls if c[1:3] == ["pr", "edit"]][0][-1]
    assert marked.startswith("BODY=<!-- fork-sync tag=v1.0.5 kind=superseded -->")
    assert sum("closed superseded" in a for a in rep.actions) == 3


def test_a_newer_conflict_replaces_an_older_pull_request(fake_gh):
    _publish(fake_gh, "conflict", [_p(3, "v1.0.5", True)], conflicts=["app.py"])
    assert _verbs(fake_gh.calls) == ["issue create -R", "pr edit 3", "pr close 3"]


def test_a_superseded_proposal_reopened_by_a_person_stays_open(fake_gh):
    _publish(fake_gh, "pr", [_p(3, "v1.0.5", True, kind="superseded")])
    assert _verbs(fake_gh.calls) == ["pr create -R"]


def test_a_refused_pull_request_becomes_an_issue_with_the_link_to_open_it(fake_gh):
    fake_gh.refuse.add("pr create")
    rep = _publish(fake_gh, "pr", [])
    assert _verbs(fake_gh.calls) == ["pr create -R", "issue create -R"]
    body = fake_gh.calls[-1][-1]
    assert (
        "kind=open-pr" in body
        and "https://github.com/o/fork/compare/main...sync/v1.1.0?expand=1" in body
    )
    assert any("refused" in w for w in rep.warnings)


def test_a_failed_close_is_reported_not_claimed(fake_gh):
    fake_gh.refuse.add("pr close")
    rep = _publish(fake_gh, "pr", [_p(3, "v1.0.5", True)])
    assert not any("closed superseded" in a for a in rep.actions)
    assert any("could not close" in w for w in rep.warnings)


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
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.test",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.test",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "RUNNER_TEMP": str(tmp_path / "runner"),
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    up = tmp_path / "upstream"
    up.mkdir()
    _git(up, "init", "-q", "-b", "main")
    _commit(up, "backend/scripts/check_migration_chain.py", "print('ok')\n", "upstream chain check")
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
    _commit(clone, "fork/tools/check_fork_migrations.py", "print('ok')\n", "fork chain check")
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
    return SimpleNamespace(up=up, origin=origin, clone=clone, out=tmp_path / "out")


def _refs(origin):
    out = _git(origin, "for-each-ref", "--format=%(refname) %(objectname)")
    return dict(line.split() for line in out.splitlines())


def _sync(repos, *extra):
    return sync.main(["--publish", "files", "--out", str(repos.out), *extra])


def _move_main(repos, text):
    clone = repos.clone
    _git(clone, "checkout", "-q", "--detach", "origin/main")
    _commit(clone, "app.py", text, "fork edits the same line")
    _git(clone, "push", "-q", "origin", "HEAD:refs/heads/main")
    _git(clone, "fetch", "-q", "origin")


def test_dry_run_writes_nothing(repos):
    before = _refs(repos.origin)
    assert _sync(repos) == 0
    assert _refs(repos.origin) == before
    report = (repos.out / "report.md").read_text()
    assert "Base `v1.0.0`, target `v1.1.0`: clean merge." in report
    assert (repos.out / "pr-body.md").exists()
    assert _git(repos.clone, "worktree", "list").count("\n") == 0  # the temporary worktree is gone


def test_clean_merge_pushes_the_sync_branch_and_fast_forwards_the_mirror_without_tags(repos):
    _git(repos.clone, "config", "push.followTags", "true")
    main_before = _refs(repos.origin)["refs/heads/main"]
    assert _sync(repos, "--apply") == 0
    refs = _refs(repos.origin)
    assert refs["refs/heads/main"] == main_before
    assert refs["refs/heads/upstream-main"] == _git(repos.up, "rev-parse", "main")
    assert not [r for r in refs if r.startswith("refs/tags/")]
    parents = _git(repos.origin, "rev-list", "--parents", "-n1", "sync/v1.1.0").split()[1:]
    assert parents == [main_before, _git(repos.up, "rev-parse", "v1.1.0^{commit}")]
    assert _git(repos.origin, "show", "sync/v1.1.0:app.py") == "a = 1\nb = 3"
    assert "- migration chain (fork line): ok" in (repos.out / "pr-body.md").read_text()
    # A second run finds everything in place and pushes nothing new.
    _sync(repos, "--apply")
    assert _refs(repos.origin) == refs
    assert "already up to date" in (repos.out / "report.md").read_text()


def test_conflict_pushes_nothing_and_writes_an_issue_body(repos):
    _move_main(repos, "a = 1\nb = 4\n")
    assert _sync(repos, "--apply") == 0
    assert "refs/heads/sync/v1.1.0" not in _refs(repos.origin)
    body = (repos.out / "issue-body.md").read_text()
    assert "<!-- fork-sync tag=v1.1.0 kind=conflict -->" in body
    assert "- `app.py`: changed by the fork but not in the patch inventory" in body
    assert _git(repos.clone, "status", "--porcelain") == ""


def test_a_conflict_resolved_by_hand_is_listed_for_review(repos):
    _move_main(repos, "a = 1\nb = 4\n")
    clone = repos.clone
    subprocess.run(
        ["git", "merge", "--no-ff", "-m", "Merge upstream v1.1.0 into main", "v1.1.0"], cwd=clone
    )
    (clone / "app.py").write_text("a = 1\nb = 34\n")
    _git(clone, "add", "app.py")
    _git(clone, "commit", "-qm", "resolved by hand")
    _git(clone, "push", "-q", "origin", "HEAD:refs/heads/sync/v1.1.0")
    _git(clone, "fetch", "-q", "origin")
    _sync(repos, "--apply")
    body = (repos.out / "pr-body.md").read_text()
    assert "### Changed by hand on `sync/v1.1.0` (1)" in body and "- `app.py`" in body
    assert "plus the changes by hand listed below" in body


def test_committed_conflict_markers_are_refused(repos):
    clone = repos.clone
    _git(clone, "checkout", "-q", "--detach", "origin/main")
    _git(clone, "merge", "-q", "--no-ff", "--no-edit", "v1.1.0")
    _commit(clone, "app.py", "<<<<<<< HEAD\nb = 3\n=======\nb = 4\n>>>>>>> x\n", "markers")
    with pytest.raises(sync.Fail, match="conflict markers"):
        sync.assert_clean(sync.Git(clone), "origin/main", "v1.1.0")


def test_the_write_job_needs_the_tree_the_check_job_tested(repos):
    assert sync.main(["--checks-only", "--out", str(repos.out)]) == 0
    checks = json.loads((repos.out / "checks.json").read_text())
    assert checks["target"] == "v1.1.0" and checks["tree"] and checks["failed"] is False
    before = _refs(repos.origin)
    stale = repos.out / "stale.json"
    stale.write_text(json.dumps({**checks, "tree": "0" * 40}))
    assert _sync(repos, "--apply", "--checks-from", str(stale)) == 1
    assert _refs(repos.origin) == before
    assert "nothing written" in (repos.out / "report.md").read_text()
    assert _sync(repos, "--apply", "--checks-from", str(repos.out / "checks.json")) == 0
    assert "refs/heads/sync/v1.1.0" in _refs(repos.origin)


def test_a_failed_check_is_published_loudly_and_fails_the_run(repos):
    clone = repos.clone
    _git(clone, "checkout", "-q", "--detach", "origin/main")
    _commit(
        clone,
        "fork/tools/check_fork_migrations.py",
        "raise SystemExit('fork line broken')\n",
        "break it",
    )
    _git(clone, "push", "-q", "origin", "HEAD:refs/heads/main")
    _git(clone, "fetch", "-q", "origin")
    assert _sync(repos, "--apply") == 1
    body = (repos.out / "pr-body.md").read_text()
    assert "- migration chain (fork line): FAILED" in body and "fork line broken" in body
    assert "[checks failed]" in (repos.out / "report.md").read_text()


def test_nothing_to_sync_reports_and_writes_nothing(repos):
    clone = repos.clone
    _git(clone, "checkout", "-q", "--detach", "origin/main")
    _git(clone, "merge", "-q", "--no-ff", "--no-edit", "v1.1.0")
    _git(clone, "push", "-q", "origin", "HEAD:refs/heads/main")
    _git(clone, "fetch", "-q", "origin")
    before = _refs(repos.origin)
    assert _sync(repos, "--apply") == 0
    assert _refs(repos.origin) == before
    assert "Nothing to sync. Base `v1.1.0`" in (repos.out / "report.md").read_text()


def test_refuses_upstream_and_github_pushes_in_rehearsal_mode(repos):
    before = _refs(repos.origin)
    assert sync.main(["--apply", "--repo", "securo-finance/securo", "--out", str(repos.out)]) == 1
    assert "refusing to work on upstream" in (repos.out / "report.md").read_text()
    _git(repos.clone, "remote", "set-url", "origin", "https://github.com/someone/fork.git")
    assert _sync(repos, "--apply") == 1
    assert "for rehearsals" in (repos.out / "report.md").read_text()
    assert _refs(repos.origin) == before
