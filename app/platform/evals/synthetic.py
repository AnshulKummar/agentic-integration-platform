"""Seeded synthetic mapping cases.

Canonical business objects (customer, contact, opportunity, contract, invoice,
payment, address, line item) are described as *concepts* with synonym names and
a representation family. A :class:`VendorProfile` renders concepts under one
vendor's conventions: case style (snake/camel/Pascal/lower), object prefixes
(``cust_``), abbreviations (``amt``, ``qty``), synonym choice
(total/amount/value), nesting (``billing_city`` vs ``billing_address.city`` vs
``billing.address.city``), a wrapper object (``properties.*``) and value
representations (cents vs decimal, ISO vs epoch dates, currency codes vs names,
enum styles). A case renders one object under a source and a target vendor;
gold mappings and transforms follow from the concepts, never from a predictor.

Difficulty tiers
----------------
* easy   same vocabulary, only the case style differs; no distractors; every
         target has a source.
* medium ~35% of names re-drawn, representations flip with p=0.3, 2 distractor
         fields, 1 target-only field (gold null).
* hard   independent vendors (all names/representations re-drawn, wrapper and
         nesting may differ), 3-4 distractors, 1-2 deprecated duplicates whose
         names often match the target better, 2 target-only fields, the target's
         own ``id`` (gold null) half the time, and 1 target concept the source lacks.

Everything is driven by ``random.Random(seed)``: same seed, same cases.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field, replace

from app.platform.evals.dataset import CaseTags, Difficulty, EvalCase, EvalField, GoldMapping, ObjectRef, SampleValue

DEFAULT_SEED = 1234
CASE_STYLES = ("snake", "camel", "pascal", "lower")
NESTINGS = ("flat", "nested", "deep")
WRAPPERS = (None, "properties", "attributes")
ENUM_STYLES = ("lower", "upper", "title", "code")

#: Representation families and their options (first option = "canonical").
REPRESENTATIONS: dict[str, tuple[str, ...]] = {
    "money": ("decimal", "cents"),
    "datetime": ("iso8601_datetime", "epoch_s"),
    "date": ("iso8601_date", "epoch_s"),
    "currency": ("iso4217", "currency_name"),
    "country": ("country_iso2", "country_name"),
    "percent": ("percent", "fraction"),
}

_TRANSFORM_BY_REP: dict[tuple[str, str], str] = {
    ("decimal", "cents"): "decimal_to_cents",
    ("cents", "decimal"): "cents_to_decimal",
    ("epoch_s", "iso8601_datetime"): "epoch_to_iso_datetime",
    ("iso8601_datetime", "epoch_s"): "iso_to_epoch",
    ("epoch_s", "iso8601_date"): "epoch_to_iso_date",
    ("iso8601_date", "epoch_s"): "iso_to_epoch",
    ("currency_name", "iso4217"): "currency_name_to_code",
    ("iso4217", "currency_name"): "currency_code_to_name",
    ("country_name", "country_iso2"): "country_name_to_iso2",
    ("country_iso2", "country_name"): "country_iso2_to_name",
    ("percent", "fraction"): "percent_to_fraction",
    ("fraction", "percent"): "fraction_to_percent",
}

ABBREVIATIONS = {
    "amount": "amt", "quantity": "qty", "number": "num", "address": "addr", "description": "desc",
    "customer": "cust", "account": "acct", "invoice": "inv", "payment": "pmt", "reference": "ref",
    "telephone": "tel", "organization": "org", "transaction": "txn", "currency": "curr",
}

Words = tuple[str, ...]


@dataclass(frozen=True)
class Concept:
    key: str
    names: tuple[Words, ...]
    #: text | id | ref | email | phone | int | bool | enum | money | datetime | date | currency | country | percent
    rep: str = "text"
    group: str | None = None
    role: str = "core"  # core | distractor
    enum_values: tuple[str, ...] = ()
    #: For target-side concepts populated from a differently-keyed source concept (ext_ref <- id).
    source_concept: str | None = None


GROUPS: dict[str, tuple[Words, ...]] = {
    "billing": (("billing", "address"), ("bill", "to"), ("billing",)),
    "shipping": (("shipping", "address"), ("ship", "to"), ("shipping",)),
    "mailing": (("mailing", "address"), ("mailing",), ("home", "address")),
    "signer": (("signer",), ("signatory",), ("recipient",)),
}

_LINE1: tuple[Words, ...] = (("street",), ("line1",), ("address", "line1"), ("street", "address"))
_CITY: tuple[Words, ...] = (("city",), ("town",), ("locality",))
_POSTAL: tuple[Words, ...] = (("postal", "code"), ("zip",), ("zip", "code"), ("postcode",))
_COUNTRY: tuple[Words, ...] = (("country",), ("country", "code"))
_ID = Concept("id", (("id",), ("record", "id"), ("uid",)), "id")
_EXT = Concept(
    "ext_ref", (("external", "id"), ("source", "id"), ("external", "ref"), ("partner", "id")), "id",
    source_concept="id",
)
_CURRENCY = Concept("currency", (("currency",), ("currency", "code"), ("default", "currency"), ("currency", "iso")),
                    "currency")
_CREATED = Concept("created_at", (("created", "at"), ("created", "date"), ("creation", "time"), ("created",)),
                   "datetime", role="distractor")
_UPDATED = Concept("updated_at", (("updated", "at"), ("last", "modified", "date"), ("modified", "time")), "datetime",
                   role="distractor")
_TERMS = Concept("payment_terms", (("payment", "terms"), ("terms",), ("terms", "code")), "enum",
                 enum_values=("net_15", "net_30", "net_60", "due_on_receipt"))

OBJECTS: dict[str, tuple[Concept, ...]] = {
    "customer": (
        _ID, _EXT,
        Concept("name", (("name",), ("company", "name"), ("legal", "name"), ("display", "name"), ("account", "name"))),
        Concept("email", (("email",), ("email", "address"), ("primary", "email"))),
        Concept("phone", (("phone",), ("phone", "number"), ("telephone",), ("primary", "phone")), "phone"),
        Concept("website", (("website",), ("web", "site"), ("url",), ("domain",))),
        _CURRENCY,
        Concept("tax_id", (("tax", "id"), ("vat", "number"), ("tax", "number"), ("tax", "registration", "number"))),
        Concept("billing_line1", _LINE1, group="billing"),
        Concept("billing_city", _CITY, group="billing"),
        Concept("billing_postal", _POSTAL, group="billing"),
        Concept("billing_country", _COUNTRY, "country", group="billing"),
        Concept("shipping_line1", _LINE1, group="shipping", role="distractor"),
        Concept("shipping_city", _CITY, group="shipping", role="distractor"),
        _CREATED, _UPDATED,
        Concept("status", (("status",), ("state",), ("lifecycle", "status")), "enum",
                enum_values=("active", "inactive", "suspended")),
        Concept("annual_revenue", (("annual", "revenue"), ("yearly", "revenue"), ("revenue",)), "money",
                role="distractor"),
        Concept("employee_count", (("employee", "count"), ("number", "of", "employees"), ("headcount",)), "int",
                role="distractor"),
        _TERMS,
        Concept("notes", (("notes",), ("description",), ("comments",))),
    ),
    "contact": (
        _ID, _EXT,
        Concept("first_name", (("first", "name"), ("given", "name"), ("firstname",))),
        Concept("last_name", (("last", "name"), ("family", "name"), ("surname",))),
        Concept("email", (("email",), ("email", "address"), ("work", "email"))),
        Concept("secondary_email", (("secondary", "email"), ("personal", "email"), ("alt", "email")),
                role="distractor"),
        Concept("phone", (("phone",), ("phone", "number"), ("work", "phone")), "phone"),
        Concept("mobile", (("mobile",), ("mobile", "phone"), ("cell", "phone")), "phone", role="distractor"),
        Concept("title", (("title",), ("job", "title"), ("position",))),
        Concept("department", (("department",), ("dept",), ("division",))),
        Concept("mailing_line1", _LINE1, group="mailing"),
        Concept("mailing_city", _CITY, group="mailing"),
        Concept("mailing_postal", _POSTAL, group="mailing"),
        Concept("mailing_country", _COUNTRY, "country", group="mailing"),
        Concept("opt_out", (("email", "opt", "out"), ("unsubscribed",), ("do", "not", "email")), "bool"),
        Concept("account_ref", (("account", "id"), ("company", "id"), ("organization", "id")), "ref"),
        _CREATED, _UPDATED,
    ),
    "opportunity": (
        _ID, _EXT,
        Concept("name", (("name",), ("deal", "name"), ("title",), ("opportunity", "name"))),
        Concept("amount", (("amount",), ("value",), ("total",), ("deal", "value"), ("total", "amount")), "money"),
        Concept("expected_revenue", (("expected", "revenue"), ("weighted", "amount"), ("forecast", "amount")),
                "money", role="distractor"),
        Concept("close_date", (("close", "date"), ("closed", "at"), ("expected", "close", "date")), "date"),
        Concept("stage", (("stage",), ("stage", "name"), ("deal", "stage"), ("pipeline", "stage")), "enum",
                enum_values=("prospecting", "negotiation", "closed_won", "closed_lost")),
        Concept("probability", (("probability",), ("win", "probability"), ("likelihood",)), "percent"),
        _CURRENCY,
        Concept("owner", (("owner", "id"), ("owner",), ("sales", "rep", "id")), "ref"),
        Concept("description", (("description",), ("notes",), ("summary",))),
        _TERMS,
        Concept("contract_start", (("contract", "start"), ("start", "date"), ("service", "start", "date")), "date"),
        Concept("term_months", (("term", "months"), ("contract", "term"), ("duration", "months")), "int"),
        _CREATED, _UPDATED,
    ),
    "contract": (
        _ID, _EXT,
        Concept("title", (("title",), ("name",), ("subject",), ("agreement", "name"))),
        Concept("status", (("status",), ("state",), ("agreement", "status")), "enum",
                enum_values=("draft", "sent", "signed", "expired")),
        Concept("start_date", (("start", "date"), ("effective", "date"), ("starts", "at")), "date"),
        Concept("end_date", (("end", "date"), ("expiration", "date"), ("expires", "at")), "date"),
        Concept("contract_value", (("contract", "value"), ("total", "value"), ("amount",)), "money"),
        _CURRENCY,
        Concept("signer_email", (("email",), ("email", "address")), "email", group="signer"),
        Concept("signer_name", (("name",), ("full", "name")), group="signer"),
        Concept("signed_at", (("signed", "at"), ("completed", "at"), ("signature", "date")), "datetime"),
        Concept("sent_at", (("sent", "at"), ("sent", "date"), ("issued", "at")), "datetime", role="distractor"),
        _TERMS,
        Concept("auto_renew", (("auto", "renew"), ("auto", "renewal"), ("renews", "automatically")), "bool"),
        _CREATED,
    ),
    "invoice": (
        _ID, _EXT,
        Concept("number", (("number",), ("invoice", "number"), ("doc", "number"), ("reference",))),
        Concept("customer_ref", (("customer", "id"), ("customer",), ("account", "id"), ("client", "id")), "ref"),
        Concept("issue_date", (("issue", "date"), ("invoice", "date"), ("date",), ("transaction", "date")), "date"),
        Concept("due_date", (("due", "date"), ("payment", "due"), ("due", "at")), "date"),
        Concept("total", (("total",), ("total", "amount"), ("amount", "due"), ("grand", "total")), "money"),
        Concept("subtotal", (("subtotal",), ("net", "amount"), ("amount", "before", "tax")), "money",
                role="distractor"),
        Concept("tax_amount", (("tax",), ("tax", "amount"), ("total", "tax")), "money"),
        Concept("amount_paid", (("amount", "paid"), ("paid", "amount"), ("payments", "applied")), "money",
                role="distractor"),
        _CURRENCY,
        Concept("status", (("status",), ("state",), ("invoice", "status")), "enum",
                enum_values=("draft", "open", "paid", "void")),
        Concept("memo", (("memo",), ("notes",), ("customer", "memo"))),
        Concept("billing_email", (("billing", "email"), ("bill", "email"), ("email", "to")), "email"),
        _CREATED,
    ),
    "payment": (
        _ID, _EXT,
        Concept("amount", (("amount",), ("total", "amount"), ("payment", "amount"), ("value",)), "money"),
        Concept("fee", (("fee",), ("processing", "fee"), ("fee", "amount")), "money", role="distractor"),
        Concept("refunded", (("amount", "refunded"), ("refunded", "amount")), "money", role="distractor"),
        _CURRENCY,
        Concept("paid_at", (("paid", "at"), ("payment", "date"), ("transaction", "date"), ("received", "at")),
                "datetime"),
        Concept("method", (("method",), ("payment", "method"), ("payment", "type")), "enum",
                enum_values=("card", "ach", "wire", "check")),
        Concept("status", (("status",), ("state",), ("payment", "status")), "enum",
                enum_values=("pending", "succeeded", "failed", "refunded")),
        Concept("invoice_ref", (("invoice", "id"), ("invoice",), ("applied", "to", "invoice")), "ref"),
        Concept("reference", (("reference",), ("reference", "number"), ("receipt", "number"))),
        Concept("payer_email", (("payer", "email"), ("receipt", "email"), ("customer", "email")), "email"),
        _CREATED,
    ),
    "address": (
        _ID, _EXT,
        Concept("line1", _LINE1),
        Concept("line2", (("line2",), ("address", "line2"), ("suite",))),
        Concept("city", _CITY),
        Concept("region", (("state",), ("region",), ("province",), ("state", "code"))),
        Concept("postal", _POSTAL),
        Concept("country", _COUNTRY, "country"),
        Concept("kind", (("type",), ("address", "type"), ("purpose",)), "enum", enum_values=("billing", "shipping")),
        Concept("is_default", (("is", "default"), ("default",), ("primary",)), "bool"),
        Concept("latitude", (("latitude",), ("lat",)), "text", role="distractor"),
        Concept("longitude", (("longitude",), ("lng",)), "text", role="distractor"),
        _UPDATED,
    ),
    "line_item": (
        _ID, _EXT,
        Concept("description", (("description",), ("name",), ("item", "description"))),
        Concept("sku", (("sku",), ("item", "code"), ("product", "code"), ("part", "number"))),
        Concept("quantity", (("quantity",), ("units",), ("qty",)), "int"),
        Concept("unit_price", (("unit", "price"), ("rate",), ("price",)), "money"),
        Concept("amount", (("amount",), ("line", "total"), ("extended", "price"), ("total",)), "money"),
        Concept("discount", (("discount",), ("discount", "amount")), "money", role="distractor"),
        Concept("tax_code", (("tax", "code"), ("tax", "category"))),
        Concept("tax_amount", (("tax", "amount"), ("tax",)), "money", role="distractor"),
        Concept("product_ref", (("product", "id"), ("item", "id")), "ref"),
        Concept("position", (("line", "number"), ("position",), ("sequence",)), "int"),
    ),
}

#: Target-only fields: integration config or target-system state no source carries (gold null).
TARGET_ONLY: tuple[Concept, ...] = (
    Concept("template_id", (("template", "id"), ("layout", "id"))),
    Concept("sync_token", (("sync", "token"), ("etag",), ("version", "token"))),
    Concept("workflow_id", (("workflow", "id"), ("process", "id"))),
    Concept("assigned_team", (("assigned", "team"), ("team",), ("queue",))),
    Concept("portal_url", (("portal", "url"), ("hosted", "url"))),
    Concept("dunning_enabled", (("dunning", "enabled"), ("auto", "reminders")), "bool"),
    Concept("record_source", (("record", "source"), ("origin",), ("integration", "source"))),
)

OBJECT_NAMES: dict[str, tuple[str, ...]] = {
    "customer": ("Customer", "Account", "Client", "Company", "Organization"),
    "contact": ("Contact", "Person", "Individual"),
    "opportunity": ("Opportunity", "Deal", "SalesDeal"),
    "contract": ("Contract", "Agreement", "Envelope"),
    "invoice": ("Invoice", "Bill", "SalesInvoice"),
    "payment": ("Payment", "Charge", "Transaction", "Receipt"),
    "address": ("Address", "Location", "PostalAddress"),
    "line_item": ("LineItem", "InvoiceLine", "OrderLine", "Item"),
}
OBJECT_ABBREV = {"customer": "cust", "contact": "ct", "opportunity": "opp", "contract": "agr", "invoice": "inv",
                 "payment": "pmt", "address": "addr", "line_item": "li"}
SYSTEM_PAIRS: dict[str, tuple[tuple[str, str], ...]] = {
    "customer": (("crm", "billing"), ("crm", "erp"), ("crm", "accounting"), ("erp", "crm")),
    "contact": (("crm", "marketing"), ("crm", "esign"), ("marketing", "crm")),
    "opportunity": (("crm", "erp"), ("crm", "billing")),
    "contract": (("crm", "esign"), ("esign", "crm"), ("esign", "billing")),
    "invoice": (("billing", "accounting"), ("billing", "erp"), ("erp", "billing")),
    "payment": (("billing", "accounting"), ("billing", "erp")),
    "address": (("crm", "tax"), ("erp", "tax")),
    "line_item": (("billing", "tax"), ("erp", "tax"), ("crm", "billing")),
}
CATEGORIES: tuple[str, ...] = tuple(OBJECTS)


@dataclass(frozen=True)
class VendorProfile:
    system: str
    kind: str
    case_style: str
    prefix: bool
    abbreviate: bool
    nesting: str
    wrapper: str | None
    enum_style: str
    synonym: dict[str, int] = field(default_factory=dict)
    group_synonym: dict[str, int] = field(default_factory=dict)
    reps: dict[str, str] = field(default_factory=dict)
    object_name: dict[str, int] = field(default_factory=dict)

    def rep_for(self, concept: Concept) -> str:
        return self.reps.get(concept.rep, REPRESENTATIONS[concept.rep][0]) if concept.rep in REPRESENTATIONS else ""


def _all_keys() -> list[str]:
    keys = {c.key for cs in OBJECTS.values() for c in cs} | {c.key for c in TARGET_ONLY}
    return sorted(keys)


def random_vendor(rng: random.Random, system: str, kind: str) -> VendorProfile:
    return VendorProfile(
        system=system,
        kind=kind,
        case_style=rng.choice(CASE_STYLES),
        prefix=rng.random() < 0.25,
        abbreviate=rng.random() < 0.3,
        nesting=rng.choice(NESTINGS),
        wrapper=rng.choice(WRAPPERS) if rng.random() < 0.3 else None,
        enum_style=rng.choice(ENUM_STYLES),
        synonym={k: rng.randrange(8) for k in _all_keys()},
        group_synonym={g: rng.randrange(len(v)) for g, v in GROUPS.items()},
        reps={fam: rng.choice(opts) for fam, opts in REPRESENTATIONS.items()},
        object_name={cat: rng.randrange(len(names)) for cat, names in OBJECT_NAMES.items()},
    )


def perturb(base: VendorProfile, rng: random.Random, system: str, kind: str, *, p_syn: float, p_rep: float,
            p_structure: float) -> VendorProfile:
    """A vendor that shares part of ``base``'s vocabulary (controls difficulty)."""
    synonym = {k: (rng.randrange(8) if rng.random() < p_syn else v) for k, v in base.synonym.items()}
    reps = {fam: (rng.choice(REPRESENTATIONS[fam]) if rng.random() < p_rep else v) for fam, v in base.reps.items()}
    styles = [s for s in CASE_STYLES if s != base.case_style]
    return replace(
        base,
        system=system,
        kind=kind,
        case_style=rng.choice(styles),
        nesting=rng.choice(NESTINGS) if rng.random() < p_structure else base.nesting,
        abbreviate=(not base.abbreviate) if rng.random() < p_structure else base.abbreviate,
        enum_style=rng.choice(ENUM_STYLES) if rng.random() < p_rep else base.enum_style,
        synonym=synonym,
        reps=reps,
    )


