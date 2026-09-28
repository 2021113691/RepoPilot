"""Write one JSON object per event and a final summary."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from repopilot.agent.state import AgentState


class TrajectoryLogger:
    def __init__(self, runs_dir: Path, task_id: str):
        self.directory = runs_dir.resolve() / task_id
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "trajectory.jsonl"

    def event(self, name: str, **data: Any) -> None:
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"event": name, **data}, ensure_ascii=False, default=str) + "\n")

    def finish(self, state: AgentState) -> dict[str, Any]:
        summary = {
            "task_id": state.task_id,
            "issue": state.issue,
            "status": state.status,
            "steps": state.step_count,
            "llm_calls": state.llm_calls,
            "tool_calls": state.tool_calls,
            "tool_sequence": state.tool_sequence,
            "input_tokens": state.input_tokens,
            "output_tokens": state.output_tokens,
            "files_seen": sorted(state.files_seen),
            "files_modified": sorted(state.files_modified),
            "started_at": state.started_at,
            "finished_at": state.finished_at,
            "final_answer": state.final_answer,
            "error": state.error,
            "latency_sec": state.latency_sec,
        }
        (self.directory / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return summary
