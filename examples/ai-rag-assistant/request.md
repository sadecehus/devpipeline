Build a small document question-answering service: users upload Markdown or PDF files and ask questions; answers must cite the source document and page, and say "not found in your documents" when the evidence is insufficient.
Backend: FastAPI. Retrieval: PostgreSQL with pgvector. AI framework: LangGraph. Return structured JSON {answer, citations[], insufficient_evidence}.
Tests must run without live model credentials (deterministic fake model); live evaluation only when ANTHROPIC_API_KEY is present and approved.
Use the balanced profile.