# ------------------------------------------------------------------ rendering
def _style(words: Words, style: str) -> str:
    words = tuple(w for w in words if w)
    if style == "snake":
        return "_".join(words)
    if style == "lower":
        return "".join(words)
    if style == "pascal":
        return "".join(w[:1].upper() + w[1:] for w in words)
    return words[0] + "".join(w[:1].upper() + w[1:] for w in words[1:])


def _abbrev(words: Words, vendor: VendorProfile) -> Words:
    return tuple(ABBREVIATIONS.get(w, w) for w in words) if vendor.abbreviate else words


def render_path(concept: Concept, vendor: VendorProfile, category: str, synonym_shift: int = 0) -> str:
    names = concept.names
    words = _abbrev(names[(vendor.synonym.get(concept.key, 0) + synonym_shift) % len(names)], vendor)
    segments: list[Words]
    if concept.group is None:
        segments = [words]
    else:
        gnames = GROUPS[concept.group]
        gwords = _abbrev(gnames[vendor.group_synonym.get(concept.group, 0) % len(gnames)], vendor)
        if vendor.nesting == "flat":
            segments = [gwords + words]
        elif vendor.nesting == "nested":
            segments = [gwords, words]
        else:
            segments = [(w,) for w in gwords] + [words]
    if vendor.prefix:
        segments[0] = (OBJECT_ABBREV[category],) + segments[0]
    path = ".".join(_style(s, vendor.case_style) for s in segments)
    return f"{vendor.wrapper}.{path}" if vendor.wrapper else path


