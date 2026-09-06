"""Console entry point for the bridge daemon, and skill installation."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import shutil
import sys
from collections.abc import Iterator
from importlib.resources import as_file, files
from pathlib import Path

DEFAULT_SKILL_DIR = Path.home() / ".claude" / "skills"

# Design notes that live alongside a skill in the repository. hatch's
# force-include ignores the build exclude list, so they reach the wheel; they
# should still not reach anyone's skills directory.
NOT_PART_OF_A_SKILL = shutil.ignore_patterns("PLAN.md", "__pycache__")


@contextlib.contextmanager
def skills_root() -> Iterator[Path | None]:
    """Where the skills are, installed or in a checkout.

    An installed wheel carries them inside the package. A checkout — including
    an editable install — has them at the repository root instead, because
    hatch's force-include only applies when building. Falling back means the
    command works for contributors too, on the skills they are editing.
    """
    packaged = files("bridge_for_agents") / "skills"
    if packaged.is_dir():
        with as_file(packaged) as path:
            yield path
        return
    repo = Path(__file__).resolve().parents[2] / "skills"
    yield repo if repo.is_dir() else None


def bundled_skills() -> list[str]:
    """Names of the skills this build can install."""
    with skills_root() as root:
        if root is None:
            return []
        return sorted(d.name for d in root.iterdir()
                      if d.is_dir() and (d / "SKILL.md").is_file())


def install_skills(dest: Path, force: bool = False) -> int:
    """Copy the bundled skills into a Claude Code skills directory.

    An existing skill is left alone unless `force` is given: someone may have
    edited theirs, and silently replacing it is worse than saying nothing
    happened.
    """
    with skills_root() as root:
        if root is None:
            print("No skills found in this build.", file=sys.stderr)
            return 1

        dest.mkdir(parents=True, exist_ok=True)
        installed, skipped = [], []
        for src in sorted(root.iterdir()):
            if not (src.is_dir() and (src / "SKILL.md").is_file()):
                continue
            target = dest / src.name
            if target.exists() and not force:
                skipped.append(src.name)
                continue
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(src, target, ignore=NOT_PART_OF_A_SKILL)
            installed.append(src.name)

    for name in installed:
        print(f"  installed {name} -> {dest / name}")
    for name in skipped:
        print(f"  skipped   {name} (already there; --force to replace)")
    if installed:
        print("\nRestart Claude Code, or start a new session, to pick them up.")
    elif skipped:
        print("\nNothing to do.")
    return 0


def cli() -> None:
    parser = argparse.ArgumentParser(
        prog="bridge-for-agents",
        description="Answer Claude Code's prompts from Telegram. "
                    "With no arguments, runs the bridge daemon.")
    sub = parser.add_subparsers(dest="command")
    inst = sub.add_parser("install-skills",
                          help="copy the bundled Claude Code skills into ~/.claude/skills")
    inst.add_argument("--dest", type=Path, default=DEFAULT_SKILL_DIR,
                      help=f"where to install (default: {DEFAULT_SKILL_DIR})")
    inst.add_argument("--force", action="store_true",
                      help="replace skills that are already installed")
    sub.add_parser("list-skills", help="show which skills this build carries")

    args = parser.parse_args()

    if args.command == "install-skills":
        raise SystemExit(install_skills(args.dest, args.force))
    if args.command == "list-skills":
        for name in bundled_skills() or ["(none)"]:
            print(f"  {name}")
        raise SystemExit(0)

    run()


def run() -> None:
    """Run the bridge until interrupted.

    The import is deferred because :mod:`bridge_for_agents.bridge` reads
    ``TG_BOT_TOKEN`` and friends from the environment at import time — so
    ``install-skills`` works without any of them set.
    """
    from .bridge import main

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
