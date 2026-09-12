# Studio Saelix Finance MCP

Studio Saelix Finance MCP is a read-only [Model Context Protocol](https://modelcontextprotocol.io/) server for financial data made available through Plaid. It lets an AI client inspect linked accounts, balances, transactions, investments, and liabilities without giving the client authority to link institutions, manage credentials, move money, trade, or change financial records.

The released package is `studio-saelix-finance` **0.1.0**. Its MCP runtime communicates over stdio and makes outbound read requests to Plaid; it does not run a listening HTTP server.

## Architecture and capabilities

The administrator and runtime are separate executables with different responsibilities:

```text
Human administrator
        │
        ▼
studio-saelix-finance
        │  Plaid Link, credentials, environment and institution lifecycle
        ▼
      Plaid

AI agent / MCP client
        │  stdio
        ▼
studio-saelix-finance-mcp
        │  read-only financial data requests
        ▼
      Plaid
```

This is a capability boundary in the software, not a restriction expressed only through model instructions. The MCP runtime does not register enrollment, unlinking, credential-management, payment, transfer, trading, or other financial-mutation tools. Reads may update the local transaction cache, sync cursors, and sanitized retrieval-error state.

The runtime exposes exactly these ten tools:

| Tool | Purpose |
| --- | --- |
| `list_accounts` | Retrieve accounts across linked institutions. |
| `get_balances` | Retrieve current balances, optionally for one account. |
| `sync_transactions` | Synchronize transactions from Plaid into the local cache. |
| `refresh_transactions` | Ask Plaid to refresh source transactions for later retrieval. |
| `get_transactions` | Query synchronized transactions by date range and optional filters. |
| `search_transactions` | Search synchronized transactions by name or merchant. |
| `spending_summary` | Aggregate synchronized spending by category, merchant, or account. |
| `get_holdings` | Retrieve current investment holdings. |
| `get_investment_transactions` | Retrieve investment activity for a date range. |
| `get_liabilities` | Retrieve Plaid-reported credit-card, student-loan, and mortgage liability data. |

An MCP client can use these tools to inspect account inventory and balances, find transactions, summarize cached spending, or review available holdings, investment activity, and liabilities. Transaction queries and spending summaries use synchronized local data; call `sync_transactions` before querying new or updated transactions. Investment and liability results depend on institution and product availability.

## Institution linking and data availability

Plaid Link lets the administrator select an eligible institution. Availability depends on the configured country codes, Plaid's institution directory, support for the requested products, and the user's Plaid account and environment access. The software is institution-agnostic; it does not target a particular bank. Multiple institutions can be linked by completing the administrator `link` flow for each one.

Transactions are the default required Plaid product. An institution must support required products for Link to complete. Investments and liabilities are optional products requested when supported, so an institution without either optional product can still be linked. Plaid and the institution determine which records and fields are available and how current they are.

## Quick start

Install [`uv`](https://docs.astral.sh/uv/) first. The administrator CLI is published on PyPI and can be run without cloning this repository:

```bash
uvx --from 'studio-saelix-finance==0.1.0' studio-saelix-finance init
```

On a new installation, `init` selects Plaid Sandbox and asks for the Plaid client ID and Sandbox secret using hidden prompts. Enter credentials only at those prompts. Do not place them in command arguments, environment variables, `.env` files, MCP client configuration, or logs.

After initialization, link an institution in Sandbox and check readiness:

```bash
uvx --from 'studio-saelix-finance==0.1.0' studio-saelix-finance link
uvx --from 'studio-saelix-finance==0.1.0' studio-saelix-finance status
```

`link` opens Plaid Hosted Link in a browser by default. Complete the institution's sign-in and authorization there. Use `link --no-open` to print the Hosted Link URL without opening a browser automatically. Run `link` again to connect another institution.

`status` reports the selected environment, credential-store readiness, institution names, account counts, and retrieval health. It does not display credentials.

## Sandbox and Production

New installations start in Plaid Sandbox, which is the appropriate environment for commissioning with test data. Production is a separate administrator transition; it is not enabled automatically, and Plaid Production access is not guaranteed for every account.

To select Production, the administrator must have Plaid Production access and provide a Production secret at the hidden prompt. Before transition, all linked Sandbox Items must be removed. The CLI enforces this condition, asks for an explicit confirmation phrase, then records Production as the active environment:

```bash
uvx --from 'studio-saelix-finance==0.1.0' studio-saelix-finance use-production
```

Sandbox Items do not carry over to Production; link institutions again after the transition. Production accesses real financial data.

## Administrator lifecycle

The `studio-saelix-finance` executable is for human setup and institution lifecycle operations. Its commands are:

| Command | Behavior |
| --- | --- |
| `init` | Create private local state and collect any missing credentials interactively. |
| `link` | Start Plaid Hosted Link for one institution. Supports `--no-open`. |
| `status` | Show redacted environment, readiness, and institution metadata. |
| `unlink ITEM_ID` | Ask for confirmation, request upstream removal first, then remove that Item's local access token and cached data on success. |
| `use-production` | Explicitly transition from Sandbox to Production after the required checks and confirmation. |

If Plaid rejects an unlink request, local state is preserved and the command reports failure so the administrator can retry. `unlink` requires the Plaid Item ID, available as `item_id` in `list_accounts` results. The exceptional `--force-local-purge` recovery option is intentionally omitted from the normal setup instructions because it deletes that Item's local access token and cached financial data even when Plaid still considers the Item linked.

## Connect an MCP client

Configure an MCP client to launch the pinned stdio runtime on demand. For Hermes, use:

```json
{
  "command": "uvx",
  "args": [
    "--from", "studio-saelix-finance==0.1.0",
    "studio-saelix-finance-mcp"
  ]
}
```

This configuration contains no financial credentials. The runtime reads credentials from the local encrypted store initialized by the administrator CLI. Pinning the package version makes the runtime version explicit and lets an operator choose when to upgrade or roll back by changing the version in the client configuration.

The runtime starts with the MCP client and communicates over stdio. It needs no daemon, exposed port, HTTP server, reverse proxy, or Docker container. It makes outbound Plaid API requests when tools need current source data.

## Configuration and local data

`init` creates a non-secret `config.toml`. Its defaults are Plaid Sandbox, Canada (`CA`), `transactions` as the required product, and `investments,liabilities` as optional products. Country codes control which institutions Plaid can present. Country and product settings are stored there; credentials do not belong in the file. Use `use-production` for environment transitions.

Paths follow XDG base-directory settings when provided, and otherwise use these defaults:

| Data | Default path |
| --- | --- |
| Non-secret configuration and master key | `~/.config/studio-saelix-finance/config.toml` and `~/.config/studio-saelix-finance/master.key` |
| SQLite database, including encrypted credentials and local financial data | `~/.local/share/studio-saelix-finance/finance.db` |
| Operational log | `~/.local/state/studio-saelix-finance/finance.log` |

The application enforces owner-only POSIX permissions for its managed directories and sensitive files. Linux and macOS are the currently supported platforms.

## Security model and limitations

Plaid client credentials and access tokens are encrypted in SQLite with AES-256-GCM. The master key is stored separately from the database in the configuration directory. Keeping the key separate means a copy of the database alone does not contain the key needed to decrypt those credentials.

The financial cache itself is **not encrypted**. Cached account and transaction data, institution metadata, and other locally stored financial records are plaintext in the owner-protected SQLite database. The database, key, configuration, and log rely on local filesystem ownership and permissions for protection. Logs are redacted for sensitive token and secret patterns, but filesystem access still matters.

These controls do not protect against malware or another process running as the same user, a privileged local attacker, an exposed or improperly handled backup, or compromise of the operating system or Plaid account. Protect the machine and any copies of its local data accordingly. This project does not claim whole-disk or full-database encryption.

## Development

The package requires Python 3.10 or newer. From a checkout:

```bash
uv sync --extra dev
uv run pytest -q -m "not sandbox"
uv run ruff check .
```

The default unit and MCP smoke tests use isolated test state and do not require Production credentials. Plaid Sandbox integration tests are separate from that default test command.

## License

MIT licensed. See [LICENSE](LICENSE) for the license text and inherited attribution.