def _enum_values(concept: Concept, style: str) -> tuple[str, ...]:
    if style == "upper":
        return tuple(v.upper() for v in concept.enum_values)
    if style == "title":
        return tuple(v.replace("_", " ").title() for v in concept.enum_values)
    if style == "code":
        return tuple(str(i + 1) for i in range(len(concept.enum_values)))
    return concept.enum_values


_COMPANIES = ("Acme Corp", "Globex Ltd", "Initech GmbH", "Umbrella SA", "Stark Industries", "Wayne Enterprises")
_CITIES = (("San Francisco", "US", "United States"), ("Berlin", "DE", "Germany"), ("Toronto", "CA", "Canada"),
           ("London", "GB", "United Kingdom"))
_CURRENCIES = (("USD", "US Dollar"), ("EUR", "Euro"), ("CAD", "Canadian Dollar"), ("GBP", "Pound Sterling"))


@dataclass(frozen=True)
class _World:
    """Underlying values shared by source and target samples of one case."""

    company: str
    city: tuple[str, str, str]
    currency: tuple[str, str]
    money: float
    epoch: int
    enum_index: int
    number: int


def _world(rng: random.Random) -> _World:
    return _World(
        company=rng.choice(_COMPANIES), city=rng.choice(_CITIES), currency=rng.choice(_CURRENCIES),
        money=round(rng.uniform(100, 90000), 2), epoch=rng.randrange(1_600_000_000, 1_780_000_000),
        enum_index=rng.randrange(4), number=rng.randrange(1, 500),
    )


