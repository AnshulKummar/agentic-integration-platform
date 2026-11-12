"""Dataset schema and the hand-labeled set's contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.platform.evals.dataset import (
    TRANSFORMS,
    DatasetError,
    dataset_stats,
    group_sequences,
    load_directory,
    load_handlabeled,
)


def test_handlabeled_meets_size_targets() -> None:
    stats = dataset_stats(load_handlabeled())
    assert stats["cases"] >= 15
    assert stats["gold_targets"] >= 150
    assert stats["gold_null"] > 0 and stats["with_transform"] > 0


def test_every_gold_source_exists_and_is_current() -> None:
    for case in load_handlabeled():
        sources = {f.path: f for f in case.source_fields}
        targets = {f.path for f in case.target_fields}
        assert {g.target for g in case.gold} == targets, case.id
        for g in case.gold:
            assert g.transform is None or g.transform in TRANSFORMS
            if g.source is not None:
                assert g.source in sources, (case.id, g.source)
                assert not sources[g.source].deprecated, (case.id, g.source)


def test_handlabeled_covers_required_pairs() -> None:
    kinds = {(c.source.kind, c.target.kind) for c in load_handlabeled()}
    for pair in [("crm", "billing"), ("crm", "esign"), ("esign", "billing"), ("crm", "tax")]:
        assert pair in kinds
    seqs = group_sequences(load_handlabeled())
    assert all([c.step for c in s] == sorted(c.step or 0 for c in s) for s in seqs.values())


def test_case_validation_rejects_bad_gold(make_case) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValidationError, match="not in source schema"):
        make_case([("a", "x", None)], sources=["y"])
    with pytest.raises(ValidationError, match="deprecated"):
        make_case([("a", "x", None)], sources=["x"], deprecated=["x"])
    with pytest.raises(ValidationError, match="unknown transform"):
        make_case([("a", "x", "magic")])
    with pytest.raises(ValidationError, match="null source cannot have a transform"):
        make_case([("a", None, "enum_map")])
    with pytest.raises(ValidationError, match="invalid field path"):
        make_case([("a b", "x", None)])
    with pytest.raises(ValidationError, match="sequence and step"):
        make_case([("a", "x", None)], sequence="s")


def test_target_without_gold_is_rejected(make_case) -> None:  # type: ignore[no-untyped-def]
    case = make_case([("a", "x", None)])
    data = case.model_dump(mode="json")
    data["target_fields"].append({"path": "b"})
    with pytest.raises(ValidationError, match="without gold"):
        type(case).model_validate(data)


def test_compact_files_resolve_schemas(tmp_path: Path) -> None:
    schemas = {
        "s1/A": {"system": "s1", "kind": "crm", "object": "A", "fields": [{"path": "x"}, {"path": "y"}]},
        "s2/B": {"system": "s2", "kind": "billing", "object": "B", "fields": [{"path": "p"}, {"path": "q"}]},
    }
    case = {"id": "k", "source": "s1/A", "target": "s2/B", "gold": [{"target": "p", "source": "x"}],
            "tags": {"difficulty": "easy", "category": "customer", "vendor_family": "a->b"}}
    (tmp_path / "_schemas.json").write_text(json.dumps(schemas))
    (tmp_path / "cases.json").write_text(json.dumps([case]))
    [loaded] = load_directory(tmp_path)
    assert [f.path for f in loaded.source_fields] == ["x", "y"]
    assert [f.path for f in loaded.target_fields] == ["p"]  # targets = gold targets

    case["gold"] = [{"target": "nope", "source": "x"}]
    (tmp_path / "cases.json").write_text(json.dumps([case]))
    with pytest.raises(DatasetError, match="not in schema"):
        load_directory(tmp_path)


def test_duplicate_ids_rejected(tmp_path: Path, make_case) -> None:  # type: ignore[no-untyped-def]
    raw = make_case([("a", "x", None)]).model_dump(mode="json")
    raw.pop("suite")
    (tmp_path / "one.json").write_text(json.dumps(raw))
    (tmp_path / "two.json").write_text(json.dumps([raw]))
    with pytest.raises(DatasetError, match="duplicate case ids"):
        load_directory(tmp_path)
