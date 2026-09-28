"""B3 failure-driven reranking and novel context selection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .failure import FailureEvidence, build_failure_query
from .lexical import ContextItem, RetrievedContext, estimate_tokens, format_item
from .symbol_retrieval import SymbolRetriever
from .symbols import file_role

DYNAMIC_WEIGHTS = {
    "traceback_file": 10.0,
    "traceback_symbol": 10.0,
    "failed_test_source": 6.0,
    "assertion_identifier": 5.0,
    "exception_identifier": 4.0,
    "failure_keyword": 2.0,
}
HEADER = "# Failure-Driven Repository Context\nA test failure produced new execution evidence. These snippets may help investigate it; verify against current files.\n"


def _stem(file: str) -> str:
    stem = Path(file).stem.lower()
    return stem.removeprefix("test_").removesuffix("_test")


def _symbols(item: ContextItem) -> set[str]:
    return {
        evidence.split(":", 1)[1].strip().split(" [", 1)[0]
        for evidence in item.evidence if evidence.startswith("symbol:")
    }


def _fully_covered(item: ContextItem, seen: list[ContextItem]) -> bool:
    ranges = sorted((old.start_line, old.end_line) for old in seen if old.file == item.file)
    covered_to = item.start_line - 1
    for start, end in ranges:
        if start > covered_to + 1:
            break
        covered_to = max(covered_to, end)
        if covered_to >= item.end_line:
            return True
    return False


@dataclass(frozen=True)
class DynamicRefresh:
    items: tuple[ContextItem, ...]
    total_tokens: int
    candidate_count: int
    before_top3: tuple[str, ...]
    after_top3: tuple[str, ...]
    new_files: tuple[str, ...]
    new_symbols: tuple[str, ...]
    new_snippets: int
    failure_summary: str

    def as_dict(self) -> dict:
        return {
            "items": [item.__dict__ for item in self.items],
            "total_tokens": self.total_tokens, "candidate_count": self.candidate_count,
            "before_top3": list(self.before_top3), "after_top3": list(self.after_top3),
            "new_files": list(self.new_files), "new_symbols": list(self.new_symbols),
            "new_snippets": self.new_snippets, "failure_summary": self.failure_summary,
        }


def format_dynamic_context(refresh: DynamicRefresh, evidence: FailureEvidence) -> str:
    facts = []
    facts.extend(f"- Failed test: {value}" for value in evidence.failed_tests[:3])
    facts.extend(f"- Traceback: {frame.file}:{frame.line or '?'} in {frame.function or '?'}" for frame in evidence.traceback_frames[:4])
    facts.extend(f"- Exception: {value}" for value in evidence.exception_types[:2])
    prefix = HEADER + "Failure evidence:\n" + ("\n".join(facts) or "- No structured failure details") + "\n"
    return prefix + "".join(format_item(item) for item in refresh.items)


class DynamicRetriever:
    def __init__(self, symbol_retriever: SymbolRetriever | None = None):
        self.symbol_retriever = symbol_retriever if symbol_retriever is not None else SymbolRetriever()

    def retrieve(
        self, issue: str, workspace: Path, initial_context: RetrievedContext,
        evidence: FailureEvidence, refresh_budget_tokens: int = 4000,
        seen_items: tuple[ContextItem, ...] = (),
    ) -> DynamicRefresh:
        if refresh_budget_tokens < estimate_tokens(HEADER):
            raise ValueError("dynamic refresh budget is too small")
        query = build_failure_query(issue, evidence)
        # Reuse the frozen B2 candidate engine with a merged issue/failure query.
        b2 = self.symbol_retriever.retrieve(query.retrieval_text(), workspace, 1_000_000)
        traceback_files = {frame.file for frame in evidence.traceback_frames}
        traceback_functions = {frame.function.lower() for frame in evidence.traceback_frames if frame.function}
        failed_stems = {_stem(file) for file in query.test_hints}
        seen = [*initial_context.items, *seen_items]
        seen_files = {item.file for item in seen}
        seen_symbols = set().union(*(_symbols(item) for item in seen)) if seen else set()
        scored: list[ContextItem] = []
        for item in b2.context.items:
            reasons = list(item.evidence)
            score = item.score
            if item.file in traceback_files:
                score += DYNAMIC_WEIGHTS["traceback_file"]
                reasons.append(f"traceback_file_hit: {item.file}")
            matched_functions = [name for name in traceback_functions if any(name in symbol.lower() for symbol in _symbols(item))]
            if matched_functions:
                score += DYNAMIC_WEIGHTS["traceback_symbol"]
                reasons.append("traceback_symbol_hit: " + ", ".join(sorted(matched_functions)))
            if file_role(item.file) == "source" and _stem(item.file) in failed_stems:
                score += DYNAMIC_WEIGHTS["failed_test_source"]
                reasons.append("failed_test_source_relation")
            assertion_hits = [term for term in query.assertion_terms if len(term) >= 4 and term in item.content.lower()]
            if assertion_hits:
                score += DYNAMIC_WEIGHTS["assertion_identifier"]
                reasons.append("assertion_identifier_hit: " + ", ".join(sorted(assertion_hits[:5])))
            exception_hits = [term for term in query.exception_terms if len(term) >= 4 and term in item.content.lower()]
            if exception_hits:
                score += DYNAMIC_WEIGHTS["exception_identifier"]
                reasons.append("exception_identifier_hit: " + ", ".join(sorted(exception_hits[:5])))
            failure_hits = [term for term in evidence.mentioned_symbols if len(term) >= 4 and term in item.content.lower()]
            if failure_hits:
                score += DYNAMIC_WEIGHTS["failure_keyword"]
                reasons.append("failure_keyword_hit: " + ", ".join(sorted(failure_hits[:5])))
            scored.append(ContextItem(item.file, item.start_line, item.end_line, item.content, score, 0, reasons))
        costed = [ContextItem(item.file, item.start_line, item.end_line, item.content, item.score, estimate_tokens(format_item(item)), item.evidence) for item in scored]
        initial_files = tuple(dict.fromkeys(item.file for item in initial_context.items))
        # Header and failure details count toward the refresh budget too.
        # Prefer new files, then new symbols, then fresh ranges in known files.
        # Fixed B2 + failure scores still determine order within each novelty tier.
        def novelty_tier(item: ContextItem) -> int:
            if item.file not in seen_files:
                return 0
            return 1 if _symbols(item) - seen_symbols else 2

        ordered = sorted(costed, key=lambda item: (novelty_tier(item), -item.score / item.token_cost, -item.score, item.file, item.start_line))
        empty = DynamicRefresh((), 0, len(ordered), initial_files[:3], (), (), (), 0, evidence.raw_summary)
        total = estimate_tokens(format_dynamic_context(empty, evidence))
        if total > refresh_budget_tokens:
            raise ValueError("failure evidence exceeds the dynamic refresh budget")
        selected: list[ContextItem] = []
        for item in ordered:
            if _fully_covered(item, [*seen, *selected]):
                continue
            novelty = "new_file" if item.file not in seen_files else "new_symbol" if _symbols(item) - seen_symbols else "new_line_range"
            candidate = ContextItem(item.file, item.start_line, item.end_line, item.content, item.score, 0, [*item.evidence, f"novelty: {novelty}"])
            candidate = ContextItem(candidate.file, candidate.start_line, candidate.end_line, candidate.content, candidate.score, estimate_tokens(format_item(candidate)), candidate.evidence)
            if total + candidate.token_cost > refresh_budget_tokens:
                continue
            selected.append(candidate)
            total += candidate.token_cost
        new_files = tuple(dict.fromkeys(item.file for item in selected if item.file not in seen_files))
        new_symbols = tuple(sorted(set().union(*(_symbols(item) for item in selected)) - seen_symbols)) if selected else ()
        selected_files = tuple(dict.fromkeys(item.file for item in selected))
        refresh = DynamicRefresh(tuple(selected), total, len(ordered), initial_files[:3], selected_files[:3], new_files, new_symbols, len(selected), evidence.raw_summary)
        assert estimate_tokens(format_dynamic_context(refresh, evidence)) <= total <= refresh_budget_tokens
        return refresh
