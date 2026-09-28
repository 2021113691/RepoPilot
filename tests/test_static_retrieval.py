import inspect
import shutil

from repopilot.context.lexical import StaticRetriever, _merge_windows, extract_query, format_context
from repopilot.evaluation.toy_cases import CASES, prepare_case
from repopilot.agent.loop import AgentLoop
from repopilot.models.base import ModelResponse
from repopilot.models.mock import MockBackend


def test_issue_identifiers_and_stopwords():
    query = extract_query("The bug: QuerySet.annotate() should return normalize_email() for domain, not User@Example.COM")
    assert {"queryset.annotate", "queryset", "annotate", "normalize_email"}.issubset(query.identifiers)
    assert "domain" in query.keywords
    assert "the" not in query.terms and "should" not in query.terms
    assert "example.com" not in query.terms


def test_file_hint_extraction():
    assert "app/email_utils.py" in extract_query("Fix app/email_utils.py for uppercase domain").file_hints


def test_snippet_merge():
    assert _merge_windows([20, 27], 100, 20, 80) == [(1, 47)]
    assert _merge_windows([20, 27], 100, 20, 30) == [(1, 30)]


def test_filename_content_and_source_relation(tmp_path):
    _, workspace = prepare_case("email", tmp_path / "runs")
    result = StaticRetriever().retrieve(CASES["email"].issue, workspace)
    files = [item.file for item in result.items]
    assert "app/email_utils.py" in files
    assert "tests/test_email_utils.py" in files
    source = next(item for item in result.items if item.file == "app/email_utils.py")
    assert any(reason.startswith("filename_match") for reason in source.evidence)
    assert any("identifier_match" in reason for reason in source.evidence)
    assert "test_source_relation" in source.evidence
    assert result.candidate_count >= len(result.items)


def test_retrieval_budget_and_determinism(tmp_path):
    _, workspace = prepare_case("discount", tmp_path / "runs")
    retriever = StaticRetriever()
    first = retriever.retrieve(CASES["discount"].issue, workspace, 350)
    second = retriever.retrieve(CASES["discount"].issue, workspace, 350)
    assert first == second
    assert first.total_tokens <= 350
    assert len(first.items) > 0
    assert format_context(first).startswith("# Retrieved Repository Context")


def test_retriever_does_not_accept_gold_inputs():
    parameters = inspect.signature(StaticRetriever.retrieve).parameters
    assert set(parameters) == {"self", "issue", "workspace", "context_budget_tokens"}
    assert not {"gold_patch", "expected_files", "hidden_test"}.intersection(parameters)


def test_retrieval_filters_credentials_and_uses_relative_paths(tmp_path):
    _, workspace = prepare_case("email", tmp_path / "runs")
    (workspace / ".env").write_text("TOKEN=normalize_email\n", encoding="utf-8")
    result = StaticRetriever().retrieve("normalize_email", workspace)
    assert all(not item.file.startswith(("/", "\\")) and ".env" not in item.file for item in result.items)


def test_b0_messages_frozen_and_b1_injects_once(tmp_path):
    _, workspace = prepare_case("email", tmp_path / "runs")
    b0_backend = MockBackend([ModelResponse(content="done")])
    b0 = AgentLoop(b0_backend, workspace, runs_dir=tmp_path / "runs").run("inspect")
    assert len(b0_backend.requests[0][0]) == 2
    assert b0_backend.requests[0][0][-1] == {"role": "user", "content": "inspect"}
    assert (b0.retrieval_mode, b0.initial_context_tokens) == ("none", 0)
    b1_backend = MockBackend([ModelResponse(content="done")])
    context = "# Retrieved Repository Context\ncode"
    b1 = AgentLoop(b1_backend, workspace, runs_dir=tmp_path / "runs").run("inspect", initial_context=context, initial_context_tokens=10)
    assert [message["content"] for message in b1_backend.requests[0][0][-2:]] == [context, "inspect"]
    assert (b1.retrieval_mode, b1.initial_context_tokens) == ("lexical", 10)


def test_lexical_rg_and_python_fallback_agree(tmp_path, monkeypatch):
    _, workspace = prepare_case("email", tmp_path / "runs")
    retriever = StaticRetriever()
    first = retriever.retrieve("normalize_email domain", workspace)
    original_which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name: None if name == "rg" else original_which(name))
    second = retriever.retrieve("normalize_email domain", workspace)
    assert [item.file for item in first.items] == [item.file for item in second.items]


def test_config_and_pair_budget_equality():
    from scripts.run_experiment import load_config
    from repopilot.evaluation.toy_cases import ROOT
    b0 = load_config(ROOT / "experiments/b0_naive.yaml")
    b1 = load_config(ROOT / "experiments/b1_static.yaml")
    assert b0["agent"] == b1["agent"]
    assert b0["retrieval"] == {"enabled": False}
    assert b1["retrieval"]["context_budget_tokens"] == 8000
