"""Trusted prelude: the transforms integration code may call.

This file is BOTH an importable module (host-side tests compare it with the demo
transforms in ``app.demo.mocks``) AND source text that the sandbox sends to the
child, where it is executed with full builtins in its own namespace. Only the
names in ``__all__`` are copied into the (restricted) integration namespace.

Constraints: standard library only, no imports at all, no I/O. Keep the tables
in sync with ``app.demo.mocks`` (``tests/platform/sandbox`` asserts equality).
"""

ISO_4217 = frozenset({"USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "INR", "SEK"})
TERMS_CODES = frozenset({"NET15", "NET30", "NET60", "DUE_ON_RECEIPT"})
TERMS_MAP = {
    "net 15": "NET15",
    "net15": "NET15",
    "net 30": "NET30",
    "net 30 days": "NET30",
    "net30": "NET30",
    "net 60": "NET60",
    "net60": "NET60",
    "due on receipt": "DUE_ON_RECEIPT",
    "due_on_receipt": "DUE_ON_RECEIPT",
}
CURRENCY_NAMES = {
    "us dollar": "USD",
    "dollar": "USD",
    "euro": "EUR",
    "pound sterling": "GBP",
    "british pound": "GBP",
    "swiss franc": "CHF",
    "yen": "JPY",
}


class ToolError(Exception):
    """A tool call failed. Attributes: tool, code, status, message, detail (all JSON data)."""

    def __init__(self, error: object) -> None:
        error = error if isinstance(error, dict) else {"message": str(error)}
        self.tool = error.get("tool")
        self.code = error.get("code") or "tool_error"
        self.status = error.get("status")
        self.message = error.get("message") or ""
        self.detail = error.get("detail")
        status = f", HTTP {self.status}" if self.status else ""
        super().__init__(f"{self.tool} failed ({self.code}{status}): {self.message}")


def to_iso4217(value: object) -> str:
    """'Euro' -> 'EUR'; ISO codes pass through (upper-cased)."""
    v = str(value).strip()
    if v.upper() in ISO_4217:
        return v.upper()
    code = CURRENCY_NAMES.get(v.lower())
    if code is None:
        raise ValueError(f"cannot map currency {value!r} to ISO 4217")
    return code


def terms_lookup(value: object) -> str:
    """'Net 30 days' -> 'NET30' (the TERMS_MAP lookup); terms codes pass through."""
    v = str(value).strip()
    if v.upper() in TERMS_CODES:
        return v.upper()
    code = TERMS_MAP.get(v.lower())
    if code is None:
        raise ValueError(f"no terms code for {value!r}")
    return code


def geocode(address: object) -> str:
    """Address -> tax region code: 'Boston, MA, US' -> 'US-MA', 'Berlin, DE' -> 'DE'."""
    parts = [p.strip() for p in str(address).split(",") if p.strip()]
    if not parts:
        raise ValueError("empty address")
    country = parts[-1].upper()
    if country == "US" and len(parts) >= 2:
        return f"US-{parts[-2].upper()}"
    return country


def plus_tax(value: object, tax_quote: dict | None) -> float:
    """``value + tax`` from a tax.calculate result (0 when there is no quote), rounded to cents."""
    tax = float((tax_quote or {}).get("tax", 0.0))
    return round(float(value) + tax, 2)


def first(items: list, label: str) -> object:
    """First element of a collection; ``label`` names the mapping source in the error."""
    if not items:
        raise ValueError(f"{label}: empty collection")
    return items[0]


__all__ = ["TERMS_MAP", "ToolError", "first", "geocode", "plus_tax", "terms_lookup", "to_iso4217"]
