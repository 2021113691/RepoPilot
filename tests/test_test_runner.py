from repopilot.tools.tests import RunTests


def make_test_repo(tmp_path, failing=False):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "pyproject.toml").write_text('[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8")
    tests = root / "tests"
    tests.mkdir()
    text = "def test_example():\n    assert 1 == 2\n" if failing else "def test_example():\n    assert 1 == 1\n"
    (tests / "test_example.py").write_text(text, encoding="utf-8")
    return root


def test_full_file_and_node_scopes(tmp_path):
    tool = RunTests(make_test_repo(tmp_path))
    for scope in (None, "tests/test_example.py", "tests/test_example.py::test_example"):
        result = tool.execute(scope=scope)
        assert result.success, result.observation()
        assert result.metadata["test_result"]["passed_count"] == 1


def test_failed_test_parses_evidence(tmp_path):
    result = RunTests(make_test_repo(tmp_path, failing=True)).execute()
    assert not result.success
    test = result.metadata["test_result"]
    assert test["failed_tests"] == ["tests/test_example.py::test_example"]
    assert test["exception_type"] == "AssertionError"
    assert "Test Result: FAILED" in result.observation()
    assert len(result.observation()) < 3000


def test_timeout_is_structured(tmp_path):
    root = make_test_repo(tmp_path)
    (root / "tests" / "test_example.py").write_text("import time\ndef test_slow():\n    time.sleep(5)\n", encoding="utf-8")
    result = RunTests(root).execute(timeout_sec=1)
    assert not result.success
    assert result.error == "tests_timeout"
    assert result.metadata["test_result"]["timeout"] is True


def test_shell_injection_and_invalid_paths_rejected(tmp_path):
    tool = RunTests(make_test_repo(tmp_path))
    for scope in ("tests/test_example.py;echo bad", "tests/test_example.py && whoami", "../outside.py", "tests/missing.py", "-k test"):
        result = tool.execute(scope=scope)
        assert not result.success and "invalid pytest scope" in result.error


def test_credential_environment_is_stripped(tmp_path, monkeypatch):
    root = make_test_repo(tmp_path)
    (root / "tests" / "test_example.py").write_text(
        "import os\ndef test_no_key():\n    assert os.getenv('REPOPILOT_API_KEY') is None\n", encoding="utf-8"
    )
    monkeypatch.setenv("REPOPILOT_API_KEY", "fictional-test-key")
    assert RunTests(root).execute().success
