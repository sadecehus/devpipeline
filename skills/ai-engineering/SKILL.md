---
name: ai-engineering
description: "Production AI engineering for DevPipeline: when (not) to use LLMs/agents, provider integration (Anthropic, OpenAI, Gemini, Ollama, Claude Agent SDK), structured outputs, tool calling and MCP, prompt/context engineering, reliability (timeouts, retries, fallbacks, loop limits), security (prompt injection, tool authorization), observability (opt-in LangSmith/Langfuse/OpenTelemetry) and credential-free testing."
user-invocable: false
---

# AI engineering

## Activation criteria
AI Engineer tasks; architect designing `ai_architecture`; reviewers/security checking AI code. Load `rag-engineering`, `langgraph-engineering` or `langchain-engineering` additionally when those are selected.

## Required inputs
AI requirements and ACs, architecture `ai_architecture` (providers, models, tools, state, retrieval), data sensitivity notes, latency/cost targets.

## Decision tree: how much AI machinery?
1. Deterministic code can do it (parsing, rules, math)? → no LLM.
2. One transformation (classify, extract, summarise, answer with given context)? → single model call with a structured-output schema.
3. Needs external data at query time? → RAG (see rag-engineering).
4. Needs a few tool calls chosen by the model, bounded steps? → tool-calling loop (provider SDK tool use, LangChain `create_agent`, or Claude Agent SDK) with a max-iterations limit.
5. Needs explicit branching, persistence across turns, human approval, parallel fan-out or recovery? → LangGraph state machine.
6. Multi-agent only when sub-tasks need isolated contexts/tools and the gain is demonstrated; otherwise one agent.

## Provider integration
- Wrap model access in a small client module (`ai/llm.py` / `ai/llm.ts`): model id from config/env, timeout, retries, usage capture. Add a provider abstraction only when ≥2 providers are required.
- Anthropic: Messages API (`anthropic` Python/TS SDK), tool use with JSON Schema `input_schema`, structured outputs via tool/JSON schema; model ids from configuration (current Claude models, e.g. `claude-sonnet-5-5`), never hardcoded in many places.
- OpenAI: Responses/Chat Completions with `response_format` JSON schema / function tools. Gemini: `google-genai` SDK with `response_schema`. Ollama: local HTTP API for offline/private use.
- Claude Agent SDK: for agentic applications that need Claude Code-style tools/sessions; configure allowed tools and permission mode explicitly.
- Read keys from env (`ANTHROPIC_API_KEY`, …); fail closed with a clear error; never log them.

## Structured outputs and validation
Define schemas with Pydantic/Zod/JSON Schema; request schema-constrained output; validate again in code; on validation failure retry once with the error, then return a typed failure. Never `eval`/execute model output; parameterise any downstream query; render as text, not HTML, unless sanitised.

## Tools and MCP
Typed tool definitions with strict parameter schemas; allow-list tools per use case; authorise every tool call against the *end user's* permissions (independent of what the model asks); require human approval for destructive/external side effects; cap tool iterations; treat tool outputs as untrusted data. MCP servers: least privilege, no production credentials by default.

## Prompt and context engineering
System prompt = role, rules, output contract; user/retrieved content clearly delimited (XML tags) and labelled untrusted; include only relevant context (retrieval/summaries), not whole histories; version prompts as files with ids; keep few-shot examples small and representative; ask for "insufficient information" instead of guessing.

## Reliability
Timeouts per call; exponential backoff with jitter for 429/5xx (bounded, e.g. 3 attempts); honour `retry-after`; graceful degradation (cached/partial answer or clear error); fallback model only if acceptable; recursion/iteration limits; idempotent tool side effects; streaming for long user-facing generations; caching only for deterministic, non-personal prompts (prompt caching for long static prefixes is safe).

## Security (must)
Prompt-injection defence in depth: separation of instructions/data, tool allow-lists, output validation, authorization outside the model, no secrets in prompts, minimal PII sent to providers (redact/anonymise), logging with redaction and consent, rate limits per user.

## Observability
Record latency, token usage and errors per call (from provider responses). LangSmith/Langfuse/OpenTelemetry integrations are **opt-in** via env flags; never upload user data without consent.

## Testing without credentials
Provide a fake/recorded model client (deterministic responses keyed by prompt fixture) used by default in tests; live tests behind `RUN_LIVE_AI_TESTS=1` and real credentials. Provide evaluation fixtures (positive, negative, edge, adversarial incl. injected instructions) for the AI Evaluation Engineer under `evals/fixtures/`.

## Anti-patterns
Free-text parsing with regex instead of structured outputs; agents for single calls; unbounded loops; model decides authorization; secrets in prompts; huge context stuffing; untested prompts; silent fallbacks that hide failures.

## Validation
Deterministic tests pass with the fake client; schema validation tests; timeout/retry tests (simulated 429/timeout); prompt-injection unit tests for tool authorization; record that live evaluation was NOT_RUN unless executed.

## Completion criteria
AI behaviour behind typed interfaces, outputs validated, tools authorised, reliability controls in place, credential-free tests passing, `AI_NOTES.md` documents models, limitations, latency/usage expectations and security considerations.

## References
- Anthropic API docs: https://docs.claude.com/en/api/overview · Tool use: https://docs.claude.com/en/docs/agents-and-tools/tool-use/overview
- Claude Agent SDK: https://docs.claude.com/en/docs/agent-sdk/overview
- OpenAI API: https://platform.openai.com/docs · Gemini API: https://ai.google.dev/gemini-api/docs · Ollama: https://github.com/ollama/ollama/blob/main/docs/api.md
- Model Context Protocol: https://modelcontextprotocol.io/
- OWASP Top 10 for LLM Applications: https://genai.owasp.org/llm-top-10/
- OpenTelemetry GenAI semantic conventions: https://opentelemetry.io/docs/specs/semconv/gen-ai/
