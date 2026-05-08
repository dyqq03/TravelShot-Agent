# Agent Runtime Structure

`llm_planner.py` is the active planning runtime.

Active path:

1. `services/plan_service.py`
2. `agent/llm_planner.py`
3. `tools/*`
4. `db/repository.py`

The older graph, option, scoring, route optimizer and repair modules are retained as legacy/reference code, but they are no longer called by the plan generation endpoint.
