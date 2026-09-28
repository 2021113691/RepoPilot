import json

from repopilot.context.lexical import ContextItem
from repopilot.evaluation.redundancy import analyze_redundant_reads


def _trajectory(tmp_path, calls):
    path = tmp_path / "trajectory.jsonl"
    path.write_text("\n".join(json.dumps({"event": "tool_call", "tool": "read_file", "success": True, "metadata": {"file": file, "start_line": start, "end_line": end}}) for file, start, end in calls) + "\n", encoding="utf-8")
    return path


def _context():
    return [ContextItem("app/foo.py", 20, 60, "code", 1.0, 10, [])]


def test_same_file_reread(tmp_path):
    result = analyze_redundant_reads(_trajectory(tmp_path, [("app/foo.py", 1, 10)]), _context())
    assert result["read_file_calls"] == 1
    assert result["same_file_reread_calls"] == 1
    assert result["same_file_reread_ratio"] == 1.0


def test_overlapping_reread(tmp_path):
    result = analyze_redundant_reads(_trajectory(tmp_path, [(r"app\foo.py", 1, 200)]), _context())
    assert result["same_file_reread_calls"] == 1
    assert result["overlapping_reread_calls"] == 1
    assert result["overlapping_reread_ratio"] == 1.0


def test_nonoverlap_and_other_file(tmp_path):
    result = analyze_redundant_reads(_trajectory(tmp_path, [("app/foo.py", 61, 80), ("app/bar.py", 20, 60)]), _context())
    assert result["read_file_calls"] == 2
    assert result["same_file_reread_calls"] == 1
    assert result["overlapping_reread_calls"] == 0
    assert result["same_file_reread_ratio"] == 0.5
