# Todoist

Hierarchical goal tracking, completion history, summaries, and velocity visualization under `/todoist`.

- Goal mutation routes are grouped in `goals.py`.
- The `/actions/todoist/goals` API and OpenAPI document are implemented in `api.py`.
- Data writes use `DataInterface.edit_goals`; load methods are read-only.
