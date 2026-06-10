"""aspire-kb-api client — RAG retrieval over the Aspire intelligence layer.

The aspire-kb-api FastAPI on Posit Connect fronts the Aiven PG `aspire_kb`
store (Aspire IG posts + Peninsula sport + future sources). Embedding and
cross-encoder models live server-side, so consumers never pip-install
PyTorch — this client is pure HTTP.

USAGE
=====

    from aspire_data.aspire_kb import kb_search, kb_sources, kb_stats, kb_document

    hits = kb_search("Mahmoud fencing medal", k=5)
    hits = kb_search("squad attendance", source="peninsula", rerank=True)
    for h in hits:
        print(h["score"], h["title"], h["url"])

    kb_sources()          # registered sources + active flags
    kb_stats()            # doc/chunk counts + per-source freshness
    kb_document(doc_id)   # full document by id

CONFIG (env, public-safe)

    CONNECT_BASE_URL     (shared with all Connect-backed calls)
    CONNECT_API_KEY
    ASPIRE_KB_API_GUID   <your aspire-kb-api content GUID>
"""
from __future__ import annotations

__all__ = ["kb_search", "kb_sources", "kb_stats", "kb_document"]

from typing import Any

from .connect import _client_for


def kb_search(query: str, *, k: int = 5, strategy: str = "hybrid",
              source: str | None = None, rewrite: bool = False,
              multi_query_n: int = 0, hyde: bool = False,
              rerank: bool = False) -> list[dict]:
    """POST /retrieve — hybrid BM25+vector+RRF retrieval (default), or
    pure `vector` / `bm25`. Returns a list of hit dicts:
    chunk_id, doc_id, source_id, posted_at, url, title, text, score.

    Opt-in quality levers (each adds server-side latency):
    rewrite (LLM query rewrite), multi_query_n (parallel reformulations),
    hyde (hypothetical-document embedding), rerank (cross-encoder).
    """
    cli = _client_for("ASPIRE_KB_API_GUID")
    body: dict[str, Any] = {"query": query, "k": k, "strategy": strategy}
    if source is not None: body["source_filter"] = source
    if rewrite:        body["rewrite"] = True
    if multi_query_n:  body["multi_query_n"] = multi_query_n
    if hyde:           body["hyde"] = True
    if rerank:         body["rerank"] = True
    return cli.post("/retrieve", json=body)


def kb_sources() -> list[dict]:
    """GET /sources — registered sources (source_id, name, type, active)."""
    cli = _client_for("ASPIRE_KB_API_GUID")
    return cli.get("/sources")


def kb_stats() -> dict:
    """GET /stats — documents/chunks totals + `latest_per_source`
    freshness map (prefer it over the back-compat `latest_posted_at`)."""
    cli = _client_for("ASPIRE_KB_API_GUID")
    return cli.get("/stats")


def kb_document(doc_id: str) -> dict:
    """GET /document/{doc_id} — full document with all its chunks."""
    cli = _client_for("ASPIRE_KB_API_GUID")
    return cli.get(f"/document/{doc_id}")
