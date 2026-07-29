# Dev

Admin debugging tools for logs, maps, and an interactive terminal under `/dev`.

The terminal holds live PTY subprocesses in the module-level `_sessions` dictionary. File descriptors cannot move through Redis, so terminal requests are not reliable across multiple gunicorn workers without sticky-session affinity. Run one worker when using it if affinity is unavailable.
