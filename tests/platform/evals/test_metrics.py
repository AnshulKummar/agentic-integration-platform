"""Metric math on hand-built examples."""

from __future__ import annotations

import pytest

from app.platform.evals.metrics import Counts, aggregate, bootstrap_ci, reliability, score_case

GOLD = [
    ("name", "acct_name", None),          # predicted right
    ("email", "billing_email", None),     # predicted wrong source
    ("city", "billing_city", None),       # abstained -> missing
    ("portal", None, None),               # predicted -> spurious
    ("template", None, None),             # abstained -> correct abstention
    ("total", "amount", "cents_to_decimal"),  # right source, wrong transform
]


@pytest.fixture
def scored(make_case, make_prediction):  # type: ignore[no-untyped-def]
    case = make_case(GOLD, sources=["acct_name", "billing_email", "billing_city", "amount", "old_email"],
                     deprecated=["old_email"])
    pred = make_prediction(case.id, [
        ("name", "acct_name", None, 0.95, "kg_reused"),
        ("email", "old_email", None, 0.6, "kg_inferred"),
        ("city", None, None, 0.3, "llm_new"),
        ("portal", "acct_name", None, 0.32, "llm_new"),
        ("template", None, None, 0.3, "llm_new"),
        ("total", "amount", None, 0.9, "kg_inferred"),
    ])
    return score_case(case, pred)


def test_outcome_classification(scored) -> None:  # type: ignore[no-untyped-def]
    outcomes = {o.target: o.outcome for o in scored.outcomes}
    assert outcomes == {"name": "correct", "email": "wrong", "city": "missing", "portal": "spurious",
                        "template": "correct_abstention", "total": "correct"}
    assert (scored.tp, scored.wrong, scored.missing, scored.spurious, scored.correct_abstention) == (2, 1, 1, 1, 1)
    assert scored.edits == 3  # wrong + missing + spurious
    assert scored.transform_edits == 1 and scored.transform_ok == 1
    assert scored.emitted == 4 and scored.reused == 1
    assert scored.picked_deprecated == 1
    assert not scored.exact


def test_precision_recall_f1(scored) -> None:  # type: ignore[no-untyped-def]
    c = Counts.of([scored])
    assert c.precision == pytest.approx(2 / 4)  # tp / (tp + wrong + spurious)
    assert c.recall == pytest.approx(2 / 4)  # tp / (tp + wrong + missing)
    assert c.f1 == pytest.approx(0.5)


def test_aggregate_fields(scored) -> None:  # type: ignore[no-untyped-def]
    m = aggregate([scored], seed=1, n_bootstrap=50)
    assert m["abstention_accuracy"] == 0.5
    assert m["transform_accuracy"] == 0.5
    assert m["reuse_rate"] == 0.25
    assert m["exact_case_accuracy"] == 0.0
    assert m["edits_incl_transforms_per_case"] == 4.0
    assert m["by_origin"]["kg_reused"] == {"emitted": 1, "correct": 1, "precision": 1.0}
    assert set(m["by_difficulty"]) == {"easy"}


def test_perfect_case_is_exact(make_case, make_prediction) -> None:  # type: ignore[no-untyped-def]
    case = make_case([("a", "x", None), ("b", None, None), ("c", "y", "enum_map")])
    pred = make_prediction(case.id, [("a", "x", None, 1.0, "kg_inferred"), ("b", None, None, 0.3, "llm_new"),
                                     ("c", "y", "enum_map", 0.8, "kg_reused")])
    s = score_case(case, pred)
    assert s.exact and s.edits == 0
    assert Counts.of([s]).f1 == 1.0


def test_missing_prediction_counts_as_abstention(make_case, make_prediction) -> None:  # type: ignore[no-untyped-def]
    case = make_case([("a", "x", None), ("b", None, None)])
    s = score_case(case, make_prediction(case.id, []))
    assert (s.missing, s.correct_abstention) == (1, 1)


def test_score_rejects_mismatched_ids(make_case, make_prediction) -> None:  # type: ignore[no-untyped-def]
    case = make_case([("a", "x", None)])
    with pytest.raises(ValueError):
        score_case(case, make_prediction("other", []))


def test_ece_and_reliability_table() -> None:
    # bin 0.9-1.0: conf 0.95 x2, 1 right -> |0.5 - 0.95| = 0.45 ; bin 0.2-0.3: conf 0.25 x2, 0 right -> 0.25
    ece, table = reliability([(0.95, True), (0.95, False), (0.25, False), (0.25, False)])
    assert ece == pytest.approx(0.5 * 0.45 + 0.5 * 0.25)
    assert len(table) == 10
    top = table[9]
    assert (top.count, top.mean_confidence, top.accuracy) == (2, 0.95, 0.5)
    assert table[2].count == 2 and table[2].accuracy == 0.0


def test_perfect_calibration_has_zero_ece() -> None:
    pairs = [(0.75, i < 3) for i in range(4)]  # 75% confident, 75% right
    ece, _ = reliability(pairs)
    assert ece == pytest.approx(0.0)
    assert reliability([(1.0, True)])[1][9].count == 1  # confidence 1.0 lands in the last bin


def test_bootstrap_ci_is_seeded_and_brackets(make_case, make_prediction) -> None:  # type: ignore[no-untyped-def]
    scores = []
    for i in range(10):
        case = make_case([("a", "x", None), ("b", "y", None)], case_id=f"c{i}")
        right = "y" if i % 2 else "z"
        rows = [("a", "x", None, 1, "o"), ("b", right, None, 1, "o")]
        scores.append(score_case(case, make_prediction(case.id, rows)))
    stat = lambda xs: Counts.of(xs).f1  # noqa: E731
    lo, hi = bootstrap_ci(scores, stat, seed=7, n=300)
    assert (lo, hi) == bootstrap_ci(scores, stat, seed=7, n=300)
    assert lo <= Counts.of(scores).f1 <= hi
    assert lo < hi
