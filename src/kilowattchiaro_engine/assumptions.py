"""Assumptions register: every money input the engines rest on, with its source and dates.

The values live in ``assumptions.yaml`` next to this module; the engine modules read
them through :func:`value`. :func:`check` is the staleness gate CI runs on every
build (``scripts/check_assumptions.py``): an overdue value is an error; a rule
ending within :data:`WARN_DAYS` days, or already ended, is a warning.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

REGISTER_PATH = Path(__file__).with_name("assumptions.yaml")
WARN_DAYS = 60

_REQUIRED = ("id", "value", "unit", "source", "verified_on", "owner", "review_every")
_REVIEW_EVERY = re.compile(r"^(\d+)d$")


@dataclass(frozen=True)
class Assumption:
    id: str
    value: Any
    unit: str
    source: str
    verified_on: date
    owner: str
    review_every_days: int
    rule_ends_on: date | None = None
    headline: str | None = None

    @property
    def review_due_on(self) -> date:
        """Last day the current check is valid."""
        return self.verified_on + timedelta(days=self.review_every_days)

    def headline_value(self) -> Any:
        """The part of ``value`` that ``headline`` points at (the whole value if none)."""
        node = self.value
        for key in (self.headline or "").split(".") if self.headline else []:
            node = node[key]
        return node


def _parse(raw: dict) -> Assumption:
    ident = raw.get("id", "?")
    for field in _REQUIRED:
        if raw.get(field) in (None, ""):
            raise ValueError(f"assumption {ident!r}: missing required field {field!r}")
    match = _REVIEW_EVERY.match(str(raw["review_every"]))
    if not match:
        raise ValueError(f"assumption {ident!r}: review_every must look like '90d'")
    for field in ("verified_on", "rule_ends_on"):
        if raw.get(field) is not None and not isinstance(raw[field], date):
            raise ValueError(f"assumption {ident!r}: {field} must be a YYYY-MM-DD date")
    entry = Assumption(
        id=raw["id"],
        value=raw["value"],
        unit=raw["unit"],
        source=" ".join(str(raw["source"]).split()),
        verified_on=raw["verified_on"],
        owner=raw["owner"],
        review_every_days=int(match.group(1)),
        rule_ends_on=raw.get("rule_ends_on"),
        headline=raw.get("headline"),
    )
    entry.headline_value()  # fail early on a headline path that does not resolve
    return entry


def load_register(path: Path = REGISTER_PATH) -> dict[str, Assumption]:
    """Parse and validate a register file, keyed by id in file order."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries: dict[str, Assumption] = {}
    for raw in data.get("assumptions") or []:
        entry = _parse(raw)
        if entry.id in entries:
            raise ValueError(f"duplicate assumption id {entry.id!r}")
        entries[entry.id] = entry
    return entries


@lru_cache(maxsize=1)
def register() -> dict[str, Assumption]:
    """The live register (``assumptions.yaml``)."""
    return load_register()


def value(ident: str) -> Any:
    """The value of one assumption in the live register."""
    return register()[ident].value


def check(entries: dict[str, Assumption], *, today: date) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for the register as of ``today``."""
    errors: list[str] = []
    warnings: list[str] = []
    for a in entries.values():
        if today > a.review_due_on:
            errors.append(
                f"{a.id}: overdue — verified {a.verified_on}, review every "
                f"{a.review_every_days} days, due {a.review_due_on}. Re-check: {a.source}"
            )
        if a.rule_ends_on is None:
            continue
        days_left = (a.rule_ends_on - today).days
        if days_left < 0:
            warnings.append(
                f"{a.id}: the rule it belongs to ended on {a.rule_ends_on}; update the value"
            )
        elif days_left <= WARN_DAYS:
            warnings.append(
                f"{a.id}: the rule it belongs to ends on {a.rule_ends_on} "
                f"({days_left} days); plan the update"
            )
    return errors, warnings
