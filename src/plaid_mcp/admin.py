"""Explicit human administrator CLI; never imported by the MCP runtime."""

from __future__ import annotations

import logging
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

import click
from plaid.exceptions import ApiException

from .config import Config
from .credentials import CredentialValidationError, validate_credential
from .crypto import CredentialError
from .link import complete_link, create_hosted_link
from .logging_setup import configure_logging
from .paths import (
    config_dir,
    config_path,
    data_dir,
    ensure_private_dir,
    ensure_private_file,
    lock_dir,
    log_path,
    state_dir,
)
from .storage import Storage
from .tools_transactions import remove_institution

logger = logging.getLogger("plaid_mcp")


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
    ensure_private_dir(path)


def _write_config(cfg: Config) -> None:
    content = (
        f'plaid_env = "{cfg.env}"\n'
        f'country_codes = "{",".join(cfg.country_codes)}"\n'
        f'products = "{",".join(cfg.products)}"\n'
        f'optional_products = "{",".join(cfg.optional_products)}"\n'
        f'client_name = "{cfg.client_name}"\n'
    )
    _mkdir_private(config_dir())
    target = config_path()
    ensure_private_file(target, create=True) if target.exists() else None
    with tempfile.NamedTemporaryFile("w", dir=config_dir(), prefix=".config.", delete=False) as fh:
        temp = Path(fh.name)
        os.fchmod(fh.fileno(), 0o600)
        fh.write(content)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(temp, target)
    ensure_private_file(target)


def _open_storage(cfg: Config, *, create_key: bool = False) -> Storage:
    return Storage(cfg.db_path, cfg.master_key_path, create_key=create_key)


def _admin_config(*, require_credentials: bool = True) -> Config:
    try:
        return Config.from_env(require_credentials=require_credentials)
    except CredentialError as exc:
        raise click.ClickException(str(exc)) from None


def _prompt_credential(label: str) -> str:
    while True:
        value = click.prompt(label, hide_input=True)
        try:
            return validate_credential(value, label)
        except CredentialValidationError as exc:
            click.echo(str(exc), err=True)


def _credential_issue(value: str | None, label: str) -> str | None:
    if value is None:
        return f"{label} must not be empty"
    try:
        validate_credential(value, label)
    except CredentialValidationError as exc:
        return str(exc)
    return None


def _report_provider_error(exc: ApiException) -> click.ClickException:
    status = exc.status if isinstance(exc.status, int) else None
    logger.warning("admin_provider_failure provider=Plaid status=%s", status)
    message = "Plaid request failed"
    if status is not None:
        message += f" (HTTP {status})"
    return click.ClickException(message + ".")


@click.group()
def main() -> None:
    """Studio Saelix Finance administrator commands."""
    configure_logging(log_path())


@main.command()
def init() -> None:
    """Initialize private state and capture Plaid credentials interactively."""
    with _admin_lock():
        logger.info("admin_init_start")
        existing_config = config_path().exists()
        for path in (config_dir(), data_dir(), state_dir(), lock_dir()):
            _mkdir_private(path)
        cfg = _admin_config(require_credentials=False)
        if not existing_config:
            cfg.env = "sandbox"
        try:
            secret_name = cfg.credential_secret_name
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        try:
            storage = _open_storage(cfg, create_key=True)
        except CredentialError as exc:
            raise click.ClickException(str(exc)) from exc
        try:
            client_id = storage.get_secret("plaid_client_id")
            secret = storage.get_secret(secret_name)
            issue = _credential_issue(client_id, "Plaid client ID")
            if issue:
                if client_id is not None:
                    click.echo(f"Stored Plaid client ID needs repair: {issue}", err=True)
                client_id = _prompt_credential("Plaid client ID")
                storage.save_secret("plaid_client_id", client_id)
            issue = _credential_issue(secret, "Plaid secret")
            if issue:
                if secret is not None:
                    click.echo(f"Stored Plaid secret needs repair: {issue}", err=True)
                secret = _prompt_credential("Plaid secret")
                storage.save_secret(secret_name, secret)
            _write_config(cfg)
        finally:
            storage.close()
    click.echo(f"Initialized {cfg.env.title()} state. Next: studio-saelix-finance link")
    logger.info("admin_init_complete environment=%s", cfg.env)


@main.command()
@click.option("--no-open", is_flag=True, help="Do not open the browser")
def link(no_open: bool) -> None:
    """Link one institution through Plaid Hosted Link."""
    cfg = _admin_config()
    logger.info("admin_link_start environment=%s", cfg.env)
    with _admin_lock():
        storage = _open_storage(cfg)
        try:
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
                click.echo(
                    f"Linked {result.get('institution_name') or 'institution'} "
                    f"({result['accounts']} accounts)."
                )
            except ApiException as exc:
                raise _report_provider_error(exc) from None
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
        logger.info("admin_status environment=%s", cfg.env)
        click.echo(f"Environment: {cfg.env}")
        items = storage.list_items()
        click.echo(f"Credential store: ready\nInstitutions: {len(items)}")
        for item in items:
            click.echo(
                f"- {item.get('institution_name') or '(unknown)'} "
                f"({storage.count_accounts(item['item_id'])} accounts) "
                f"health={item.get('last_error') or 'ok'}"
            )
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
    cfg = _admin_config()
    logger.info("admin_unlink_start item_id=%s force_local_purge=%s", item_id, force_local_purge)
    with _admin_lock():
        storage = _open_storage(cfg)
        try:
            result = remove_institution(storage, item_id, force_local_purge=force_local_purge)
            click.echo(result.get("error") or result.get("status"))
            if result.get("status") == "upstream_failed":
                raise click.ClickException("Upstream removal failed; local state was preserved")
        finally:
            storage.close()


@main.command("use-production")
def use_production() -> None:
    """Select Production only after commissioning, audits, and human approval."""
    with _admin_lock():
        logger.info("admin_production_transition_start")
        cfg = _admin_config(require_credentials=False)
        if cfg.env == "production":
            storage = _open_storage(cfg)
            try:
                try:
                    production_secret = storage.get_secret("plaid_secret_production")
                except CredentialError:
                    production_secret = None
                if production_secret and not _credential_issue(
                    production_secret, "Plaid Production secret"
                ):
                    click.echo("Production is already selected.")
                    return
                click.echo("Production is selected but its credential is missing or unusable.")
                production_secret = _prompt_credential("Plaid Production secret")
                storage.save_secret("plaid_secret_production", production_secret)
            finally:
                storage.close()
            click.echo("Production credential repaired; linked Items were unchanged.")
            return
        storage = _open_storage(cfg)
        try:
            if storage.list_items():
                raise click.ClickException(
                    "Unlink all Sandbox Items before switching to Production"
                )
            click.echo(
                "Production accesses real financial data. Continue only after Sandbox "
                "commissioning, completion/security audit, and explicit human approval."
            )
            if click.prompt("Type ENABLE PRODUCTION") != "ENABLE PRODUCTION":
                raise click.Abort()
            production_secret = _prompt_credential("Plaid Production secret")
            storage.save_secret("plaid_secret_production", production_secret)
            cfg.env = "production"
            _write_config(cfg)
        finally:
            storage.close()
    click.echo("Production selected with a newly captured Production secret.")