def _iso(epoch: int, date_only: bool) -> str:
    from datetime import UTC, datetime

    dt = datetime.fromtimestamp(epoch, UTC)
    return dt.date().isoformat() if date_only else dt.isoformat().replace("+00:00", "Z")


def _sample(concept: Concept, vendor: VendorProfile, world: _World) -> SampleValue:
    rep = vendor.rep_for(concept)
    if concept.rep == "money":
        return int(round(world.money * 100)) if rep == "cents" else world.money
    if concept.rep in ("datetime", "date"):
        return world.epoch if rep == "epoch_s" else _iso(world.epoch, concept.rep == "date")
    if concept.rep == "currency":
        return world.currency[0] if rep == "iso4217" else world.currency[1]
    if concept.rep == "country":
        return world.city[1] if rep == "country_iso2" else world.city[2]
    if concept.rep == "percent":
        return 60 if rep == "percent" else 0.6
    if concept.rep == "enum":
        values = _enum_values(concept, vendor.enum_style)
        return values[world.enum_index % len(values)]
    if concept.rep == "bool":
        return world.number % 2 == 0
    if concept.rep == "int":
        return world.number
    if concept.rep in ("id", "ref"):
        return f"{concept.key[:3]}_{world.number:05d}{world.epoch % 997}"
    if concept.rep == "email":
        return f"ap@{world.company.split()[0].lower()}.example"
    if concept.rep == "phone":
        return f"+1-415-555-{world.number:04d}"
    if "city" in concept.key:
        return world.city[0]
    if "name" in concept.key or concept.key == "title":
        return world.company
    return f"{concept.key.replace('_', ' ')} {world.number}"


