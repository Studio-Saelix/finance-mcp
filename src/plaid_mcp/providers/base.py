"""Normalized models and separated runtime/admin provider protocols."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol


class Capability(str, Enum):
    ACCOUNTS = "accounts"
    BALANCES = "balances"
    TRANSACTIONS = "transactions"
    IDENTITY = "identity"
    INVESTMENTS = "investments"
    LIABILITIES = "liabilities"
    INCOME = "income"


@dataclass(frozen=True)
class Enrollment:
    """A user's link at one institution. 1 enrollment → N accounts."""

    id: str
    institution_id: str | None
    institution_name: str | None
    access_token: str
    provider: str  # "plaid"


@dataclass(frozen=True)
class Account:
    id: str
    enrollment_id: str
    name: str | None
    official_name: str | None
    type: str | None       # depository | credit | investment | loan
    subtype: str | None    # checking | savings | credit card | ...
    mask: str | None       # last 4
    iso_currency: str | None


@dataclass(frozen=True)
class Balance:
    account_id: str
    current: float | None
    available: float | None
    limit: float | None
    iso_currency: str | None


@dataclass(frozen=True)
class Transaction:
    id: str
    account_id: str
    amount: float              # positive = outflow (spend), per Plaid convention
    iso_currency: str | None
    date: str                  # YYYY-MM-DD
    authorized_date: str | None
    name: str | None
    merchant_name: str | None
    category: str | None
    subcategory: str | None
    pending: bool
    payment_channel: str | None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Identity:
    account_id: str
    names: list[str]
    emails: list[str]
    phones: list[str]
    addresses: list[dict[str, Any]]


class ReadProvider(Protocol):
    """Provider authority available to the runtime: retrieval only."""

    name: str

    def capabilities(self) -> set[Capability]:
        """Which products this provider can serve. Tools that need a missing
        capability should 4xx clean rather than hit the wire."""
        ...

    def list_accounts(self, enrollment: Enrollment) -> list[Account]:
        ...

    def get_balances(self, enrollment: Enrollment) -> list[Balance]:
        ...

    def get_transactions(
        self,
        enrollment: Enrollment,
        start_date: str,
        end_date: str,
        account_id: str | None = None,
    ) -> list[Transaction]:
        ...

class AdminProvider(Protocol):
    """Administrator-only provider authority; never registered with FastMCP."""

    def begin_enrollment(self) -> dict[str, Any]: ...

    def complete_enrollment(self, payload: dict[str, Any]) -> Enrollment: ...

    def remove_enrollment(self, enrollment: Enrollment) -> None: ...


Provider = ReadProvider
