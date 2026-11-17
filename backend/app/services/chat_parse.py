"""Rule-based understanding of requester chat messages.

Pulls quantity, unit, plant, need-by date, urgency and price out of free text
such as "need 200 nitrile gloves for Chakan by next Friday". What is left is
the item description, which goes to the item matcher. The optional LLM layer
(see chat.py) fills the same slots; this parser is the always-available base.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta

UNITS = {
    "nos": "NOS", "no": "NOS", "pcs": "NOS", "pc": "NOS", "pieces": "NOS", "piece": "NOS", "units": "NOS", "unit": "NOS",
    "kg": "KG", "kgs": "KG", "kilo": "KG", "kilos": "KG",
    "ltr": "LTR", "ltrs": "LTR", "litre": "LTR", "litres": "LTR", "liter": "LTR", "liters": "LTR", "l": "LTR",
    "mtr": "MTR", "mtrs": "MTR", "meter": "MTR", "meters": "MTR", "metre": "MTR", "metres": "MTR", "m": "MTR",
    "pair": "PAIR", "pairs": "PAIR", "set": "SET", "sets": "SET", "roll": "ROL", "rolls": "ROL", "rol": "ROL",
    "cyl": "CYL", "cylinders": "CYL", "ream": "REAM", "reams": "REAM", "bkt": "BKT", "bucket": "BKT", "buckets": "BKT",
    "brl": "BRL", "barrel": "BRL", "barrels": "BRL", "job": "JOB", "jobs": "JOB", "month": "MON", "months": "MON",
    "mon": "MON", "qtr": "QTR", "quarter": "QTR", "quarters": "QTR", "box": "NOS", "boxes": "NOS",
}
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
URGENT_WORDS = re.compile(r"\b(emergency|machine breakdown|breakdown|line down|machine down|urgent(ly)?|asap|immediately|critical)\b", re.I)
NORMAL_WORDS = re.compile(r"\b(normal|not urgent|no rush|routine|regular)\b", re.I)
YES = re.compile(r"^\s*(y|yes|yeah|yep|ok|okay|confirm|confirmed|correct|right|sure|go ahead|create|submit|looks good|done)\b", re.I)
NO = re.compile(r"^\s*(n|no|nope|not really|wrong|incorrect|change|edit)\b", re.I)
# a number followed by one of these is a specification ("24 inch", "5 ton"), not a quantity
SPEC_AFTER = r"(?![\w./x\"'-])(?!\s*(?:inch|in|mm|cm|ton|tonne|hp|kw|w|a|v|micron|gsm|sqmm|core|pole|ply|lph|cum|t|x)\b)"
FILLER = re.compile(
    r"\b(i|we|need|needs|needed|want|wanted|require|required|requirement|request|please|pls|kindly|order|buy|"
    r"get|send|some|of|for|the|a|an|by|before|on|at|to|plant|deliver(ed)?|delivery|qty|quantity|raise|pr|"
    r"approx(imately)?|around|about|each|per|unit|price|estimated?|cost|rs|inr|hi|hello|hey|can|you|me|us)\b",
    re.I,
)


@dataclass
class Parsed:
    quantity: float | None = None
    uom: str | None = None
    plant: str | None = None
    need_by: date | None = None
    date_text: str | None = None
    urgency: str | None = None  # Emergency | Normal | None
    urgency_hint: bool = False  # said "urgent" etc. - needs confirmation
    price: float | None = None
    item_code: str | None = None
    description: str = ""
    yes: bool = False
    no: bool = False
    choice: int | None = None
    spans: list[tuple[int, int]] = field(default_factory=list)


def _weekday_after(today: date, weekday: int, skip_week: bool = False) -> date:
    days = (weekday - today.weekday()) % 7 or 7
    return today + timedelta(days=days + (7 if skip_week else 0))


def parse_date(text: str, today: date) -> tuple[date | None, tuple[int, int] | None]:
    t = text.lower()
    patterns: list[tuple[str, callable]] = [
        (r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", lambda m: date(int(m[1]), int(m[2]), int(m[3]))),
        (r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})\b",
         lambda m: date(int(m[3]) + (2000 if len(m[3]) == 2 else 0), int(m[2]), int(m[1]))),
        (r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?(?:,?\s+(\d{4}))?\b",
         lambda m: _roll(date(int(m[3]) if m[3] else today.year, MONTHS[m[2]], int(m[1])), today, bool(m[3]))),
        (r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?\b",
         lambda m: _roll(date(int(m[3]) if m[3] else today.year, MONTHS[m[1]], int(m[2])), today, bool(m[3]))),
        (r"\bday after tomorrow\b", lambda m: today + timedelta(days=2)),
        (r"\btomorrow\b", lambda m: today + timedelta(days=1)),
        (r"\btoday\b", lambda m: today),
        (r"\bin\s+(\d+|a|one|two|three|four)\s+(day|week|month)s?\b", lambda m: _in(today, m[1], m[2])),
        (r"\bnext week\b", lambda m: _weekday_after(today, 0)),
        (r"\b(end of (the )?week|this weekend)\b", lambda m: _weekday_after(today, 4) if today.weekday() < 4 else today),
        (r"\bend of (the )?month\b", lambda m: (today.replace(day=1) + relativedelta(months=1)) - timedelta(days=1)),
        (r"\bnext month\b", lambda m: today.replace(day=1) + relativedelta(months=1)),
        (r"\b(?:next|this|coming)?\s*(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
         lambda m: _weekday_after(today, WEEKDAYS.index(m[1]))),
    ]
    for pattern, build in patterns:
        m = re.search(pattern, t)
        if m:
            try:
                return build(m), m.span()
            except ValueError:
                return None, m.span()
    return None, None


def _roll(d: date, today: date, explicit_year: bool) -> date:
    return d if explicit_year or d >= today else d.replace(year=d.year + 1)


def _in(today: date, n: str, unit: str) -> date:
    count = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4}.get(n, None) or int(n)
    if unit == "day":
        return today + timedelta(days=count)
    if unit == "week":
        return today + timedelta(weeks=count)
    return today + relativedelta(months=count)


def parse_plant(text: str, plants: dict[str, str]) -> tuple[str | None, tuple[int, int] | None]:
    aliases = {"chakan": "CHK1", "chk1": "CHK1", "chk": "CHK1", "bhosari": "PUN1", "pune": "PUN1", "pun1": "PUN1",
               "pun": "PUN1"}
    for code, name in plants.items():
        aliases[code.lower()] = code
        for word in re.findall(r"[a-z]+", name.lower()):
            if word not in {"plant", "pune"} and len(word) > 3:
                aliases[word] = code
    for alias, code in sorted(aliases.items(), key=lambda kv: -len(kv[0])):
        m = re.search(rf"\b{re.escape(alias)}\b", text, re.I)
        if m and code in plants:
            return code, m.span()
    return None, None


def parse_message(text: str, today: date, plants: dict[str, str], expecting: str | None = None) -> Parsed:
    p = Parsed()
    t = text.strip()
    p.yes, p.no = bool(YES.match(t)), bool(NO.match(t)) and not YES.match(t)
    if m := re.fullmatch(r"\s*(?:option\s*)?#?(\d)\s*", t):
        p.choice = int(m[1])
    if m := re.search(r"\bITM-\d{4}\b", t, re.I):
        p.item_code = m[0].upper()
        p.spans.append(m.span())

    if m := re.search(r"(?<!\w)(?:rs\.?|inr|₹|@|price|estimated?|approx\.?|cost)\s*:?\s*([\d,]+(?:\.\d+)?)", t, re.I):
        p.price = float(m[1].replace(",", ""))
        p.spans.append(m.span())

    p.need_by, span = parse_date(t, today)
    if span:
        p.date_text = t[span[0]:span[1]]
        p.spans.append(span)
    p.plant, span = parse_plant(t, plants)
    if span:
        p.spans.append(span)

    if m := URGENT_WORDS.search(t):
        p.urgency_hint = True
        if m[1].lower() in ("emergency", "machine breakdown", "breakdown", "line down", "machine down"):
            p.urgency = "Emergency"
        p.spans.append(m.span())
    elif NORMAL_WORDS.search(t) or (expecting == "urgency" and p.no):
        p.urgency = "Normal"

    _parse_quantity(t, p, expecting)
    p.description = _remainder(t, p.spans)
    return p


def _parse_quantity(t: str, p: Parsed, expecting: str | None) -> None:
    unit_alt = "|".join(sorted(map(re.escape, UNITS), key=len, reverse=True))
    taken = lambda s: any(a <= s[0] < b for a, b in p.spans)  # noqa: E731
    # 1) number, space, unit word: "200 nos", "50 kg" (but not specs such as "18kg" or "210L")
    for m in re.finditer(rf"(?<![\w./-])(\d[\d,]*(?:\.\d+)?)\s+({unit_alt})\b", t, re.I):
        if not taken(m.span()):
            p.quantity, p.uom = float(m[1].replace(",", "")), UNITS[m[2].lower()]
            p.spans.append(m.span())
            return
    # 2) number right after "need / qty / order" or at the very start: "need 200 gloves"
    m = re.search(
        rf"(?:^|\b(?:need|want|order|qty|quantity|require|buy|get)\s*:?\s*)(\d[\d,]*(?:\.\d+)?){SPEC_AFTER}", t, re.I
    )
    if m and not taken(m.span(1)):
        p.quantity = float(m[1].replace(",", ""))
        p.spans.append(m.span(1))
        return
    # 3) "monitor x 3", "qty: 3"
    m = re.search(r"(?:\bx|\bqty\s*:?)\s*(\d[\d,]*)(?![\w./-])", t, re.I)
    if m and not taken(m.span(1)):
        p.quantity = float(m[1].replace(",", ""))
        p.spans.append(m.span())
        return
    # 4) the answer to "how many?" is a bare number
    if expecting == "quantity":
        m = re.search(rf"(?<![\w./-])(\d[\d,]*(?:\.\d+)?){SPEC_AFTER}", t)
        if m and not taken(m.span()):
            p.quantity = float(m[1].replace(",", ""))
            p.spans.append(m.span())


def _remainder(t: str, spans: list[tuple[int, int]]) -> str:
    chars = list(t)
    for a, b in spans:
        for i in range(a, b):
            chars[i] = " "
    rest = FILLER.sub(" ", "".join(chars))
    rest = re.sub(r"[,;!?]+", " ", rest)
    return re.sub(r"\s+", " ", rest).strip(" .-")
