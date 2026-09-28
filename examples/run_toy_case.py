"""Run one real API repair smoke on a fresh, committed toy case."""

import argparse
import json
from pathlib import Path

from repopilot.__main__ import load_dotenv
from repopilot.evaluation.toy_cases import CASES, ROOT, run_case
from repopilot.models.openai_compatible import BackendConfig, OpenAICompatibleBackend


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=sorted(CASES), required=True)
    parser.add_argument("--max-steps", type=int, default=20)
    parser.add_argument("--max-tool-calls", type=int, default=40)
    parser.add_argument("--max-patch-attempts", type=int, default=5)
    parser.add_argument("--max-test-runs", type=int, default=5)
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    backend = OpenAICompatibleBackend(BackendConfig.from_env())
    state, workspace = run_case(
        args.case, backend, max_steps=args.max_steps, max_tool_calls=args.max_tool_calls,
        max_patch_attempts=args.max_patch_attempts, max_test_runs=args.max_test_runs,
    )
    print(json.dumps({
        "task_id": state.task_id,
        "workspace": str(workspace),
        "status": state.status,
        "steps": state.step_count,
        "tool_sequence": state.tool_sequence,
        "files_modified": sorted(state.files_modified),
        "patch_count": state.patch_count,
        "test_runs": state.test_runs,
        "test_failures": state.test_failures,
        "changed_loc": state.changed_loc,
        "input_tokens": state.input_tokens,
        "output_tokens": state.output_tokens,
        "latency_sec": state.latency_sec,
        "final_answer": state.final_answer,
    }, ensure_ascii=False, indent=2))
    return 0 if state.status == "tests_passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
