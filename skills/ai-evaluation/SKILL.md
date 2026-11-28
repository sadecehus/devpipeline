---
name: ai-evaluation
description: "AI evaluation methodology for DevPipeline: thresholds defined up front, calibrated datasets (positive/negative/edge/adversarial), deterministic assertions vs LLM-as-judge, structured-output/tool/routing/termination/retrieval/faithfulness/injection/latency metrics, framework choice (custom first, then DeepEval/Ragas/Promptfoo/LangSmith), live-evaluation policy and honest reporting."
user-invocable: false
---

# AI evaluation

## Activation criteria
AI Evaluation Engineer tasks (always active for AI/RAG projects); AI Engineer preparing fixtures.

## Required inputs
AI requirements/ACs, AI architecture, fixtures from the AI engineer, live-evaluation policy (`optional` default, `mandatory` in strict profile), credentials availability (never ask for them in prompts).

## Step 1 — thresholds before evaluating
Write them into the report `thresholds`. Defaults (override from requirements):
| Metric | Default threshold | Type |
|---|---|---|
| Structured output validity | 100 % | deterministic |
| Critical authorization tests | 100 % | deterministic |
| Critical prompt-injection tests | 100 % | deterministic |
| Graph termination within limits | 100 % | deterministic |
| Tool selection accuracy | ≥ 95 % (when ≥ 20 cases) | deterministic |
| Tool argument validity | 100 % | deterministic |
| Citation validity (RAG) | 100 % | deterministic |
| Retrieval recall@k | project-specific | deterministic on labelled set |
| Faithfulness / answer relevance | project-specific | LLM-judge (live) |
| Latency p50/p95 | project-specific | measured |

## Step 2 — datasets
`evals/datasets/*.jsonl` with fields `id, category (positive|negative|edge|adversarial), input, expected, notes`. Include: happy paths; wrong/ambiguous inputs; empty/huge inputs; unanswerable questions; prompt injection in user input, in retrieved documents and in tool outputs; attempts to call unauthorised tools or access another user's data; provider failure simulations (timeout, 429, malformed output). Use synthetic data only — no production credentials or personal data. State sample sizes; don't claim more than they support (e.g. 20 cases cannot prove 99 % accuracy).

## Step 3 — choose the smallest framework
1. Custom deterministic tests in the project's test runner (pytest/vitest) with a fake/recorded model — always.
2. Promptfoo for prompt regression matrices; DeepEval/Ragas for RAG metrics; LangSmith evaluations if the project already uses LangSmith. Install per project only when justified; never all of them.

## Step 4 — run
- Deterministic suite with fake models: structured-output parsing, tool routing given scripted tool calls, graph termination, ACL filters, citation validation, injection handling (model is scripted to "obey" the injection — the system must still refuse the action), timeout/retry behaviour.
- Live suite (`live:` prefixed checks) only when credentials exist **and** the run allows live calls (env `RUN_LIVE_AI_TESTS=1`); otherwise status NOT_RUN with reason. LLM-as-judge: fixed judge prompt, rubric, temperature 0, report raw scores and the judge model; a judge score is evidence of perceived quality, not factual correctness.
- Latency: measured with the fake model = orchestration overhead only; with live model = lab latency (not production).

## Step 5 — report
Report kind `ai_eval` (test-report schema): one check per metric with value vs threshold, sample size, command, exit code, log path; issues for each failing metric routed to `ai-engineer` (prompts, retrieval, routing) or `backend-engineer` (authorization, transport). A deterministic evaluation you ran (even against a fake/recorded model) is `evidence_type: "executed"` with command, exit code and log; use `"mock"` only for a result that was not produced by running anything, and `llm_judgment` for judge scores. The gate counts executed deterministic checks; live checks follow `live_ai_evaluation`.

## Anti-patterns
Tuning on the test set; changing thresholds after seeing results; reporting mock results as model quality; single-example "evaluations"; judge prompts that leak the expected answer; ignoring adversarial categories.

## Validation
Every executed check has command, exit code and log; thresholds compared numerically; live checks either executed with evidence or NOT_RUN/BLOCKED with reason.

## Completion criteria
Thresholds defined; deterministic suite executed and passing or failures routed; live evaluation executed or honestly NOT_RUN/BLOCKED; report validates.

## References
- Anthropic: define success criteria & develop tests: https://docs.claude.com/en/docs/test-and-evaluate/develop-tests
- Promptfoo: https://www.promptfoo.dev/docs/intro/ · DeepEval: https://deepeval.com/docs/getting-started · Ragas: https://docs.ragas.io/
- LangSmith evaluation: https://docs.langchain.com/langsmith/evaluation
- OWASP LLM Top 10: https://genai.owasp.org/llm-top-10/
