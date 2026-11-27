---
name: langgraph-engineering
description: "LangGraph implementation guidance: explicit typed state (TypedDict/MessagesState/Pydantic), reducers for concurrent updates, conditional edges, Send for dynamic fan-out, Command, checkpointers and thread ids, short- vs long-term memory (Store), interrupts for human approval, recursion limits, retries and graph-level testing. Use only when control flow justifies a graph."
user-invocable: false
---

# LangGraph engineering

## Activation criteria
The architecture selected LangGraph because the AI flow needs branching, loops with explicit termination, persistence across turns, human-in-the-loop approval, or parallel fan-out. If a single call or a fixed sequence suffices, do not use LangGraph.

## Required inputs
State design and node list from `ai_architecture`, tool contracts, persistence requirements (conversation memory? multi-user?), approval points.

## Design steps
1. **State** — define it explicitly:
   ```python
   from typing import Annotated, TypedDict
   import operator
   from langgraph.graph import StateGraph, START, END, MessagesState

   class ResearchState(MessagesState):          # messages with add_messages reducer
       question: str
       documents: Annotated[list[dict], operator.add]   # reducer: parallel nodes append
       answer: dict | None
       attempts: int
   ```
   Every key written by more than one node in the same step needs a reducer (`operator.add`, `add_messages`, or a custom function), otherwise LangGraph raises `InvalidUpdateError`.
2. **Nodes** — pure-ish functions `(state) -> partial update`; keep I/O (LLM, retrieval, tools) in nodes, decisions in routing functions.
3. **Edges** — `builder.add_edge(START, "plan")`; conditional routing with `builder.add_conditional_edges("grade", route_fn, {"retry": "retrieve", "answer": "generate", "give_up": END})`; routing functions are deterministic on state.
4. **Fan-out** — return `[Send("retrieve_one", {"query": q}) for q in state["queries"]]` from a conditional edge (`from langgraph.types import Send`); collect via a reducer key. Use `Command(goto=..., update=...)` when a node must both update state and route.
5. **Termination** — every loop has an explicit exit (attempt counter in state, quality threshold) and the run config sets `{"recursion_limit": N}`; handle `GraphRecursionError` as a typed failure.
6. **Persistence** — compile with a checkpointer: `InMemorySaver` (`langgraph.checkpoint.memory`) for tests; `SqliteSaver`/`PostgresSaver` (`langgraph-checkpoint-sqlite` / `-postgres`) in production. Invoke with `config={"configurable": {"thread_id": f"{user_id}:{conversation_id}"}}` — thread ids must be scoped per user to prevent cross-user leakage.
7. **Memory** — short-term = checkpointed thread state; long-term = a `Store` (e.g. `InMemoryStore`, Postgres store) with namespaced keys `(user_id, "memories")`; never mix users' namespaces.
8. **Human-in-the-loop** — call `interrupt({"action": ..., "args": ...})` (`from langgraph.types import interrupt`) inside the node before a sensitive tool executes; resume with `graph.invoke(Command(resume=decision), config)`. Requires a checkpointer.
9. **Retries/failures** — node-level `retry_policy` (`RetryPolicy(max_attempts=3)`) for transient errors; catch provider errors and write an error field to state so routing can degrade gracefully.
10. **Streaming** — `graph.stream(..., stream_mode="updates"|"messages")` for UI progress.

## Testing
- Unit-test each node with a fake model/tool and a hand-built state.
- Unit-test routing functions exhaustively (each branch, termination at max attempts).
- Graph tests with `InMemorySaver`: full run reaches END; recursion limit produces the typed failure; interrupt pauses and `Command(resume=...)` continues; parallel `Send` results merge correctly via the reducer; thread isolation (two thread ids don't share state).
- Tool routing: given model tool-call messages, the correct tool node runs and unauthorised tools are rejected.

## Anti-patterns
Implicit state in globals; keys updated in parallel without reducers; loops relying only on the model to stop; one thread id for all users; checkpointer missing when interrupts are used; giant single node doing everything; using LangGraph for a linear two-step chain.

## Validation
Run graph and node tests (deterministic); render the graph (`graph.get_graph().draw_mermaid()`) into AI_NOTES.md for review; check recursion limit and max attempts are configured.

## Completion criteria
Explicit typed state with reducers, deterministic routing, bounded loops, per-user thread ids, persistence and interrupts where required, all transitions covered by tests.

## References
- LangGraph overview: https://docs.langchain.com/oss/python/langgraph/overview
- Graph API (state, reducers, Send, Command): https://docs.langchain.com/oss/python/langgraph/graph-api
- Persistence/checkpointers: https://docs.langchain.com/oss/python/langgraph/persistence
- Memory: https://docs.langchain.com/oss/python/langgraph/add-memory
- Interrupts / human-in-the-loop: https://docs.langchain.com/oss/python/langgraph/interrupts
- LangGraph JS: https://docs.langchain.com/oss/javascript/langgraph/overview
Confirm import paths for the installed version (Context7) — module locations have moved between releases.
