# NabiCat

NabiCat is a Flask application containing a collection of small, independently
scoped web apps.

## GPT Actions

NabiCat exposes a read-only Todoist integration for custom GPTs. After a user
signs in and grants access, ChatGPT can list their goals or retrieve a goal by
ID. Tokens are scoped to `todoist.goals.read`; the Action cannot create, edit,
complete, or delete goals.

### Server configuration

Configure one confidential OAuth client:

```dotenv
SITE_URL=https://nabicat.example.com
OAUTH_CLIENT_ID=chatgpt
OAUTH_CLIENT_SECRET=replace-with-a-random-secret
OAUTH_REDIRECT_URIS=https://chatgpt.com/example/oauth/callback
```

Use the exact callback URL displayed by the GPT editor. Multiple permitted
callbacks can be supplied as a comma-separated list. In production, prefer
`OAUTH_CLIENT_SECRET_HASH` over `OAUTH_CLIENT_SECRET`; it accepts a Werkzeug
password hash of the client secret.

The integration provides:

- OAuth authorization: `/oauth/authorize`
- OAuth token exchange and refresh: `/oauth/token`
- OAuth token revocation: `/oauth/revoke`
- OpenAPI schema: `/actions/openapi.json`
- Goal listing: `/actions/todoist/goals`
- Goal retrieval: `/actions/todoist/goals/{goal_id}`

OAuth endpoints require HTTPS outside debug and test environments. OAuth
authorization codes expire after 10 minutes, access tokens after one hour, and
rotating refresh tokens after 30 days. These settings and Action pagination
limits live in `ConfigManager().gpt_actions`.

### Configure the custom GPT

1. Create an Action in the GPT editor and import
   `https://nabicat.example.com/actions/openapi.json`.
2. Select OAuth authentication and enter the configured client ID and secret.
3. Set the authorization URL to
   `https://nabicat.example.com/oauth/authorize`.
4. Set the token URL to `https://nabicat.example.com/oauth/token`.
5. Set the scope to `todoist.goals.read`.
6. Copy the callback URL supplied by ChatGPT into `OAUTH_REDIRECT_URIS`, restart
   NabiCat, and test the Action in GPT Preview.

Users can revoke all ChatGPT access from the home-page Actions menu. Deleting
an account also revokes its OAuth tokens.

### Test with a local server

ChatGPT cannot connect directly to `127.0.0.1`, so expose the debug server
through a temporary HTTPS tunnel:

```bash
python -m web_app --debug --port 12345
cloudflared tunnel --url http://127.0.0.1:12345
```

Set `SITE_URL` to the generated HTTPS origin and configure the GPT with that
origin. Use test credentials and non-sensitive data because tunnel URLs are
publicly reachable.

The OAuth and Action test suite can be run without ChatGPT:

```bash
pytest -q tests/unit/test_actions_oauth.py
```