def _field_type(concept: Concept, rep: str) -> str:
    if concept.rep == "money":
        return "integer" if rep == "cents" else "number"
    if concept.rep in ("datetime", "date"):
        return "integer" if rep == "epoch_s" else concept.rep
    if concept.rep == "percent":
        return "number"
    if concept.rep == "int":
        return "integer"
    if concept.rep == "bool":
        return "boolean"
    if concept.rep == "enum":
        return "enum"
    return "string"


def _render_field(concept: Concept, vendor: VendorProfile, category: str, world: _World, path: str,
                  *, deprecated_for: str | None = None) -> EvalField:
    rep = vendor.rep_for(concept)
    fmt = rep or (concept.rep if concept.rep in ("email", "phone", "id", "ref") else None)
    desc = concept.key.replace("_", " ")
    if deprecated_for is not None:
        desc = f"Deprecated: use {deprecated_for}"
    return EvalField(
        path=path,
        type=_field_type(concept, rep),  # type: ignore[arg-type]
        format=fmt,
        description=desc,
        sample=_sample(concept, vendor, world),
        enum=_enum_values(concept, vendor.enum_style) if concept.rep == "enum" else None,
        deprecated=deprecated_for is not None,
    )


def gold_transform(concept: Concept, source: VendorProfile, target: VendorProfile) -> str | None:
    if concept.rep == "ref":
        return "lookup"
    if concept.rep == "enum":
        return "enum_map" if source.enum_style != target.enum_style else None
    if concept.rep in REPRESENTATIONS:
        return _TRANSFORM_BY_REP.get((source.rep_for(concept), target.rep_for(concept)))
    return None


