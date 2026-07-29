# Metrics

Personal metric definition, logging, editing, and visualization under `/metrics`.

- `app_data.py` defines stored metric models and `visualiser.py` prepares plots.
- Data writes use `DataInterface.edit_data`; load methods are read-only.
- UI assets live in `static/` and templates in `templates/`.
