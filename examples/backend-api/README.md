# Example: backend-api

Input for a controlled DevPipeline run. `request.md` is the natural-language request; `expected.json` records the agents and gates the framework must activate for it (verified deterministically by `tests/smoke/test_examples.py` without spending model usage).

Run it for real (consumes Claude usage; asks for workflow approval):

```text
/devpipeline:build-project <contents of request.md> --dir ./backend-api-demo
```
