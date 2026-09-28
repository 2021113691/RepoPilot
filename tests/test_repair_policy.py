import difflib
import subprocess

from repopilot.tools.patch import ApplyPatch
from repopilot.tools.policy import RepairPolicy


def patch_for(path, before="before\n", after="after\n"):
    return f"diff --git a/{path} b/{path}\n" + "".join(
        difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True), fromfile=f"a/{path}", tofile=f"b/{path}")
    )


def repo(tmp_path):
    root = tmp_path / "repo"
    files = (
        "app/foo.py", "docs/foo.md", "tests/test_foo.py", "tests/helper.py", "test/helper.py",
        "pkg/module/tests/test_bar.py", "pkg/test_parser.py", "pkg/parser_test.py",
    )
    for name in files:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@localhost", "commit", "-qm", "baseline"], cwd=root, check=True)
    return root


def test_source_file_allowed(tmp_path):
    root = repo(tmp_path)
    result = ApplyPatch(root).execute(patch=patch_for("app/foo.py"))
    assert result.success, result.error
    assert (root / "app/foo.py").read_text() == "after\n"


def test_test_file_rejected_without_write(tmp_path):
    root = repo(tmp_path)
    result = ApplyPatch(root).execute(patch=patch_for("tests/test_foo.py"))
    assert result.metadata["error_code"] == "protected_path"
    assert result.metadata["paths"] == ["tests/test_foo.py"]
    assert "read-only verification oracles" in result.observation()
    assert (root / "tests/test_foo.py").read_text() == "before\n"
    assert not subprocess.run(["git", "diff", "--quiet"], cwd=root).returncode


def test_mixed_patch_rejected_atomically(tmp_path):
    root = repo(tmp_path)
    result = ApplyPatch(root).execute(patch=patch_for("app/foo.py") + patch_for("tests/test_foo.py"))
    assert result.metadata["error_code"] == "protected_path"
    assert (root / "app/foo.py").read_text() == "before\n"
    assert (root / "tests/test_foo.py").read_text() == "before\n"
    assert not subprocess.run(["git", "diff", "--quiet"], cwd=root).returncode


def test_nested_test_directories_rejected(tmp_path):
    root = repo(tmp_path)
    for name in ("pkg/module/tests/test_bar.py", "tests/helper.py", "test/helper.py"):
        assert ApplyPatch(root).execute(patch=patch_for(name)).metadata["error_code"] == "protected_path"


def test_test_filename_patterns_rejected(tmp_path):
    root = repo(tmp_path)
    for name in ("pkg/test_parser.py", "pkg/parser_test.py"):
        assert ApplyPatch(root).execute(patch=patch_for(name)).metadata["error_code"] == "protected_path"


def test_dot_prefix_normalized_before_policy(tmp_path):
    root = repo(tmp_path)
    result = ApplyPatch(root).execute(patch=patch_for("./tests/test_foo.py"))
    assert result.metadata["error_code"] == "protected_path"
    assert result.metadata["paths"] == ["tests/test_foo.py"]


def test_windows_separator_has_same_policy_result():
    policy = RepairPolicy()
    assert policy.check(r"tests\test_foo.py") == policy.check("tests/test_foo.py")


def test_writable_whitelist(tmp_path):
    root = repo(tmp_path)
    policy = RepairPolicy(writable_globs=["app/**"])
    rejected = ApplyPatch(root, policy=policy).execute(patch=patch_for("docs/foo.md"))
    assert rejected.metadata["error_code"] == "not_writable"
    assert (root / "docs/foo.md").read_text() == "before\n"
    assert ApplyPatch(root, policy=policy).execute(patch=patch_for("app/foo.py")).success


def test_protected_rule_overrides_writable_and_can_be_configured(tmp_path):
    root = repo(tmp_path)
    protected = RepairPolicy(writable_globs=["**"])
    assert ApplyPatch(root, policy=protected).execute(patch=patch_for("tests/test_foo.py")).metadata["error_code"] == "protected_path"
    allowed = RepairPolicy(protected_globs=[], writable_globs=["tests/**"])
    assert ApplyPatch(root, policy=allowed).execute(patch=patch_for("tests/test_foo.py")).success
