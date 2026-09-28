from repopilot.evaluation.trajectory import TrajectoryLogger


def test_trajectory_redacts_environment_secret_and_auth_field(tmp_path, monkeypatch):
    monkeypatch.setenv("REPOPILOT_API_KEY", "fictional-secret-12345")
    logger = TrajectoryLogger(tmp_path, "task")
    logger.event("tool_call", observation="value fictional-secret-12345", headers={"authorization": "Bearer fictional-secret-12345"})
    text = logger.path.read_text(encoding="utf-8")
    assert "fictional-secret-12345" not in text
    assert "[REDACTED]" in text
