# Studio Saelix Finance MCP

Studio Saelix Finance MCP is a stdio-only, read-only MCP server for Plaid-connected financial data.

The runtime exposes exactly these tools:

- `list_accounts`
- `get_balances`
- `sync_transactions`
- `refresh_transactions`
- `get_transactions`
- `search_transactions`
- `spending_summary`
- `get_holdings`
- `get_investment_transactions`
- `get_liabilities`

Runtime writes are limited to retrieval cache maintenance: account and transaction cache rows, transaction cursors, and sanitized retrieval errors. The runtime cannot enroll or unlink institutions, exchange tokens, manage credentials, mutate user-maintained debt data, execute payments, or use remote HTTP transport.

## Administrator setup

The normal installation does not require a repository checkout or a manually
managed virtual environment. `uvx` creates an isolated environment for each
tool invocation:

```bash
uvx studio-saelix-finance init
uvx studio-saelix-finance link
uvx studio-saelix-finance status
```

`init` defaults to Plaid Sandbox and interactively captures the Plaid client ID
and secret. They are encrypted into the local SQLite store; the encryption key
is kept separately at `~/.config/studio-saelix-finance/master.key`.

The financial cache is intentionally plaintext inside the owner-protected
database. Phase 2 protects the Plaid secret and access tokens against theft of
the database alone; it does not provide full database encryption.

Configure Hermes with one secret-free stdio server:

```json
{
  "command": "uvx",
  "args": [
    "--from", "studio-saelix-finance==0.1.0",
    "studio-saelix-finance-mcp"
  ]
}
```

Pin the version used by Hermes deliberately. Upgrade or rollback by changing
the exact version in that configuration. The runtime is launched on demand;
no daemon, port, Docker container, or reverse proxy is required.

Persistent paths follow XDG conventions:

```text
~/.config/studio-saelix-finance/config.toml   # non-secret configuration
~/.config/studio-saelix-finance/master.key    # owner-only key
~/.local/share/studio-saelix-finance/finance.db
~/.local/state/studio-saelix-finance/finance.log
```

For a Canadian deployment, set `PLAID_COUNTRY_CODES=CA` through the
administrator-managed non-secret configuration. Production is a deliberate
administrator action and must not be used before Sandbox commissioning and
the separate security/completion approval.

## Development

```bash
uv sync --extra dev
uv run pytest -q
uv run ruff check src tests
```

Tests use fake credentials and isolated temporary encrypted SQLite databases.
Plaid Sandbox tests are opt-in and require `.env.test`; production credentials
are not required.

Plaid Link and institution lifecycle helpers remain in the source tree for a future administrator-only interface, but they are not registered with FastMCP or exposed by the runtime executable.

This project retains the MIT license and inherited attribution in `LICENSE`.
