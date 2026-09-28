"""CLI smoke entry point."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from repopilot.agent.loop import AgentLoop
from repopilot.models.openai_compatible import BackendConfig, OpenAICompatibleBackend


def load_dotenv(path: Path) -> None:
    """Load simple KEY=VALUE lines without overwriting real environment variables."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def main() -> int:
    parser = argparse.ArgumentParser(description="RepoPilot Day 1 read-only coding agent")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--issue", required=True)
    parser.add_argument("--max-steps", type=int, default=20)
    parser.add_argument("--max-tool-calls", type=int, default=40)
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    args = parser.parse_args()
    load_dotenv(Path(".env"))
    try:
        config = BackendConfig.from_env(base_url=args.base_url, model=args.model)
        state = AgentLoop(
            OpenAICompatibleBackend(config), args.workspace, args.runs_dir,
            max_steps=args.max_steps, max_tool_calls=args.max_tool_calls,
        ).run(args.issue)
    except (ValueError, OSError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "task_id": state.task_id,
        "status": state.status,
        "steps": state.step_count,
        "tool_sequence": state.tool_sequence,
        "files_seen": sorted(state.files_seen),
        "input_tokens": state.input_tokens,
        "output_tokens": state.output_tokens,
        "final_answer": state.final_answer,
        "error": state.error,
        "latency_sec": state.latency_sec,
    }, ensure_ascii=False, indent=2))
    return 0 if state.status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
