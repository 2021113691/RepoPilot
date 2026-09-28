"""Recreate a clean Git baseline for each Day 2 toy repair case."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from repopilot.agent.loop import AgentLoop
from repopilot.agent.state import AgentState
from repopilot.models.base import ModelBackend


@dataclass(frozen=True)
class ToyCase:
    name: str
    fixture: Path
    issue: str
    targeted_test: str


ROOT = Path(__file__).resolve().parents[2]
CASES = {
    "email": ToyCase(
        "email", ROOT / "examples" / "toy_repo",
        "normalize_email() preserves uppercase domain letters: User@Example.COM should become User@example.com. Find the cause, patch it, and verify with pytest.",
        "tests/test_email_utils.py",
    ),
    "discount": ToyCase(
        "discount", ROOT / "examples" / "toy_cases" / "discount",
        "discounted_total(price, rate) must accept rate=0 and round half cents upward. For example, 2.05 at rate=0.5 should produce 1.03. Preserve rejection of invalid rates.",
        "tests/test_pricing.py",
    ),
    "regression": ToyCase(
        "regression", ROOT / "examples" / "toy_cases" / "regression",
        "slugify() should collapse repeated literal spaces into one hyphen, such as 'Hello  World' -> 'hello-world'. Preserve existing behavior for other characters.",
        "tests/test_slug_target.py",
    ),
}


def prepare_case(name: str, runs_dir: Path = ROOT / "runs") -> tuple[ToyCase, Path]:
    if name not in CASES:
        raise ValueError(f"unknown toy case: {name}")
    case = CASES[name]
    target = runs_dir.resolve() / "toy_cases" / f"{name}-{uuid4().hex[:12]}"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(case.fixture, target)
    (target / ".gitignore").write_text("__pycache__/\n*.pyc\n.pytest_cache/\n", encoding="utf-8")
    (target / "pyproject.toml").write_text('[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=target, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=target, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=RepoPilot Toy", "-c", "user.email=toy@localhost", "commit", "-qm", "toy baseline"],
        cwd=target, check=True, capture_output=True,
    )
    return case, target


def run_case(name: str, backend: ModelBackend, runs_dir: Path = ROOT / "runs", **loop_options) -> tuple[AgentState, Path]:
    case, workspace = prepare_case(name, runs_dir)
    state = AgentLoop(backend, workspace, runs_dir=runs_dir, repair=True, **loop_options).run(case.issue)
    return state, workspace
