"""Synthetic generator: determinism, validity, diversity."""

from __future__ import annotations

import re

from app.platform.evals.synthetic import CATEGORIES, generate_sequences, generate_suite


def _dump(cases):  # type: ignore[no-untyped-def]
    return [c.model_dump() for c in cases]


def test_same_seed_same_cases_different_seed_different_cases() -> None:
    assert _dump(generate_suite(42)) == _dump(generate_suite(42))
    assert _dump(generate_suite(42)) != _dump(generate_suite(43))
    assert _dump(generate_sequences(42, 2, 3)[0]) == _dump(generate_sequences(42, 2, 3)[0])


def test_suite_shape_and_validity() -> None:
    cases = generate_suite(7, variants=2)
    assert len(cases) == len(CATEGORIES) * 3 * 2
    assert len({c.id for c in cases}) == len(cases)
    assert {c.tags.difficulty for c in cases} == {"easy", "medium", "hard"}
    assert {c.tags.category for c in cases} == set(CATEGORIES)
    # EvalCase validation already guarantees gold sources exist; spot-check anyway.
    for c in cases:
        paths = {f.path for f in c.source_fields}
        assert all(g.source in paths for g in c.gold if g.source)


def test_diversity_of_naming_and_traps() -> None:
    cases = generate_suite(1234)
    names = [f.path for c in cases for f in c.source_fields + c.target_fields]
    assert any("_" in n for n in names)  # snake
    assert any(re.search(r"[a-z][A-Z]", n) for n in names)  # camel / Pascal
    assert any("." in n for n in names)  # nesting
    transforms = {g.transform for c in cases for g in c.gold}
    assert {"cents_to_decimal", "decimal_to_cents", "enum_map", "lookup"} <= transforms | {None}
    assert len(transforms - {None}) >= 6
    assert any(g.source is None for c in cases for g in c.gold)
    hard = [c for c in cases if c.tags.difficulty == "hard"]
    assert any(f.deprecated for c in hard for f in c.source_fields)
    easy = [c for c in cases if c.tags.difficulty == "easy"]
    assert all(g.source is not None for c in easy for g in c.gold)
    assert all(not f.deprecated for c in easy for f in c.source_fields)


def test_sequences_fix_the_hub() -> None:
    seqs = generate_sequences(9, n_sequences=4, length=3)
    assert len(seqs) == 4 and all(len(s) == 3 for s in seqs)
    for i, seq in enumerate(seqs):
        assert [c.step for c in seq] == [1, 2, 3]
        hub = {c.target.system for c in seq} if i % 2 == 0 else {c.source.system for c in seq}
        spokes = {c.source.system for c in seq} if i % 2 == 0 else {c.target.system for c in seq}
        assert len(hub) == 1 and len(spokes) == 3
