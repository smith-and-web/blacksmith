"""S18 keeps help/argument parsing while refusing execution before any effect."""

import pytest

from blacksmith import cli
from blacksmith.config import BlacksmithConfig
from blacksmith.executor import Executor
from blacksmith.respond import RESPOND_UNSUPPORTED


def forbidden(*args, **kwargs):
    pytest.fail("disabled respond reached config, credentials, GitHub, model or workspace")


def block_execution(monkeypatch):
    for name in ["load_dotenv", "_load_config", "Executor", "parse_prd", "run_respond"]:
        monkeypatch.setattr(cli, name, forbidden)


@pytest.mark.parametrize("extra", [
    [], ["--prd", "missing-prd.md"], ["--config", "missing-runtime.toml"],
    ["--repo", "example/project"],
    ["--prd", "missing-prd.md", "--config", "missing-runtime.toml", "--repo", "example/project"],
])
def test_respond_refuses_before_reading_inputs_or_constructing_dependencies(
    tmp_path, monkeypatch, capsys, extra,
):
    monkeypatch.chdir(tmp_path)
    block_execution(monkeypatch)
    assert cli.main(["respond", "--pr", "42", *extra]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err.strip() == "respond: " + RESPOND_UNSUPPORTED
    assert list(tmp_path.iterdir()) == []


def test_respond_help_is_available_without_loading_configuration(monkeypatch, capsys):
    block_execution(monkeypatch)
    with pytest.raises(SystemExit) as result:
        cli.main(["respond", "--help"])
    assert result.value.code == 0
    output = capsys.readouterr()
    assert "unsupported" in output.out.lower()
    assert "--pr" in output.out and "--prd" in output.out and "--config" in output.out
    assert output.err == ""


def test_respond_requires_pr_flag_before_any_execution(monkeypatch, capsys):
    block_execution(monkeypatch)
    with pytest.raises(SystemExit) as result:
        cli.main(["respond"])
    assert result.value.code == 2
    assert "--pr" in capsys.readouterr().err


@pytest.mark.parametrize("contract", [None, object()])
def test_programmatic_wrapper_refuses_before_branch_lookup_or_flow(
    tmp_path, monkeypatch, contract,
):
    config = BlacksmithConfig()
    executor = Executor(config)
    monkeypatch.setattr(cli, "_pr_branch", forbidden)
    monkeypatch.setattr(cli, "_render_respond_result", forbidden)
    output = []
    assert cli.run_respond(42, config=config, repo_path=tmp_path, executor=executor,
                           contract=contract, pr_runner=forbidden, gate=forbidden,
                           fix=forbidden, clone_manager=object(), out=output.append) == 1
    assert output == ["respond: " + RESPOND_UNSUPPORTED]
    assert list(tmp_path.iterdir()) == []
