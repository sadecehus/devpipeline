---
name: langchain-engineering
description: "LangChain implementation guidance (v1 APIs): init_chat_model, create_agent with typed tools and response_format, with_structured_output, middleware, retrievers, error/timeout handling, avoiding deprecated chains/agents, and testing with fake chat models. Use only when LangChain is the selected AI framework."
user-invocable: false
---

# LangChain engineering

## Activation criteria
architecture.json selects LangChain (or the existing project uses it). If the task is a single structured call, prefer the provider SDK or `with_structured_output` only — do not build an agent.

## Required inputs
AI architecture (models, tools, schemas), pinned `langchain` / `langchain-<provider>` versions in the project manifest.

## Current API (verify versions before coding)
LangChain v1 consolidated the agent API. Before writing code, check the installed version (`uv pip show langchain` / `npm ls langchain`) and confirm signatures in the docs (Context7 or the links below). Do not copy pre-1.0 tutorials.

- **Models:** `from langchain.chat_models import init_chat_model` → `model = init_chat_model("anthropic:claude-sonnet-5-5", timeout=30, max_retries=2)` (or provider classes such as `ChatAnthropic`, `ChatOpenAI` from `langchain-anthropic`/`langchain-openai`). Model id from config/env.
- **Structured output:** `model.with_structured_output(AnswerSchema)` with a Pydantic model/TypedDict/JSON Schema; validate again in code for safety-critical use.
- **Tools:** `from langchain.tools import tool`; type-annotated functions with docstrings; use Pydantic `args_schema` for strict validation; tools return strings/JSON, raise typed errors handled by the agent.
- **Agents:** `from langchain.agents import create_agent` → `agent = create_agent(model, tools=[...], system_prompt="...", response_format=AnswerSchema)`; invoke with `agent.invoke({"messages": [{"role": "user", "content": q}]})`; limit steps via the run config `recursion_limit` and middleware. `create_agent` builds on LangGraph, so checkpointers/thread ids work the same way (see langgraph-engineering).
- **Middleware (v1):** use for cross-cutting concerns (summarisation, human-in-the-loop approval of tool calls, PII redaction, call limits) instead of hand-rolled wrappers — check the docs for available built-ins in the installed version.
- **Retrieval:** vector stores (`PGVector` from `langchain-postgres`, etc.) + `as_retriever(search_kwargs={"k": 5, "filter": {...}})`; keep ingestion code separate from query code.
- **Deprecated — avoid:** `LLMChain`, `initialize_agent`, `AgentExecutor`-based legacy agents, `ConversationChain`, old `langchain.memory` classes, `RetrievalQA` (use explicit retrieval + structured answer, or `create_agent` with a retrieval tool).

## Error handling
Set `timeout` and `max_retries` on models; catch provider errors (`anthropic.RateLimitError`, `openai.APITimeoutError` …) at the service boundary and map them to typed application errors; never let a stack trace reach the user; cap agent iterations.

## Testing
Use `langchain_core.language_models.fake_chat_models` (e.g. `GenericFakeChatModel` / `FakeListChatModel`) to script model replies, including tool-call messages; unit-test tools directly; test that invalid tool arguments are rejected; snapshot structured outputs. Live tests behind an env flag only.

## Anti-patterns
Mixing legacy and v1 APIs; giant prompt templates with business logic; tools with side effects but no authorization; `allow_dangerous_code`-style options; unbounded agent loops; using LangChain wrappers for things the provider SDK does in two lines.

## Validation
Lint/typecheck; tests with fake models; one test per tool for argument validation and authorization; confirm no deprecated imports (`grep -R "LLMChain\|initialize_agent\|AgentExecutor"` returns nothing in new code).

## Completion criteria
Version-pinned, v1-style code, typed tools and schemas, bounded agents, credential-free tests passing.

## References
- LangChain Python docs: https://docs.langchain.com/oss/python/langchain/overview
- Agents (`create_agent`): https://docs.langchain.com/oss/python/langchain/agents
- Structured output: https://docs.langchain.com/oss/python/langchain/structured-output
- Middleware: https://docs.langchain.com/oss/python/langchain/middleware
- LangChain JS: https://docs.langchain.com/oss/javascript/langchain/overview
- v1 migration guide: https://docs.langchain.com/oss/python/migrate/langchain-v1
