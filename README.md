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

## Development

```bash
uv sync --extra dev
uv run pytest -q
uv run ruff check src tests
```

Copy `.env.example` to `.env` for local configuration. Tests use fake credentials and isolated temporary SQLite databases. Plaid Sandbox tests are opt-in and require `.env.test`; production credentials are not required.

Plaid Link and institution lifecycle helpers remain in the source tree for a future administrator-only interface, but they are not registered with FastMCP or exposed by the runtime executable.

This project retains the MIT license and inherited attribution in `LICENSE`.
