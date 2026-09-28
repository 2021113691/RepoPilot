import inspect
import subprocess

from repopilot.context import SymbolIndex, SymbolRetriever, file_role
from repopilot.evaluation.toy_cases import CASES, prepare_case


def _repo(tmp_path, files):
    root = tmp_path / "repo"
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@localhost", "commit", "-qm", "baseline"], cwd=root, check=True)
    return root


def test_function_extraction(tmp_path):
    root = _repo(tmp_path, {"app/code.py": "def foo():\n    return 1\n"})
    record = SymbolIndex.build(root).by_name["foo"][0]
    assert (record.kind, record.qualified_name, record.file, record.start_line, record.end_line) == ("function", "foo", "app/code.py", 1, 2)


def test_class_extraction(tmp_path):
    root = _repo(tmp_path, {"app/code.py": "class Foo:\n    pass\n"})
    record = SymbolIndex.build(root).by_name["Foo"][0]
    assert (record.kind, record.qualified_name) == ("class", "Foo")


def test_method_qualified_name(tmp_path):
    root = _repo(tmp_path, {"app/code.py": "class Foo:\n    def bar(self):\n        return 1\n"})
    record = SymbolIndex.build(root).by_name["bar"][0]
    assert (record.kind, record.qualified_name, record.parent) == ("method", "Foo.bar", "Foo")


def test_async_function_extraction(tmp_path):
    root = _repo(tmp_path, {"app/code.py": "async def foo():\n    return 1\n"})
    assert SymbolIndex.build(root).by_name["foo"][0].kind == "async_function"


def test_symbol_index_is_deterministic(tmp_path):
    root = _repo(tmp_path, {"z.py": "def zed():\n    pass\n", "a.py": "def alpha():\n    pass\n"})
    assert SymbolIndex.build(root) == SymbolIndex.build(root)
    assert list(SymbolIndex.build(root).by_file) == ["a.py", "z.py"]


def test_syntax_error_is_logged_and_other_files_survive(tmp_path):
    root = _repo(tmp_path, {"broken.py": "def bad(:\n", "good.py": "def good():\n    pass\n"})
    index = SymbolIndex.build(root)
    assert index.parse_errors == {"broken.py": "SyntaxError"}
    assert index.by_name["good"][0].file == "good.py"


def test_exact_definition_beats_test_reference(tmp_path):
    _, root = prepare_case("regression", tmp_path / "runs")
    result = SymbolRetriever().retrieve(CASES["regression"].issue, root)
    assert result.context.items[0].file == "app/slug.py"
    assert any(reason == "exact_symbol_definition: slugify" for reason in result.context.items[0].evidence)
    assert result.exact_definition_matches >= 1


def test_tests_remain_retrievable(tmp_path):
    _, root = prepare_case("regression", tmp_path / "runs")
    result = SymbolRetriever().retrieve(CASES["regression"].issue, root)
    assert any(item.file.startswith("tests/") for item in result.context.items)


def test_symbol_span_and_budget_deterministic(tmp_path):
    _, root = prepare_case("email", tmp_path / "runs")
    retriever = SymbolRetriever()
    first = retriever.retrieve(CASES["email"].issue, root, 400)
    second = retriever.retrieve(CASES["email"].issue, root, 400)
    assert first == second
    assert first.context.total_tokens <= 400
    definition = SymbolIndex.build(root).by_name["normalize_email"][0]
    item = next(item for item in first.context.items if item.file == definition.file)
    assert (item.start_line, item.end_line) == (definition.start_line, definition.end_line)


def test_large_symbol_is_bounded(tmp_path):
    body = "def giant():\n" + "    x = 1\n" * 120
    root = _repo(tmp_path, {"app/code.py": body})
    item = SymbolRetriever().retrieve("giant() is broken", root).context.items[0]
    assert item.file == "app/code.py" and item.end_line - item.start_line + 1 <= 80


def test_file_roles_and_no_gold_inputs():
    assert file_role(r"pkg\tests\test_foo.py") == "test"
    assert file_role("app/foo_test.py") == "test"
    assert file_role("app/foo.py") == "source"
    assert file_role("README.md") == "unknown"
    assert set(inspect.signature(SymbolRetriever.retrieve).parameters) == {"self", "issue", "workspace", "context_budget_tokens"}
