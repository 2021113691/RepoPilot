"""B3-only observer that injects novel context after failed pytest results."""

from __future__ import annotations

import time
from dataclasses import asdict

from repopilot.agent.state import AgentState
from repopilot.context.dynamic import DynamicRetriever, format_dynamic_context
from repopilot.context.failure import FailureEvidence, extract_failure_evidence, failure_signature
from repopilot.context.lexical import ContextItem, RetrievedContext
from repopilot.evaluation.trajectory import TrajectoryLogger
from repopilot.tools.base import ToolResult


class DynamicContextHook:
    def __init__(
        self, issue: str, workspace, initial_context: RetrievedContext,
        refresh_budget_tokens: int = 4000, max_refreshes: int = 2,
        retriever: DynamicRetriever | None = None,
    ):
        if refresh_budget_tokens < 1 or max_refreshes < 1:
            raise ValueError("dynamic refresh limits must be positive")
        self.issue = issue
        self.workspace = workspace
        self.initial_context = initial_context
        self.refresh_budget_tokens = refresh_budget_tokens
        self.max_refreshes = max_refreshes
        self.retriever = retriever if retriever is not None else DynamicRetriever()
        self.seen_items: list[ContextItem] = []
        self.pending: list[tuple[FailureEvidence, str, float]] = []

    def on_tool_result(self, state: AgentState, logger: TrajectoryLogger, step: int, name: str, arguments: dict, result: ToolResult) -> None:
        if name != "run_tests" or result.success or "test_result" not in result.metadata:
            return
        evidence = extract_failure_evidence(result.metadata["test_result"])
        signature = failure_signature(evidence)
        state.failure_evidence_history.append({"signature": signature, **evidence.as_dict()})
        logger.event("test_failure_evidence", step=step, signature=signature, evidence=evidence.as_dict())
        if signature not in state.failure_signatures_seen and state.dynamic_refresh_count < self.max_refreshes:
            state.failure_signatures_seen.add(signature)
            self.pending.append((evidence, signature, time.perf_counter()))

    def after_step(self, state: AgentState, logger: TrajectoryLogger, step: int) -> None:
        while self.pending and state.dynamic_refresh_count < self.max_refreshes:
            evidence, signature, failed_at = self.pending.pop(0)
            try:
                refresh = self.retriever.retrieve(
                    self.issue, self.workspace, self.initial_context, evidence,
                    self.refresh_budget_tokens, tuple(self.seen_items),
                )
            except (OSError, ValueError, RuntimeError) as exc:
                logger.event("dynamic_retrieval_error", step=step, signature=signature, error=type(exc).__name__)
                continue
            logger.event(
                "dynamic_retrieval", step=step, signature=signature,
                failed_tests=evidence.failed_tests,
                traceback_files=[frame.file for frame in evidence.traceback_frames],
                before_top3=refresh.before_top3, after_top3=refresh.after_top3,
                selected_files=[item.file for item in refresh.items],
                selected_scores=[item.score for item in refresh.items],
                selected_reasons=[item.evidence for item in refresh.items],
                candidate_count=refresh.candidate_count,
                estimated_tokens=refresh.total_tokens,
                new_files=refresh.new_files, new_symbols=refresh.new_symbols,
                new_snippets=refresh.new_snippets,
            )
            if not refresh.items:
                logger.event("dynamic_refresh_skipped", step=step, signature=signature, reason="no_novel_context")
                continue
            message = format_dynamic_context(refresh, evidence)
            state.messages.append({"role": "user", "content": message})
            state.dynamic_refresh_count += 1
            state.dynamic_context_tokens += refresh.total_tokens
            state.dynamic_context_items.extend(asdict(item) for item in refresh.items)
            state.dynamic_new_files.update(refresh.new_files)
            state.dynamic_new_symbols.update(refresh.new_symbols)
            latency = round(time.perf_counter() - failed_at, 4)
            state.failure_to_new_context_latency_sec.append(latency)
            self.seen_items.extend(refresh.items)
            logger.event(
                "dynamic_context_injected", step=step, signature=signature,
                content=message, estimated_tokens=refresh.total_tokens,
                files=[item.file for item in refresh.items],
                failure_to_new_context_latency_sec=latency,
            )
        self.pending.clear()
