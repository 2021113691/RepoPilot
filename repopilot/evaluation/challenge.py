"""Offline challenge qualification and post-run labels; never passed to AgentLoop."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from repopilot.context import SymbolRetriever
from repopilot.context.dynamic import DynamicRetriever
from repopilot.context.failure import FailureEvidence, extract_failure_evidence
from repopilot.context.lexical import RetrievedContext
from repopilot.evaluation.challenge_cases import CASES, ChallengeCase, ROOT
from repopilot.tools.tests import RunTests

GOLD_PATH = Path(__file__).with_name("challenge_gold.json")


def load_gold() -> dict[str, dict]:
    """Evaluation-only labels, intentionally outside every cloned repository."""
    return json.loads(GOLD_PATH.read_text(encoding="utf-8"))


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
    return result.stdout.strip()


def _fixture_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(path for path in root.rglob("*") if path.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def prepare_baseline(case: ChallengeCase, runs_dir: Path) -> tuple[Path, str]:
    base_dir = runs_dir.resolve() / "baselines"
    base_dir.mkdir(parents=True, exist_ok=True)
    target = base_dir / case.case_id
    manifest_path = base_dir / f"{case.case_id}.json"
    fixture_hash = _fixture_hash(case.fixture)
    if not target.exists():
        if manifest_path.exists():
            raise ValueError("challenge baseline manifest exists without repository")
        shutil.copytree(case.fixture, target)
        _git(target, "init", "-q", "-b", "main")
        _git(target, "add", ".")
        _git(target, "-c", "user.name=RepoPilot Challenge", "-c", "user.email=challenge@localhost", "commit", "-qm", "baseline")
        manifest_path.write_text(json.dumps({"fixture_hash": fixture_hash, "baseline_commit": _git(target, "rev-parse", "HEAD"), "issue": case.issue}, indent=2) + "\n", encoding="utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    commit = _git(target, "rev-parse", "HEAD")
    if manifest != {"fixture_hash": fixture_hash, "baseline_commit": commit, "issue": case.issue}:
        raise ValueError(f"fixture or issue changed after baseline freeze: {case.case_id}")
    if _git(target, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError(f"baseline is dirty: {case.case_id}")
    return target, commit


def ranked_files(context: RetrievedContext) -> list[str]:
    return list(dict.fromkeys(item.file for item in context.items))


def rank(files: list[str], file: str) -> int | None:
    return next((index for index, value in enumerate(files, 1) if value == file), None)


def evidence_novelty(issue: str, initial: RetrievedContext, evidence: FailureEvidence) -> dict[str, list[str]]:
    issue_lower = issue.lower()
    initial_files = set(ranked_files(initial))
    initial_text = "\n".join(item.content for item in initial.items).lower()
    files = [file for file in evidence.mentioned_files if file not in initial_files and file.lower() not in issue_lower]
    symbols = [symbol for symbol in evidence.mentioned_symbols if len(symbol) >= 4 and symbol.lower() not in issue_lower and symbol.lower() not in initial_text]
    assertions = [message for message in evidence.assertion_messages if message.lower() not in issue_lower]
    return {"novel_file_clues": files, "novel_symbol_clues": symbols, "novel_assertion_terms": assertions}


@dataclass(frozen=True)
class Qualification:
    case_id: str
    baseline_commit: str
    expected_bug_file: str
    expected_bug_symbol: str
    dependency_pattern: str
    initial_bug_file_rank: int | None
    initial_bug_file_selected: bool
    initial_files: list[str]
    baseline_test_failed: bool
    failed_tests: list[str]
    evidence_files: list[str]
    evidence_symbols: list[str]
    novel_file_clues: list[str]
    novel_symbol_clues: list[str]
    novel_assertion_terms: list[str]
    expected_failure_clue_present: bool
    expected_failure_clue_novel: bool
    offline_dynamic_bug_file_rank: int | None
    offline_dynamic_files: list[str]
    qualified: bool

    def as_dict(self) -> dict:
        return asdict(self)


def qualify_case(case: ChallengeCase, baseline: Path, commit: str, gold: dict) -> Qualification:
    """Use gold only in an offline gate; no model or Agent is invoked."""
    initial = SymbolRetriever().retrieve(case.issue, baseline, 8000).context
    files = ranked_files(initial)
    bug_file = gold["expected_bug_file"]
    initial_rank = rank(files, bug_file)
    test = RunTests(baseline).execute()
    if "test_result" not in test.metadata:
        raise RuntimeError(f"baseline pytest yielded no structured result: {case.case_id}")
    evidence = extract_failure_evidence(test.metadata["test_result"])
    novelty = evidence_novelty(case.issue, initial, evidence)
    clue = gold["expected_failure_clue"]
    clue_present = clue in evidence.mentioned_files if clue.endswith(".py") else clue in evidence.mentioned_symbols
    clue_novel = clue in novelty["novel_file_clues"] if clue.endswith(".py") else clue in novelty["novel_symbol_clues"]
    refresh = DynamicRetriever().retrieve(case.issue, baseline, initial, evidence) if not test.success else None
    dynamic_files = list(refresh.after_top3) if refresh else []
    dynamic_rank = rank(dynamic_files, bug_file)
    qualified = (
        not test.success and (initial_rank is None or initial_rank > 3)
        and clue_present and clue_novel and dynamic_rank is not None and dynamic_rank <= 3
    )
    return Qualification(
        case.case_id, commit, bug_file, gold["expected_bug_symbol"], gold["dependency_pattern"],
        initial_rank, initial_rank is not None, files, not test.success,
        list(evidence.failed_tests), list(evidence.mentioned_files), list(evidence.mentioned_symbols),
        novelty["novel_file_clues"], novelty["novel_symbol_clues"], novelty["novel_assertion_terms"],
        clue_present, clue_novel, dynamic_rank, dynamic_files, qualified,
    )


def qualify_all(runs_dir: Path, cases: list[str] | None = None) -> list[Qualification]:
    gold = load_gold()
    rows = []
    for case_id in cases or list(CASES):
        case = CASES[case_id]
        baseline, commit = prepare_baseline(case, runs_dir)
        rows.append(qualify_case(case, baseline, commit, gold[case_id]))
    return rows
