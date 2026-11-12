"""Codegen: reviewable output, tool lists, injection safety, prelude parity with app.demo."""

from __future__ import annotations

from typing import Any

import pytest

from app.demo import mocks
from app.platform.sandbox import prelude
from app.platform.sandbox.codegen import (
    AGREEMENT_COMPLETED,
    CLOSED_WON,
    CodegenError,
    derive_operations,
    detect_trigger,
    render_runnable,
    required_tools,
)
from app.platform.sandbox.validate import find_violations


def _row(id_: str, source: str, target: str, transform: str | None = "copy", **extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {"id": id_, "source": source, "target": target, "origin": "llm_new", "confidence": 0.8}
    if transform is not None:
        row["transform"] = transform
    row.update(extra)
    return row


BASE = [
    _row("m-name", "Account.name", "Customer.legal_name"),
    _row("m-cur", "Account.currency", "Customer.currency_code", "to_iso4217()"),
    _row("m-terms", "Opportunity.payment_terms", "Customer.terms_code", "TERMS_MAP lookup"),
]


def render(rows: list[dict[str, Any]], trigger: str = "Deal closed-won", **kw: Any) -> str:
    return render_runnable(rows, name="Test integration", source_system="crm", target_system="billing",
                           trigger=trigger, **kw)


def test_loop1_and_loop2_render_readable_valid_code(demo_integrations) -> None:  # type: ignore[no-untyped-def]
    l1, l2 = demo_integrations.loop1, demo_integrations.loop2
    code1 = render([r.to_json() for r in l1.mappings], trigger=l1.trigger)
    code2 = render([r.to_json() for r in l2.mappings], trigger=l2.trigger)
    for code in (code1, code2):
        assert find_violations(code) == []
        assert code.count("def run(event, tools):") == 1
        assert "import" not in code
    assert "Trigger: crm.opportunity.closed_won" in code1
    assert '"currency_code": to_iso4217(acct["currency"]),  # m1-currency-code-customer | human edit' in code1
    assert '"jurisdiction": geocode(acct["shipping_address"])' in code1
    assert "Trigger: esign.agreement.completed" in code2
    assert '"total": plus_tax(fields["amount"], tax),' in code2
    assert 'first(signers, "Agreement.signers[].email")["email"]' in code2
    assert "rule rule-iso-currency" in code2
    assert 'SIGNED_STAGE = "Contract Signed"' in code2


def test_required_tools_match_call_order(demo_integrations) -> None:  # type: ignore[no-untyped-def]
    l1, l2 = demo_integrations.loop1, demo_integrations.loop2
    assert required_tools([r.to_json() for r in l1.mappings], trigger=l1.trigger) == [
        "crm.get_opportunity", "crm.get_account", "tax.calculate", "billing.create_customer",
    ]
    assert required_tools([r.to_json() for r in l2.mappings], trigger=l2.trigger) == [
        "esign.get_agreement_fields", "esign.get_signers", "crm.find_contact", "crm.find_opportunity",
        "crm.get_account", "crm.update_opportunity", "tax.calculate", "billing.create_customer",
        "billing.create_invoice",
    ]


def test_derived_operations_equal_demo_operations(demo_integrations) -> None:  # type: ignore[no-untyped-def]
    for integ in (demo_integrations.loop1, demo_integrations.loop2):
        assert sorted(derive_operations(integ.mappings)) == sorted(integ.operations)
        rows = [r.to_json() for r in integ.mappings]
        assert render(rows, trigger=integ.trigger) == render(rows, trigger=integ.trigger, operations=integ.operations)


def test_trigger_detection() -> None:
    assert detect_trigger("Deal closed-won", []) == CLOSED_WON
    assert detect_trigger("agreement.completed (new in v3.0)", []) == AGREEMENT_COMPLETED
    from app.demo.models import MappingRow

    row = MappingRow.model_validate(_row("x", "Agreement.fields.amount", "Invoice.total"))
    assert detect_trigger("whatever", [row]) == AGREEMENT_COMPLETED


def test_accepts_snake_case_rows_and_mapping_row_objects() -> None:
    from app.demo.models import MappingRow

    snake = [{**r, "rule_id": "rule-x"} for r in BASE]
    objs = [MappingRow.model_validate(r) for r in BASE]
    assert render(snake).count("rule rule-x") == 3
    assert render(objs) == render(BASE)


@pytest.mark.parametrize(
    "rows, message",
    [
        ([_row("bad", "Account.name", "Customer.legal_name", "shell('rm')")], "unknown transform"),
        ([_row("bad", "Account.name\"]); import os; x = ([\"", "Customer.legal_name")], "unsupported path segment"),
        ([_row("bad", "Account.__class__", "Customer.legal_name")], "unsupported path segment"),
        ([_row("bad", "Customer.id", "Customer.legal_name")], "not available"),
        ([_row("bad", "Agreement.fields.amount", "Invoice.total")], "needs billing.create_customer"),
        ([{"id": "x"}], "malformed"),
    ],
    ids=["transform", "code injection in path", "dunder path", "self reference", "invoice without customer",
         "malformed row"],
)
def test_unrenderable_rows_raise(rows: list[dict[str, Any]], message: str) -> None:
    with pytest.raises(CodegenError) as err:
        render(rows)
    assert message in err.value.message


def test_untrusted_text_cannot_break_out_of_strings_or_comments() -> None:
    rows = [
        _row("id\nimport os\n", "Account.name", "Customer.legal_name\nimport os",
             flag="Low confidence\n    import socket"),
        *BASE[1:],
    ]
    code = render(rows, trigger="Deal closed-won\nimport subprocess")
    assert find_violations(code) == []
    for line in code.splitlines():
        assert not line.lstrip().startswith("import"), line


def test_unknown_operation_rejected() -> None:
    with pytest.raises(CodegenError):
        render(BASE, operations=["crm.delete_everything"])


# ------------------------------------------------------------ prelude parity
def test_prelude_tables_match_demo() -> None:
    assert set(prelude.ISO_4217) == mocks.ISO_4217
    assert set(prelude.TERMS_CODES) == mocks.TERMS_CODES
    assert prelude.TERMS_MAP == mocks.TERMS_MAP
    assert prelude.CURRENCY_NAMES == mocks.CURRENCY_NAMES


@pytest.mark.parametrize("value", ["USD", "eur", " Euro ", "Pound Sterling", "yen", "Bitcoin", ""])
def test_prelude_to_iso4217_matches_demo(value: str) -> None:
    _assert_same(prelude.to_iso4217, mocks.to_iso4217, value)


@pytest.mark.parametrize("value", ["NET30", "net 30 days", "Due on receipt", "Net 15", "whenever"])
def test_prelude_terms_lookup_matches_demo(value: str) -> None:
    _assert_same(prelude.terms_lookup, mocks.terms_lookup, value)


@pytest.mark.parametrize(
    "value", ["100 Main St, Boston, MA, US", "Hauptstrasse 5, Berlin, DE", "10 King St, London, GB", "US", " , "]
)
def test_prelude_geocode_matches_demo(value: str) -> None:
    _assert_same(prelude.geocode, mocks.geocode, value)


def test_prelude_plus_tax_matches_demo_transform() -> None:
    from app.demo.runtime import apply_transform

    for amount, quote in [(100.0, {"tax": 19.0}), (120000.0, {"tax": 22800.0}), (10.005, None)]:
        ctx = {"TaxQuote": quote} if quote is not None else {}
        assert prelude.plus_tax(amount, quote) == apply_transform("+ tax", amount, ctx)


def _assert_same(ours: Any, theirs: Any, value: str) -> None:
    try:
        expected = theirs(value)
    except ValueError:
        with pytest.raises(ValueError):
            ours(value)
        return
    assert ours(value) == expected
