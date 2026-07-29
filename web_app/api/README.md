# API

Encrypted and plain data-sync endpoints under `/api`, plus health, backup, update, and RSA handshake operations.

- Per-user files live below `~/.nabicat/data/api_data/<user-folder>/`.
- `DataInterface` validates paths remain inside the user's directory.
- Ephemeral handshake keys are Redis-backed so encrypted requests work across gunicorn workers.
