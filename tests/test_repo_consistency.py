"""Preflight repo-consistency guard (WU-REPO-GUARD).

Test contract (PRD §7 safety guard): a conforming PRD must target the repo blacksmith is
pointed at. The guard reads the configured ``[target].repo_path``'s ``origin`` remote and
compares its ``owner/name`` slug to the PRD's ``primary_target_repo`` — SSH and HTTPS forms
of the same slug are equal. A match passes; a mismatch aborts (the guard raises, and
``main()`` exits non-zero) with a message naming both the configured remote slug and the
PRD's expected owner/repo. The guard runs BEFORE graph execution, so a mismatch never
reaches a worktree or the model.
"""

import subprocess

import pytest

from blacksmith.cli import RepoConsistencyError, check_repo_consistency, main
from blacksmith.worktree import WorktreeManager, normalize_remote_slug

PRD_TEMPLATE = """\
---
contract_version: 1
component: demo
version: v0
primary_target_repo: {repo}
layers:
  py-logic: auto
untouchables:
  - "do not touch the brand files"
work_units:
  - id: WU-E1
    title: "trivial unit"
    layers: [py-logic]
    target_modules: ["out.txt"]
    test_contract: "the gate command passes"
    depends_on: []
---
# Demo PRD

## 1. Purpose
demo.

## 2. Scope fences
demo.

## 7. Untouchables
none.

## 10. Acceptance criteria
done.
"""


def _git(repo, *args):
    subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )


def _repo_with_origin(tmp_path, origin_url):
    repo = tmp_path / "target"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "remote", "add", "origin", origin_url)
    (repo / "README.md").write_text("x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    return repo


def _write_prd(tmp_path, repo_slug):
    path = tmp_path / "prd.md"
    path.write_text(PRD_TEMPLATE.format(repo=repo_slug))
    return path


def _write_config(tmp_path, repo):
    cfg = tmp_path / "blacksmith.config.toml"
    cfg.write_text(f'[target]\nrepo_path = "{repo}"\n')
    return cfg


# --- the slug normalizer treats SSH and HTTPS as equal ------------------------


@pytest.mark.parametrize(
    "url",
    [
        "git@github.com:owner/name.git",
        "git@github.com:owner/name",
        "https://github.com/owner/name.git",
        "https://github.com/owner/name",
        "https://github.com/owner/name/",
        "ssh://git@github.com/owner/name.git",
        "owner/name",
        "OWNER/Name",
    ],
)
def test_normalize_remote_slug_equates_forms(url):
    assert normalize_remote_slug(url) == "owner/name"


def test_normalize_remote_slug_returns_none_for_unusable():
    assert normalize_remote_slug("") is None
    assert normalize_remote_slug("nope") is None


# --- the preflight passes / raises on the function --------------------------


def test_preflight_passes_on_matching_remote(tmp_path):
    repo = _repo_with_origin(tmp_path, "https://github.com/owner/name.git")
    mgr = WorktreeManager(repo, base_dir=tmp_path / "wt")
    assert check_repo_consistency(mgr, "owner/name") == "owner/name"


def test_preflight_passes_when_ssh_remote_matches_https_prd(tmp_path):
    repo = _repo_with_origin(tmp_path, "git@github.com:owner/name.git")
    mgr = WorktreeManager(repo, base_dir=tmp_path / "wt")
    # PRD carries the HTTPS form of the same repo — must be treated as equal.
    assert check_repo_consistency(mgr, "https://github.com/owner/name") == "owner/name"


def test_preflight_raises_on_mismatch_naming_both_slugs(tmp_path):
    repo = _repo_with_origin(tmp_path, "git@github.com:owner/name.git")
    mgr = WorktreeManager(repo, base_dir=tmp_path / "wt")
    with pytest.raises(RepoConsistencyError) as excinfo:
        check_repo_consistency(mgr, "other/elsewhere")
    message = str(excinfo.value)
    assert "owner/name" in message  # the configured remote slug
    assert "other/elsewhere" in message  # the PRD's expected owner/repo


def test_preflight_raises_when_no_remote(tmp_path):
    repo = tmp_path / "target"
    repo.mkdir()
    _git(repo, "init", "-b", "main")  # no 'origin' remote configured
    mgr = WorktreeManager(repo, base_dir=tmp_path / "wt")
    with pytest.raises(RepoConsistencyError):
        check_repo_consistency(mgr, "owner/name")


# --- main() exits non-zero on a mismatch, before any worktree/model spend ----


def test_main_exits_nonzero_on_mismatch(tmp_path, capsys):
    repo = _repo_with_origin(tmp_path, "git@github.com:owner/name.git")
    cfg = _write_config(tmp_path, repo)
    prd = _write_prd(tmp_path, "other/elsewhere")

    code = main([str(prd), "--config", str(cfg), "--auto-approve", "--quiet"])

    assert code == 1
    err = capsys.readouterr().err
    assert "owner/name" in err
    assert "other/elsewhere" in err
    # The guard aborts before graph execution: no worktree directory is ever created.
    assert not (tmp_path / "wt").exists()
    assert not (repo.parent / "target-worktrees").exists()
