"""Installing the bundled skills.

`pip install` gives you a daemon; without this the skills that tell an agent
how to drive it are only in a repository the user does not have.
"""
from __future__ import annotations

import pytest

from bridge_for_agents import cli


def test_the_package_carries_both_skills():
    assert cli.bundled_skills() == ["bridge-ops", "notify-user"]


def test_install_copies_them(tmp_path, capsys):
    assert cli.install_skills(tmp_path) == 0
    for name in ("bridge-ops", "notify-user"):
        assert (tmp_path / name / "SKILL.md").is_file()
    assert (tmp_path / "bridge-ops" / "references" / "diagnose.md").is_file()
    assert "installed" in capsys.readouterr().out


def test_design_notes_do_not_reach_a_skills_directory(tmp_path):
    """PLAN.md sits beside the skill in the repo and rides along in the wheel,
    because force-include ignores the build exclude list. It should not end up
    in anyone's skills directory."""
    cli.install_skills(tmp_path)
    assert list(tmp_path.rglob("PLAN.md")) == []


def test_an_existing_skill_is_left_alone(tmp_path, capsys):
    cli.install_skills(tmp_path)
    edited = tmp_path / "notify-user" / "SKILL.md"
    edited.write_text("mine now")

    assert cli.install_skills(tmp_path) == 0
    assert edited.read_text() == "mine now", "clobbered a skill the user may have edited"
    assert "skipped" in capsys.readouterr().out


def test_force_replaces_it(tmp_path):
    cli.install_skills(tmp_path)
    edited = tmp_path / "notify-user" / "SKILL.md"
    edited.write_text("mine now")

    cli.install_skills(tmp_path, force=True)
    assert edited.read_text() != "mine now"
    assert "notify-user" in edited.read_text()


def test_a_missing_destination_is_created(tmp_path):
    dest = tmp_path / "deep" / "not" / "there"
    assert cli.install_skills(dest) == 0
    assert (dest / "notify-user" / "SKILL.md").is_file()


def test_install_codex_hooks_copies_the_example(tmp_path, capsys):
    dest = tmp_path / ".codex" / "hooks.json"
    assert cli.install_codex_hooks(dest) == 0
    assert "PermissionRequest" in dest.read_text()
    assert "bridge-for-agents-hook" in dest.read_text()
    assert "installed Codex hooks" in capsys.readouterr().out


def test_install_codex_hooks_does_not_replace_an_existing_file(tmp_path, capsys):
    dest = tmp_path / "hooks.json"
    dest.write_text("mine")
    assert cli.install_codex_hooks(dest) == 1
    assert dest.read_text() == "mine"
    assert "Refusing" in capsys.readouterr().err


def test_force_replaces_existing_codex_hooks(tmp_path):
    dest = tmp_path / "hooks.json"
    dest.write_text("mine")
    assert cli.install_codex_hooks(dest, force=True) == 0
    assert "PermissionRequest" in dest.read_text()


@pytest.mark.parametrize("argv,expected", [
    (["list-skills"], 0),
    (["install-skills", "--dest"], None),      # --dest needs a value
    (["install-codex-hooks", "--dest"], None),
])
def test_cli_parses_the_subcommands(monkeypatch, tmp_path, argv, expected, capsys):
    if argv[-1] == "--dest":
        dest = tmp_path / "hooks.json" if argv[0] == "install-codex-hooks" else tmp_path
        argv = argv + [str(dest)]
        expected = 0
    monkeypatch.setattr("sys.argv", ["bridge-for-agents", *argv])
    with pytest.raises(SystemExit) as e:
        cli.cli()
    assert e.value.code == expected


def test_codex_client_uses_the_agents_skill_directory(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setitem(cli.SKILL_DIRS, "codex", tmp_path / ".agents" / "skills")
    monkeypatch.setattr(cli, "install_skills", lambda dest, force=False: seen.append(dest) or 0)
    monkeypatch.setattr("sys.argv", ["bridge-for-agents", "install-skills",
                                    "--client", "codex"])
    with pytest.raises(SystemExit) as error:
        cli.cli()
    assert error.value.code == 0
    assert seen == [tmp_path / ".agents" / "skills"]


def test_bare_invocation_still_runs_the_daemon(monkeypatch):
    """The daemon is the default; adding subcommands must not change that."""
    called = []
    monkeypatch.setattr("sys.argv", ["bridge-for-agents"])
    monkeypatch.setattr(cli, "run", lambda: called.append(True))
    cli.cli()
    assert called == [True]


def test_a_checkout_finds_the_skills_at_the_repository_root():
    """Editable installs have no skills inside the package — force-include only
    applies when a wheel is built. The command still has to work."""
    from importlib.resources import files

    packaged = files("bridge_for_agents") / "skills"
    with cli.skills_root() as root:
        assert root is not None
        assert (root / "notify-user" / "SKILL.md").is_file()
        if not packaged.is_dir():
            assert root.name == "skills" and (root.parent / "pyproject.toml").is_file()
