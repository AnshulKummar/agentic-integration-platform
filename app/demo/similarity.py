"""Deterministic name/token similarity used by the schema aligner and mapping drafter.

Field labels look like ``Owner.leaf`` or ``Owner.path.leaf`` (``Agreement.fields.amount``,
``Agreement.signers[].email``). Similarity combines the leaf name (with a small concept
table) and the owner object (with a small synonym table).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Business-object synonyms across systems (symmetric), strength 0..1.
OBJECT_SYNONYMS: dict[frozenset[str], float] = {
    frozenset({"agreement", "contract"}): 0.92,
    frozenset({"envelope", "contract"}): 0.75,
    frozenset({"account", "customer"}): 0.85,
    frozenset({"signer", "contact"}): 0.8,
    frozenset({"recipient", "contact"}): 0.8,
    frozenset({"fields", "opportunity"}): 0.7,  # signed form data of a deal
}

# Word-level synonyms used for events/processes (agreement.completed ~ Contract signed).
WORD_SYNONYMS: dict[frozenset[str], float] = {
    frozenset({"agreement", "contract"}): 0.92,
    frozenset({"completed", "signed"}): 0.9,
    frozenset({"closed", "won"}): 0.6,
}

# Leaf-field concepts: names in the same concept are near-synonyms.
FIELD_CONCEPTS: dict[str, set[str]] = {
    "name": {"name", "legal_name", "display_name", "company_name"},
    "address": {"address", "billing_address", "shipping_address", "postal_address"},
    "currency": {"currency", "currency_code"},
    "terms": {"payment_terms", "terms_code", "terms"},
    "amount": {"amount", "total", "value"},
    "email": {"email", "signer_email"},
    "jurisdiction": {"jurisdiction", "tax_region"},
}

# Cross-concept relations (a jurisdiction is derived from an address).
CONCEPT_RELATIONS: dict[frozenset[str], float] = {
    frozenset({"jurisdiction", "address"}): 0.7,
}

CONCEPT_MATCH = 0.9


@dataclass(frozen=True)
class FieldRef:
    owner: str  # lower-cased owning object, e.g. "agreement"
    path: tuple[str, ...]  # intermediate segments, e.g. ("signers",)
    leaf: str  # e.g. "email"

    @property
    def effective_owner(self) -> str:
        """Nested collections behave like their own object: signers[].email -> signer."""
        if self.path:
            seg = self.path[-1]
            return seg[:-1] if seg.endswith("s") and seg != "fields" else seg
        return self.owner


def parse_field(label: str) -> FieldRef:
    parts = [p.replace("[]", "") for p in label.split(".")]
    if len(parts) == 1:
        return FieldRef(owner="", path=(), leaf=parts[0].lower())
    return FieldRef(owner=parts[0].lower(), path=tuple(p.lower() for p in parts[1:-1]), leaf=parts[-1].lower())


def split_words(text: str) -> list[str]:
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    return [w for w in re.split(r"[^a-zA-Z0-9]+", text.lower()) if w]


def concept_of(leaf: str) -> str | None:
    for concept, names in FIELD_CONCEPTS.items():
        if leaf in names:
            return concept
    return None


def leaf_similarity(a: str, b: str) -> float:
    if a == b:
        return 1.0
    ca, cb = concept_of(a), concept_of(b)
    if ca is not None and ca == cb:
        return CONCEPT_MATCH
    if ca is not None and cb is not None:
        rel = CONCEPT_RELATIONS.get(frozenset({ca, cb}))
        if rel is not None:
            return rel
    wa, wb = set(a.split("_")), set(b.split("_"))
    return len(wa & wb) / len(wa | wb) if wa | wb else 0.0


def object_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return OBJECT_SYNONYMS.get(frozenset({a, b}), 0.0)


def word_similarity(a: str, b: str) -> float:
    if a == b:
        return 1.0
    return WORD_SYNONYMS.get(frozenset({a, b}), 0.0)


def field_similarity(source_label: str, target_label: str) -> float:
    """Similarity of two field labels in 0..1."""
    s, t = parse_field(source_label), parse_field(target_label)
    leaf = leaf_similarity(s.leaf, t.leaf)
    owner = max(
        object_similarity(s.effective_owner, t.effective_owner),
        object_similarity(s.owner, t.owner),
    )
    return round(max(0.6 * leaf + 0.4 * owner, 0.85 * leaf), 4)


def phrase_similarity(a: str, b: str) -> float:
    """Average best word match of phrase ``a`` against phrase ``b`` (0..1)."""
    wa, wb = split_words(a), split_words(b)
    if not wa or not wb:
        return 0.0
    best = [max(word_similarity(x, y) for y in wb) for x in wa]
    return round(sum(best) / len(best), 4)


STOPWORDS = {
    "the", "a", "an", "and", "or", "to", "of", "for", "in", "on", "with", "without", "their", "our", "every",
    "should", "be", "is", "are", "from", "by", "own", "not", "need", "when", "via", "no", "new", "into",
    "become", "automatically", "it", "that", "this", "as", "we", "us", "can", "let",
}


def stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def content_words(text: str) -> set[str]:
    return {stem(w) for w in split_words(text) if w not in STOPWORDS and len(w) > 1}


def text_overlap(text: str, vocabulary: set[str]) -> float:
    words = content_words(text)
    if not words:
        return 0.0
    return len(words & vocabulary) / len(words)
