import subprocess

from repopilot.tools.files import ListFiles, ReadFile
from repopilot.tools.git import GitDiff
from repopilot.tools.search import SearchCode


def test_boundary_rejects_parent_and_symlink(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    assert ReadFile(root).execute(path="../secret.txt").error == "workspace boundary violation"
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    try:
        (root / "link.txt").symlink_to(outside)
    except (OSError, NotImplementedError):
        return
    assert ReadFile(root).execute(path="link.txt").error == "workspace boundary violation"


def test_list_files_is_bounded_and_ignores_artifacts(tmp_path):
    (tmp_path / "app.py").write_text("hello", encoding="utf-8")
    (tmp_path / ".env").write_text("TOKEN=secret", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / "__pycache__").mkdir()
    result = ListFiles(tmp_path).execute(max_entries=10)
    assert result.success
    assert "app.py" in result.content
    assert ".env" not in result.content
    assert ".git" not in result.content
    assert "__pycache__" not in result.content


def test_read_file_slice_and_limit(tmp_path):
    (tmp_path / "app.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    tool = ReadFile(tmp_path)
    result = tool.execute(path="app.py", start_line=2, end_line=3)
    assert result.success and result.content == "2 | two\n3 | three"
    assert not tool.execute(path="app.py", start_line=1, end_line=300).success
    assert not tool.execute(path=".env").success


def test_search_code_finds_line_and_skips_env(tmp_path):
    (tmp_path / "app.py").write_text("alpha\nneedle here\n", encoding="utf-8")
    (tmp_path / ".env").write_text("needle secret\n", encoding="utf-8")
    result = SearchCode(tmp_path).execute(query="needle")
    assert result.success and "app.py:2: needle here" in result.content
    assert ".env" not in result.content


def test_search_code_python_fallback(tmp_path, monkeypatch):
    (tmp_path / "app.py").write_text("needle\n", encoding="utf-8")
    monkeypatch.setattr("repopilot.tools.search.shutil.which", lambda _: None)
    result = SearchCode(tmp_path).execute(query="needle")
    assert result.success and result.metadata["engine"] == "python"


def test_git_diff_workspace_without_git_root(tmp_path):
    result = GitDiff(tmp_path).execute()
    assert not result.success
    assert result.error in {"workspace is not a Git repository", "workspace is not the Git repository root"}


def test_git_diff_repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "app.py").write_text("old\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "app.py"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "initial"], check=True)
    (tmp_path / "app.py").write_text("new\n", encoding="utf-8")
    result = GitDiff(tmp_path).execute()
    assert result.success and "+new" in result.content
    assert GitDiff(tmp_path).execute(stat=True).success
