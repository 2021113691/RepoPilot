import difflib
import subprocess

from repopilot.tools.git import GitDiff
from repopilot.tools.patch import ApplyPatch


def make_patch(path, before, after):
    return f"diff --git a/{path} b/{path}\n" + "".join(
        difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True), fromfile=f"a/{path}", tofile=f"b/{path}")
    )


def committed_repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    (root / "other.py").write_text("def other():\n    return 2\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@localhost", "commit", "-qm", "baseline"], cwd=root, check=True)
    return root


def test_valid_patch_updates_diff_and_numstat(tmp_path):
    root = committed_repo(tmp_path)
    patch = make_patch("app.py", "def value():\n    return 1\n", "def value():\n    return 3\n")
    result = ApplyPatch(root).execute(patch=patch)
    assert result.success, result.error
    assert result.metadata["files_modified"] == ["app.py"]
    assert result.metadata["changed_loc"] == 2
    assert "+    return 3" in GitDiff(root).execute().content
    assert (root / "app.py").read_text(encoding="utf-8").endswith("return 3\n")


def test_invalid_patch_does_not_change_file(tmp_path):
    root = committed_repo(tmp_path)
    before = (root / "app.py").read_bytes()
    patch = make_patch("app.py", "def value():\n    return 999\n", "def value():\n    return 3\n")
    result = ApplyPatch(root).execute(patch=patch)
    assert not result.success and "structured patch failure" in result.error
    assert (root / "app.py").read_bytes() == before


def test_multifile_patch_failure_is_atomic(tmp_path):
    root = committed_repo(tmp_path)
    before = (root / "app.py").read_bytes()
    patch = make_patch("app.py", "def value():\n    return 1\n", "def value():\n    return 3\n")
    patch += make_patch("other.py", "def other():\n    return 999\n", "def other():\n    return 4\n")
    result = ApplyPatch(root).execute(patch=patch)
    assert not result.success
    assert (root / "app.py").read_bytes() == before
    assert GitDiff(root).execute().content == "(no diff)"


def test_patch_rejects_escape_and_credentials(tmp_path):
    root = committed_repo(tmp_path)
    tool = ApplyPatch(root)
    escape = make_patch("../outside.py", "one\n", "two\n")
    assert not tool.execute(patch=escape).success
    (root / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
    credential = make_patch(".env", "TOKEN=secret\n", "TOKEN=other\n")
    result = tool.execute(patch=credential)
    assert not result.success and "sensitive path" in result.error
    assert (root / ".env").read_text(encoding="utf-8") == "TOKEN=secret\n"


def test_patch_rejects_file_creation(tmp_path):
    root = committed_repo(tmp_path)
    patch = "diff --git a/new.py b/new.py\nnew file mode 100644\n--- /dev/null\n+++ b/new.py\n@@ -0,0 +1 @@\n+print('hello')\n"
    assert not ApplyPatch(root).execute(patch=patch).success


def test_patch_rejects_extra_file_header(tmp_path):
    root = committed_repo(tmp_path)
    patch = make_patch("app.py", "def value():\n    return 1\n", "def value():\n    return 3\n")
    patch += "--- a/.env\n+++ b/.env\n@@ -1 +1 @@\n-a\n+b\n"
    result = ApplyPatch(root).execute(patch=patch)
    assert not result.success
    assert (root / "app.py").read_text(encoding="utf-8").endswith("return 1\n")


def test_patch_rejects_symlink_escape(tmp_path):
    root = committed_repo(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("one\n", encoding="utf-8")
    try:
        (root / "link.py").symlink_to(outside)
    except (OSError, NotImplementedError):
        return
    patch = make_patch("link.py", "one\n", "two\n")
    result = ApplyPatch(root).execute(patch=patch)
    assert not result.success and "symlink target is unsupported" in result.error
    assert outside.read_text(encoding="utf-8") == "one\n"
