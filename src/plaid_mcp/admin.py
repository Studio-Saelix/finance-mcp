"""Explicit human administrator CLI; never imported by the MCP runtime."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

import click

from .config import Config
from .crypto import CredentialError
from .link import complete_link, create_hosted_link
from .logging_setup import configure_logging
from .paths import config_dir, config_path, data_dir, lock_dir, log_path, state_dir
from .storage import Storage
from .tools_transactions import remove_institution


@contextmanager
def _admin_lock():
    """Serialize lifecycle operations across separately spawned CLI processes."""
    import fcntl

    _mkdir_private(lock_dir())
    path = lock_dir() / "admin.lock"
    with path.open("a+") as fh:
        os.chmod(path, 0o600)
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _mkdir_private(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)


def _write_config(cfg: Config) -> None:
    content = (
        f'plaid_env = "{cfg.env}"\n'
        f'country_codes = "{",".join(cfg.country_codes)}"\n'
        f'products = "{",".join(cfg.products)}"\n'
        f'optional_products = "{",".join(cfg.optional_products)}"\n'
        f'client_name = "{cfg.client_name}"\n'
    )
    config_path().write_text(content)
    os.chmod(config_path(), 0o600)


def _open_storage(cfg: Config, *, create_key: bool = False) -> Storage:
    return Storage(cfg.db_path, cfg.master_key_path, create_key=create_key)


@click.group()
def main() -> None:
    """Studio Saelix Finance administrator commands."""
    configure_logging(log_path())


@main.command()
def init() -> None:
    """Initialize private state and capture Plaid credentials interactively."""
    with _admin_lock():
        for path in (config_dir(), data_dir(), state_dir(), lock_dir()):
            _mkdir_private(path)
        cfg = Config.from_env(require_credentials=False)
        cfg.env = "sandbox"
        try:
            storage = _open_storage(cfg, create_key=True)
        except CredentialError as exc:
            raise click.ClickException(str(exc)) from exc
        try:
            if not storage.get_secret("plaid_client_id"):
                client_id = click.prompt("Plaid client ID", hide_input=True)
                secret = click.prompt("Plaid secret", hide_input=True)
                storage.save_secret("plaid_client_id", client_id)
                storage.save_secret("plaid_secret", secret)
            _write_config(cfg)
        finally:
            storage.close()
    click.echo("Initialized Sandbox state. Next: studio-saelix-finance link")


@main.command()
@click.option("--no-open", is_flag=True, help="Do not open the browser")
def link(no_open: bool) -> None:
    """Link one institution through Plaid Hosted Link."""
    cfg = Config.from_env()
    with _admin_lock():
        storage = _open_storage(cfg)
        try:
            session = create_hosted_link(storage, cfg, user_id="studio-saelix-finance-user")
            url = session.get("hosted_url")
            if not url:
                raise click.ClickException("Plaid did not return a Hosted Link URL")
            click.echo(f"Open this URL in your browser:\n\n  {url}")
            if not no_open:
                import webbrowser
                webbrowser.open(url)
            result = complete_link(storage, session["link_token"], timeout_s=600)
            if result.get("status") != "completed":
                raise click.ClickException(result.get("message", "Link did not complete"))
            click.echo(f"Linked {result.get('institution_name') or 'institution'} "
                       f"({result['accounts']} accounts).")
        finally:
            storage.close()


@main.command()
def status() -> None:
    """Show redacted readiness and institution metadata."""
    try:
        cfg = Config.from_env()
        storage = _open_storage(cfg)
    except (CredentialError, RuntimeError) as exc:
        click.echo(f"Not ready: {exc}")
        return
    try:
        click.echo(f"Environment: {cfg.env}")
        items = storage.list_items()
        click.echo(f"Credential store: ready\nInstitutions: {len(items)}")
        for item in items:
            click.echo(f"- {item.get('institution_name') or '(unknown)'} "
                       f"({storage.count_accounts(item['item_id'])} accounts) "
                       f"health={item.get('last_error') or 'ok'}")
    finally:
        storage.close()


@main.command()
@click.argument("item_id")
@click.option("--force-local-purge", is_flag=True, help="Purge local state after upstream failure")
def unlink(item_id: str, force_local_purge: bool) -> None:
    """Remove an institution; upstream removal is attempted first."""
    if force_local_purge:
        click.echo(
            "DANGER: local credentials and cached financial data will be deleted "
            "even if Plaid still considers this Item linked."
        )
        if click.prompt(f"Type PURGE {item_id} to continue") != f"PURGE {item_id}":
            raise click.Abort()
    elif not click.confirm("Unlink this institution upstream and remove local data?"):
        raise click.Abort()
    cfg = Config.from_env()
    with _admin_lock():
        storage = _open_storage(cfg)
        try:
            result = remove_institution(storage, item_id, force_local_purge=force_local_purge)
            click.echo(result.get("error") or result.get("status"))
            if result.get("status") == "upstream_failed":
                raise click.ClickException(
                    "Upstream removal failed; local state was preserved"
                )
        finally:
            storage.close()


@main.command("use-production")
def use_production() -> None:
    """Select Production only after commissioning, audits, and human approval."""
    cfg = Config.from_env(require_credentials=False)
    if cfg.env == "production":
        click.echo("Production is already selected.")
        return
    click.echo(
        "Production accesses real financial data. Continue only after Sandbox "
        "commissioning, completion/security audit, and explicit human approval."
    )
    if click.prompt("Type ENABLE PRODUCTION") != "ENABLE PRODUCTION":
        raise click.Abort()
    cfg.env = "production"
    _write_config(cfg)
    click.echo("Production selected. Confirm the credentials are Production before linking.")