class _Paths:
    """Unique path allocation within one schema (falls back to other synonyms on collision)."""

    def __init__(self, vendor: VendorProfile, category: str) -> None:
        self.vendor, self.category, self.used = vendor, category, set[str]()

    def take(self, concept: Concept, start_shift: int = 0) -> str:
        for shift in range(start_shift, start_shift + len(concept.names) + 1):
            path = render_path(concept, self.vendor, self.category, shift)
            if path not in self.used:
                self.used.add(path)
                return path
        path = f"{render_path(concept, self.vendor, self.category)}_{concept.key}"
        self.used.add(path)
        return path


@dataclass(frozen=True)
class Knobs:
    n_targets: tuple[int, int]
    n_distractors: int
    n_deprecated: int
    n_target_only: int
    n_dropped: int
    target_own_id_p: float


KNOBS: dict[Difficulty, Knobs] = {
    "easy": Knobs((6, 8), 0, 0, 0, 0, 0.0),
    "medium": Knobs((7, 9), 2, 0, 1, 0, 0.0),
    "hard": Knobs((8, 10), 4, 2, 2, 1, 0.5),
}


def render_case(rng: random.Random, case_id: str, category: str, src: VendorProfile, tgt: VendorProfile,
                difficulty: Difficulty, *, sequence: str | None = None, step: int | None = None) -> EvalCase:
    knobs = KNOBS[difficulty]
    concepts = OBJECTS[category]
    by_key = {c.key: c for c in concepts}
    core = [c for c in concepts if c.role == "core" and c.key != "id"]
    k = rng.randint(*knobs.n_targets)
    target_concepts = rng.sample(core, min(k, len(core)))
    world = _world(rng)

    # Source concepts: what the targets need (minus dropped ones) + distractors.
    needed = {(c.source_concept or c.key) for c in target_concepts}
    droppable = sorted(needed - {"id"})
    dropped = set(rng.sample(droppable, min(knobs.n_dropped, len(droppable))))
    source_keys = (needed - dropped) | {"id"}
    pool = sorted({c.key for c in concepts if c.key not in source_keys and c.key not in dropped and c.key != "ext_ref"})
    distractor_first = sorted(pool, key=lambda key: (by_key[key].role != "distractor", rng.random()))
    source_keys |= set(distractor_first[: knobs.n_distractors])

    src_paths, tgt_paths = _Paths(src, category), _Paths(tgt, category)
    source_fields: list[EvalField] = []
    source_path_of: dict[str, str] = {}
    for key in sorted(source_keys, key=lambda x: [c.key for c in concepts].index(x)):
        path = src_paths.take(by_key[key])
        source_path_of[key] = path
        source_fields.append(_render_field(by_key[key], src, category, world, path))

    # Deprecated duplicates: an old name for a needed concept (often the target's own name).
    for key in rng.sample(sorted((needed - dropped) - {"id"}), min(knobs.n_deprecated, len(needed - dropped - {"id"}))):
        concept = by_key[key]
        target_shift = (tgt.synonym.get(key, 0) - src.synonym.get(key, 0)) % len(concept.names)
        path = src_paths.take(concept, target_shift or 1)
        source_fields.append(
            _render_field(concept, src, category, world, path, deprecated_for=source_path_of[key])
        )

    target_fields: list[EvalField] = []
    gold: list[GoldMapping] = []
    for concept in target_concepts:
        path = tgt_paths.take(concept)
        target_fields.append(_render_field(concept, tgt, category, world, path))
        skey = concept.source_concept or concept.key
        if skey in dropped:
            gold.append(GoldMapping(target=path, source=None))
        else:
            src_concept = by_key[skey]
            transform = None if concept.source_concept else gold_transform(src_concept, src, tgt)
            gold.append(GoldMapping(target=path, source=source_path_of[skey], transform=transform))
    extras = rng.sample(TARGET_ONLY, knobs.n_target_only)
    if rng.random() < knobs.target_own_id_p:
        extras.append(_ID)
    for concept in extras:
        path = tgt_paths.take(concept)
        target_fields.append(_render_field(concept, tgt, category, world, path))
        gold.append(GoldMapping(target=path, source=None))

    order = list(range(len(target_fields)))
    rng.shuffle(order)
    rng.shuffle(source_fields)
    return EvalCase(
        id=case_id,
        suite="synthetic",
        source=ObjectRef(system=src.system, kind=src.kind,
                         object=OBJECT_NAMES[category][src.object_name.get(category, 0)]),
        target=ObjectRef(system=tgt.system, kind=tgt.kind,
                         object=OBJECT_NAMES[category][tgt.object_name.get(category, 0)]),
        source_fields=tuple(source_fields),
        target_fields=tuple(target_fields[i] for i in order),
        gold=tuple(gold[i] for i in order),
        tags=CaseTags(difficulty=difficulty, category=category, vendor_family=f"{src.case_style}->{tgt.case_style}"),
        sequence=sequence,
        step=step,
    )


