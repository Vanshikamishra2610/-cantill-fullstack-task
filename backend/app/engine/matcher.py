"""Item matching (policy 1.1 / 1.2).

Free-text descriptions are matched to the item master with an IDF-weighted
token overlap:

* text is normalised (``brg`` -> bearing, ``"`` -> inch, noise such as
  "urgent", "as per sample", "(repeat)" removed);
* each token is weighted by how rare it is in the item master, and tokens that
  carry a specification (they contain a digit, e.g. ``6308``, ``m10``,
  ``50x100``) count double;
* score = harmonic mean of "how much of the item is mentioned" and "how much of
  the request is explained by the item";
* a request that names a specification the item does *not* have (``6205`` vs
  ``6308``) is rejected for that item - wrong-size parts are worse than no match.

The matcher never invents items: below ``vague_score`` the line is a new item;
between ``vague_score`` and ``min_score``, or when two items score within
``ambiguity_margin``, the result is ambiguous and the requester must confirm.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from .types import Item

SYNONYMS = {
    "brg": "bearing",
    "brgs": "bearing",
    "bearings": "bearing",
    "hyd": "hydraulic",
    "pneu": "pneumatic",
    "pneum": "pneumatic",
    "cyl": "cylinder",
    "rod": "electrode",
    "electrodes": "electrode",
    "glove": "gloves",
    "helmets": "helmet",
    "shoe": "shoes",
    "boxes": "box",
    "bolts": "bolt",
    "nuts": "nut",
    "screws": "screw",
    "washers": "washer",
    "bags": "bag",
    "pallets": "pallet",
    "monitors": "monitor",
    "laptops": "laptop",
    "cartridges": "cartridge",
    "sheets": "sheet",
    "wheels": "wheel",
    "seals": "seal",
    "cylinders": "cylinder",
    "valves": "valve",
    "sensors": "sensor",
    "lights": "light",
    "earplugs": "ear plugs",
    "ss304": "ss 304",
    "aluminum": "aluminium",
    "alu": "aluminium",
    "qtrly": "quarterly",
    "calib": "calibration",
    "inches": "inch",
    "in.": "inch",
}

NOISE_PHRASES = [
    r"as per sample",
    r"as per drawing",
    r"\(repeat\)",
    r"\brepeat\b",
    r"\burgent(ly)?\b",
    r"\basap\b",
    r"\bfor maintenance\b",
    r"\bfor production\b",
    r"\brequired\b",
    r"\bplease\b",
]

STOPWORDS = {"for", "the", "a", "an", "of", "and", "with", "to", "pcs", "nos", "qty", "-", "type", "new", "need", "needed"}


def _is_spec(token: str) -> bool:
    return any(ch.isdigit() for ch in token)


def normalise(text: str) -> list[str]:
    t = (text or "").lower()
    t = t.replace('"', " inch ").replace("''", " inch ")
    for phrase in NOISE_PHRASES:
        t = re.sub(phrase, " ", t)
    t = re.sub(r"[(),;:]", " ", t)
    # split hyphenated specs ("6205-2rs" -> "6205 2rs") but keep 1/2, 3.15mm, 50x100
    t = re.sub(r"(?<=\w)-(?=\w)", " ", t)
    t = t.replace(" - ", " ")
    tokens: list[str] = []
    for raw in t.split():
        raw = raw.strip(".-")
        if not raw:
            continue
        mapped = SYNONYMS.get(raw, raw)
        for tok in mapped.split():
            if tok not in STOPWORDS:
                tokens.append(tok)
    return tokens


@dataclass
class MatchCandidate:
    item_code: str
    description: str
    score: float


@dataclass
class MatchResult:
    status: str  # matched | new | ambiguous
    item_code: str | None
    score: float
    method: str  # code | description | none
    candidates: list[MatchCandidate] = field(default_factory=list)

    @property
    def matched(self) -> bool:
        return self.status == "matched"


class ItemMatcher:
    def __init__(self, items: dict[str, Item]):
        self.items = items
        self._tokens = {code: set(normalise(it.description)) for code, it in items.items()}
        n = max(len(items), 1)
        df: dict[str, int] = {}
        for toks in self._tokens.values():
            for tok in toks:
                df[tok] = df.get(tok, 0) + 1
        self._idf = {tok: math.log(1 + n / c) for tok, c in df.items()}
        self._unknown_idf = math.log(1 + n)

    def _weight(self, tok: str) -> float:
        w = self._idf.get(tok, self._unknown_idf)
        return w * 2 if _is_spec(tok) else w

    def score(self, query_tokens: set[str], item_code: str) -> float:
        item_tokens = self._tokens[item_code]
        if not query_tokens or not item_tokens:
            return 0.0
        common = query_tokens & item_tokens
        if not any(not _is_spec(t) for t in common):
            return 0.0  # sharing only a number ("100") is not evidence of the same item
        # conflicting specification: the request names a size/model the item does not have
        query_specs = {t for t in query_tokens if _is_spec(t)}
        item_specs = {t for t in item_tokens if _is_spec(t)}
        if query_specs - item_specs and item_specs - query_specs:
            return 0.0
        common_w = sum(self._weight(t) for t in common)
        recall = common_w / sum(self._weight(t) for t in item_tokens)
        precision = common_w / sum(self._weight(t) for t in query_tokens)
        return 2 * recall * precision / (recall + precision)

    def rank(self, description: str, limit: int = 5) -> list[MatchCandidate]:
        q = set(normalise(description))
        scored = [
            MatchCandidate(code, self.items[code].description, round(self.score(q, code), 4))
            for code in self.items
        ]
        scored = [c for c in scored if c.score > 0]
        scored.sort(key=lambda c: (-c.score, c.item_code))
        return scored[:limit]

    def match(
        self,
        item_code: str | None,
        description: str,
        min_score: float = 0.6,
        ambiguity_margin: float = 0.05,
        vague_score: float = 0.4,
    ) -> MatchResult:
        code = (item_code or "").strip().upper()
        if code and code in self.items:
            return MatchResult("matched", code, 1.0, "code")
        candidates = self.rank(description)
        if not candidates or candidates[0].score < vague_score:
            return MatchResult("new", None, candidates[0].score if candidates else 0.0, "none", candidates)
        if candidates[0].score < min_score:
            # resembles stocked items but not clearly one of them ("gloves"): ask, don't guess
            return MatchResult("ambiguous", None, candidates[0].score, "description",
                               [c for c in candidates if c.score >= vague_score][:3])
        best = candidates[0]
        close = [c for c in candidates[1:] if best.score - c.score < ambiguity_margin]
        if close:
            return MatchResult("ambiguous", None, best.score, "description", [best, *close])
        return MatchResult("matched", best.item_code, best.score, "description", candidates)
