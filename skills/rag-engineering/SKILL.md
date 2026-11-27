---
name: rag-engineering
description: "Retrieval-Augmented Generation engineering: ingestion and normalisation, structure-aware chunking, metadata and filters, embedding model/dimension tracking and re-indexing, vector/hybrid search, justified reranking, grounded answers with source attribution, insufficient-evidence handling, injection-safe treatment of retrieved text and retrieval evaluation."
user-invocable: false
---

# RAG engineering

## Activation criteria
Requirements need answers grounded in user/organisation documents (features.rag) or semantic search.

## Required inputs
Document types and sizes, update frequency, access-control model (who may see which document), citation requirements, latency targets, chosen store (pgvector by default when PostgreSQL is present).

## Pipeline
1. **Ingestion** — validate file type/size (magic bytes, not extension), reject encrypted/oversized files, extract text (PDF: `pypdf`/`pdfplumber`; HTML: readability-style cleaning), normalise whitespace/Unicode, keep page numbers and headings, compute a content hash for dedup. Store originals separately from chunks. Run ingestion outside request handlers for large files.
2. **Chunking** — structure-aware: split by headings/sections/pages first, then by tokens (~300–800 tokens, 10–20 % overlap) only inside sections; never cut tables/code blocks mid-way when avoidable. Keep `document_id`, `page`, `section`, `chunk_index`, `owner_id`/ACL, `source_uri`, `content_hash`.
3. **Embeddings** — one model per index; record `embedding_model`, `dimension`, `normalised` in a metadata table; the query must use the same model. Changing the model ⇒ new column/index + background re-index + switch; never mix vectors from different models.
4. **Index** — pgvector `vector(dim)` + HNSW (`vector_cosine_ops`) or IVFFlat; B-tree/GIN indexes for metadata filters; for hybrid search add PostgreSQL full-text (`tsvector` + GIN) or BM25 and fuse rankings (reciprocal rank fusion).
5. **Retrieval** — always apply authorization filters (`owner_id = :user` or ACL join) **in the query**, then similarity; top-k 5–10; optional MMR for diversity; similarity threshold to detect weak evidence.
6. **Reranking** — only if evaluation shows precision problems (cross-encoder or provider rerank API); measure the gain and latency cost.
7. **Generation** — prompt with delimited, labelled sources (`<source id="doc3#p4">…</source>`), instruct to answer only from sources and cite ids; structured output `{answer, citations[{source_id, quote}], confidence, insufficient_evidence}`; validate that every citation id exists in the retrieved set.
8. **Insufficient evidence** — if retrieval scores are below threshold or the model sets `insufficient_evidence`, return a clear "not found in your documents" response instead of a guess.

## Security
Retrieved text is untrusted: it may contain instructions ("ignore previous instructions…") — keep it inside data delimiters, never let it trigger tools without independent authorization, strip active content, and test adversarial documents. Enforce per-user document access at retrieval time. Do not send unnecessary personal data to the model provider.

## Evaluation (with the AI Evaluation Engineer)
Build a labelled set: question → relevant chunk ids → expected answer facts. Measure retrieval recall@k / precision@k / MRR, answer faithfulness (claims supported by cited chunks), citation validity (100 %), insufficient-evidence behaviour on unanswerable questions, and injection resistance. Thresholds are defined before tuning.

## Anti-patterns
Fixed-size chunking ignoring structure; no metadata; mixing embedding models; filtering by user *after* top-k (leaks and recall loss); reranking by default; answers without citations; treating retrieved text as instructions; re-embedding everything on every start.

## Validation
Tests: ingestion rejects bad files; chunker preserves page/section metadata; embedding dimension check; retrieval respects ACL filters (user B cannot retrieve user A's chunks); citations validated; unanswerable question returns insufficient evidence; deterministic fake embedder for unit tests.

## Completion criteria
Ingestion → index → retrieval → grounded answer pipeline implemented with ACL filtering, recorded embedding metadata, citation validation, insufficient-evidence path, evaluation fixtures and passing deterministic tests.

## References
- pgvector: https://github.com/pgvector/pgvector
- PostgreSQL full-text search: https://www.postgresql.org/docs/current/textsearch.html
- LangChain retrieval: https://docs.langchain.com/oss/python/langchain/retrieval
- Anthropic contextual retrieval: https://www.anthropic.com/news/contextual-retrieval
- Ragas metrics: https://docs.ragas.io/en/stable/concepts/metrics/
- OWASP LLM01 Prompt Injection: https://genai.owasp.org/llmrisk/llm01-prompt-injection/
