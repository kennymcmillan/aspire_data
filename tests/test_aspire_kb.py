"""aspire-kb-api client — kb_search / kb_sources / kb_stats / kb_document."""
from __future__ import annotations

import pytest


def _last_call(mock_httpx):
    return mock_httpx.instances[-1].calls[-1]


def test_kb_search_posts_retrieve_with_defaults(mock_httpx):
    from aspire_data.aspire_kb import kb_search
    mock_httpx.instances.clear()
    kb_search("Mahmoud fencing")
    method, path, kwargs = _last_call(mock_httpx)
    assert method == "POST"
    assert path == "/retrieve"
    assert kwargs["json"] == {"query": "Mahmoud fencing", "k": 5, "strategy": "hybrid"}


def test_kb_search_opt_in_levers_only_sent_when_set(mock_httpx):
    from aspire_data.aspire_kb import kb_search
    mock_httpx.instances.clear()
    kb_search("q", k=10, strategy="vector", source="peninsula",
              rewrite=True, multi_query_n=3, hyde=True, rerank=True)
    _, _, kwargs = _last_call(mock_httpx)
    assert kwargs["json"] == {
        "query": "q", "k": 10, "strategy": "vector",
        "source_filter": "peninsula",
        "rewrite": True, "multi_query_n": 3, "hyde": True, "rerank": True,
    }


def test_kb_sources_stats_document_paths(mock_httpx):
    from aspire_data.aspire_kb import kb_document, kb_sources, kb_stats
    mock_httpx.instances.clear()
    kb_sources()
    kb_stats()
    kb_document("doc-123")
    paths = [c[1] for inst in mock_httpx.instances for c in inst.calls]
    assert "/sources" in paths
    assert "/stats" in paths
    assert "/document/doc-123" in paths


def test_kb_guid_required(monkeypatch, mock_httpx):
    monkeypatch.delenv("ASPIRE_KB_API_GUID", raising=False)
    from aspire_data.aspire_kb import kb_search
    with pytest.raises(RuntimeError, match="ASPIRE_KB_API_GUID"):
        kb_search("anything")