def _pair(rng: random.Random, category: str, difficulty: Difficulty, n: int) -> tuple[VendorProfile, VendorProfile]:
    skind, tkind = rng.choice(SYSTEM_PAIRS[category])
    src = random_vendor(rng, f"{skind}-v{n:03d}s", skind)
    tsys = f"{tkind}-v{n:03d}t"
    if difficulty == "easy":
        tgt = perturb(src, rng, tsys, tkind, p_syn=0.0, p_rep=0.0, p_structure=0.0)
    elif difficulty == "medium":
        tgt = perturb(src, rng, tsys, tkind, p_syn=0.35, p_rep=0.3, p_structure=0.3)
    else:
        tgt = random_vendor(rng, tsys, tkind)
    return src, tgt


def generate_suite(seed: int = DEFAULT_SEED, variants: int = 3) -> list[EvalCase]:
    """``len(CATEGORIES) * 3 difficulties * variants`` independent cases."""
    rng = random.Random(seed)
    cases: list[EvalCase] = []
    n = 0
    difficulties: tuple[Difficulty, ...] = ("easy", "medium", "hard")
    for category in CATEGORIES:
        for difficulty in difficulties:
            for v in range(variants):
                n += 1
                src, tgt = _pair(rng, category, difficulty, n)
                cases.append(render_case(rng, f"syn-{category}-{difficulty}-{v + 1}", category, src, tgt,
                                         difficulty))
    return cases


def generate_sequences(seed: int = DEFAULT_SEED, n_sequences: int = 8, length: int = 6,
                       difficulty: Difficulty = "medium") -> list[list[EvalCase]]:
    """Ordered integration histories for the learning curve.

    Each sequence fixes one category and one *hub* vendor (the tenant's own
    system). Even-numbered sequences are ``hub_target`` (new partner sources
    feeding the same target API), odd ones ``hub_source`` (the same source
    pushed to new partner targets). Spoke vendors are independent, so naming
    varies at every step; the target subset varies too.
    """
    rng = random.Random(seed * 7919 + 17)
    sequences: list[list[EvalCase]] = []
    for i in range(n_sequences):
        category = CATEGORIES[i % len(CATEGORIES)]
        skind, tkind = rng.choice(SYSTEM_PAIRS[category])
        hub_is_target = i % 2 == 0
        hub = random_vendor(rng, f"{tkind if hub_is_target else skind}-hub{i:02d}", tkind if hub_is_target else skind)
        name = f"syn-seq{i:02d}-{category}-{'hub_target' if hub_is_target else 'hub_source'}"
        steps: list[EvalCase] = []
        for step in range(1, length + 1):
            kind = skind if hub_is_target else tkind
            spoke = random_vendor(rng, f"{kind}-seq{i:02d}-p{step}", kind)
            src, tgt = (spoke, hub) if hub_is_target else (hub, spoke)
            steps.append(render_case(rng, f"{name}-s{step}", category, src, tgt, difficulty, sequence=name, step=step))
        sequences.append(steps)
    return sequences
