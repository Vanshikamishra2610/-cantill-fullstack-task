"""Chat intake: a requester describes a need, the assistant turns it into a PR.

Slot filling with a fixed order of follow-ups: item -> quantity -> plant ->
need-by date -> urgency -> (estimate, for new items) -> confirm.

Item identification is always done by the deterministic matcher against the
item master, so the assistant can only offer items that exist. When it is not
sure it asks the requester to choose; when nothing fits it says so and offers
to raise the line as a new item (policy 1.2) instead of guessing.
"""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..engine.matcher import ItemMatcher
from ..engine.pipeline import fmt_inr
from ..engine.types import MasterData
from ..models import ChatSessionRow, PurchaseRequestRow, utcnow
from .chat_parse import Parsed, parse_message
from .llm import LLMUnavailable, extract_json, llm_available
from .master import audit, load_master_data
from .runs import pr_row_to_line, run_engine

AUTO_MATCH = 0.75  # at or above: matched without asking (unless ambiguous)
SUGGEST_MATCH = 0.4  # at or above: offered as "did you mean"

LLM_SCHEMA = {
    "type": "object",
    "properties": {
        "item_text": {"type": ["string", "null"], "description": "what is being requested, without quantity/plant/date words"},
        "quantity": {"type": ["number", "null"]},
        "uom": {"type": ["string", "null"]},
        "plant": {"type": ["string", "null"], "enum": ["PUN1", "CHK1", None]},
        "need_by_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
        "urgency": {"type": ["string", "null"], "enum": ["Emergency", "Normal", None]},
        "estimated_unit_price": {"type": ["number", "null"]},
        "intent": {"type": "string", "enum": ["inform", "yes", "no", "cancel", "other"]},
    },
    "required": ["item_text", "quantity", "uom", "plant", "need_by_date", "urgency", "estimated_unit_price", "intent"],
    "additionalProperties": False,
}

LLM_SYSTEM = (
    "You read one message from a factory employee raising a purchase request and extract the fields they stated. "
    "Plants: PUN1 = Pune Bhosari plant, CHK1 = Chakan plant. Only fill a field the message states or clearly answers; "
    "otherwise null. Never invent item codes. 'urgent' alone is not Emergency - use Emergency only for breakdowns, "
    "line stoppages or when the user explicitly says emergency. intent=yes/no when the message answers a yes/no question."
)


ADD_LINE = re.compile(r"\b(add|another|one more|more)\b", re.I)
# words that talk *about* the request rather than naming an item ("add one item", "something")
NON_ITEM_WORDS = {"add", "another", "one", "more", "item", "items", "line", "thing", "things", "something",
                  "stuff", "new", "request", "also", "and", "too", "please", "help", "buy", "purchase"}


def _item_words(text: str) -> str:
    """The part of ``text`` that could name an item; empty if it is only filler like "add one item"."""
    words = [w for w in re.findall(r"[\w./\"-]+", (text or "").lower()) if w not in NON_ITEM_WORDS]
    rest = " ".join(words)
    return rest if len(rest) > 2 else ""


def _empty_state(user: str, department: str) -> dict[str, Any]:
    return {
        "stage": "collecting", "pending": None, "options": [],
        "item_code": None, "item_description": None, "item_status": None, "item_text": None, "uom": None,
        "quantity": None, "plant": None, "need_by": None, "urgency": None, "urgency_hint": False,
        "estimate": None, "requester": user, "department": department, "pr_no": None, "line_no": 1,
    }


class ChatService:
    def __init__(self, db: Session):
        self.db = db
        self.md: MasterData = load_master_data(db)
        self.matcher = ItemMatcher(self.md.items)
        self.today = date.today()

    # ------------------------------------------------------------------ API
    def handle(self, session_id: str | None, message: str, option: str | None, user: str, department: str) -> dict[str, Any]:
        session = self.db.get(ChatSessionRow, session_id) if session_id else None
        if session is None:
            session = ChatSessionRow(id=uuid.uuid4().hex, user=user, state=_empty_state(user, department), messages=[])
            self.db.add(session)
        state = dict(session.state)
        state["department"] = department or state.get("department", "")
        reply = self._turn(state, (message or "").strip(), option)
        session.state = state
        session.messages = [*session.messages, {"role": "user", "text": message, "at": utcnow().isoformat()},
                            {"role": "assistant", "text": reply["text"], "at": utcnow().isoformat()}]
        session.updated_at = utcnow()
        self.db.commit()
        return {"session_id": session.id, **reply, "draft": self._draft(state), "stage": state["stage"]}

    # ---------------------------------------------------------------- turns
    def _turn(self, state: dict[str, Any], message: str, option: str | None) -> dict[str, Any]:
        low = message.lower()
        if option == "restart" or low in {"cancel", "start over", "reset", "restart"}:
            keep = {k: state[k] for k in ("requester", "department")}
            state.clear()
            state.update(_empty_state(keep["requester"], keep["department"]))
            return self._say("No problem, let's start again. What do you need?")
        if state["stage"] == "done":
            if option == "add_line" or (ADD_LINE.search(low) and not _item_words(message)):
                self._reset_item(state)
                state["stage"], state["line_no"] = "collecting", state["line_no"] + 1
                return self._say(f"Adding line {state['line_no']} to {state['pr_no']}. What else do you need?")
            # anything else after a finished PR starts a fresh request
            keep = {k: state[k] for k in ("requester", "department")}
            state.clear()
            state.update(_empty_state(keep["requester"], keep["department"]))
            if option == "new_pr" or not message:
                return self._say("Sure. What do you need?")
        if low.startswith("change item") or low.startswith("wrong item"):
            state.update(item_code=None, item_description=None, item_status=None, item_text=None, uom=None)
            state["pending"] = "item"
            return self._say("OK, which item do you need instead?")

        pending = state.get("pending")
        parsed = self._understand(message, pending)

        # ---- answers to the pending question ---------------------------
        if pending == "item_choice":
            chosen = self._resolve_choice(option, parsed, state)
            if chosen == "new":
                state.update(item_status="new", item_code=None, item_description=state["item_text"])
            elif chosen == "rephrase":
                state.update(item_status=None, item_text=None)
                state["pending"] = "item"
                return self._say("OK. Describe the item in a bit more detail (type, size, make) and I'll search again.")
            elif chosen:
                self._set_item(state, chosen)
            elif _item_words(parsed.description) and not parsed.yes and not parsed.no:
                state.update(item_status=None, item_text=parsed.description)  # treat as a new description
        elif pending == "confirm":
            if parsed.yes or option == "confirm":
                return self._create_pr(state)
            if parsed.no and not self._has_slots(parsed):
                return self._say("What should I change? You can say e.g. \"quantity 250\", \"plant Chakan\" or "
                                 "\"need by 30 Nov\".", options=[{"label": "Start over", "value": "restart"}])
        elif pending == "urgency" and (parsed.yes or parsed.no) and parsed.urgency is None:
            parsed.urgency = "Emergency" if parsed.yes else "Normal"
        elif pending == "urgency_confirm":
            state["urgency"] = "Emergency" if (parsed.yes or option == "emergency") else "Normal"
            state["urgency_hint"] = False
        elif pending == "estimate" and parsed.price is None and parsed.quantity is not None:
            parsed.price, parsed.quantity = parsed.quantity, None  # "1200" answers "what does it cost?"

        notes = self._apply(state, parsed, pending)
        return self._next(state, notes)

    # ------------------------------------------------------------ understanding
    def _understand(self, message: str, pending: str | None) -> Parsed:
        expecting = {"quantity": "quantity", "estimate": "quantity", "urgency": "urgency"}.get(pending or "")
        parsed = parse_message(message, self.today, self.md.plants, expecting)
        if llm_available() and len(message.split()) > 2:
            try:
                data = extract_json(
                    LLM_SYSTEM,
                    f"Today is {self.today.isoformat()} ({self.today.strftime('%A')}). "
                    f"The assistant last asked about: {pending or 'what they need'}.\nMessage: {message}",
                    LLM_SCHEMA, effort="low", max_tokens=2000,
                )
                self._merge_llm(parsed, data)
            except (LLMUnavailable, ValueError):
                pass  # rule-based parse is already in place
        return parsed

    @staticmethod
    def _merge_llm(parsed: Parsed, data: dict[str, Any]) -> None:
        if data.get("item_text"):
            parsed.description = data["item_text"]
        if data.get("quantity") is not None:
            parsed.quantity = float(data["quantity"])
        if data.get("uom"):
            parsed.uom = str(data["uom"]).upper()
        if data.get("plant"):
            parsed.plant = data["plant"]
        if data.get("need_by_date"):
            try:
                parsed.need_by = date.fromisoformat(data["need_by_date"])
            except ValueError:
                pass
        # Models tend to default an unstated urgency to "Normal"; urgency changes the
        # decision, so only an explicit emergency is taken - otherwise we ask.
        if data.get("urgency") == "Emergency" and parsed.urgency is None:
            parsed.urgency = "Emergency"
        if data.get("estimated_unit_price") is not None:
            parsed.price = float(data["estimated_unit_price"])
        parsed.yes = parsed.yes or data.get("intent") == "yes"
        parsed.no = parsed.no or data.get("intent") == "no"

    @staticmethod
    def _has_slots(p: Parsed) -> bool:
        return any([p.quantity, p.plant, p.need_by, p.price, p.urgency, p.item_code])

    def _resolve_choice(self, option: str | None, parsed: Parsed, state: dict[str, Any]) -> str | None:
        if option in ("new", "rephrase"):
            return option
        if option and option.startswith("item:"):
            return option[5:]
        if parsed.item_code and parsed.item_code in self.md.items:
            return parsed.item_code
        codes = [o["value"][5:] for o in state.get("options", []) if o["value"].startswith("item:")]
        if parsed.choice and 1 <= parsed.choice <= len(codes):
            parsed.quantity = None  # "2" picked option 2, it is not a quantity
            return codes[parsed.choice - 1]
        if parsed.yes and len(codes) == 1:
            return codes[0]  # "yes" to "do you mean X?"
        # a bare "no" means "none of these"; "no, routine" is answering something else
        if (parsed.no and not self._has_slots(parsed)) or "new" in parsed.description.lower().split():
            return "new"
        return None

    # ------------------------------------------------------------- applying
    def _apply(self, state: dict[str, Any], p: Parsed, pending: str | None) -> list[str]:
        notes: list[str] = []
        describes_item = (
            _item_words(p.description) and not p.yes and not p.no
            and pending not in ("confirm", "urgency_confirm", "urgency", "estimate", "plant", "need_by", "quantity")
        )
        if p.item_code and p.item_code in self.md.items and state["item_code"] != p.item_code:
            self._set_item(state, p.item_code)
        elif describes_item and state["item_status"] is None:
            state["item_text"] = p.description
        if p.quantity is not None:
            if p.quantity <= 0:
                notes.append("The quantity has to be more than zero.")
            else:
                state["quantity"] = p.quantity
        if p.uom and state["item_status"] == "new":
            state["uom"] = p.uom
        elif p.uom and state.get("uom") and p.uom != state["uom"]:
            notes.append(f"Note: this item is stocked in {state['uom']}, so I've recorded the quantity in {state['uom']}.")
        if p.plant:
            state["plant"] = p.plant
        if p.need_by:
            if p.need_by < self.today:
                notes.append(f"{p.need_by.strftime('%d %b %Y')} is in the past, so I haven't used it.")
            else:
                state["need_by"] = p.need_by.isoformat()
        elif pending == "need_by" and not p.need_by and not self._has_slots(p):
            notes.append("Sorry, I couldn't read that as a date. Try \"30 Nov\", \"next Friday\" or \"in 2 weeks\".")
        if p.urgency:
            state["urgency"], state["urgency_hint"] = p.urgency, False
        elif p.urgency_hint and state.get("urgency") is None:
            state["urgency_hint"] = True
        if p.price is not None:
            state["estimate"] = p.price
        return notes

    def _set_item(self, state: dict[str, Any], code: str) -> None:
        item = self.md.items[code]
        state.update(item_code=code, item_description=item.description, item_status="matched", uom=item.uom)

    def _reset_item(self, state: dict[str, Any]) -> None:
        state.update(item_code=None, item_description=None, item_status=None, item_text=None, uom=None,
                     quantity=None, estimate=None, urgency=None, urgency_hint=False, pending=None, options=[])

    # --------------------------------------------------------------- next step
    def _next(self, state: dict[str, Any], notes: list[str]) -> dict[str, Any]:
        prefix = " ".join(notes) + ("\n\n" if notes else "")

        if state["item_status"] is None:
            if not state.get("item_text"):
                state["pending"] = "item"
                return self._say(prefix + "What do you need? Describe the item, e.g. \"200 nitrile gloves for Chakan by next Friday\".")
            found = self._identify(state)
            if found:
                return self._say(prefix + found["text"], options=found.get("options", []))
            prefix += f"Matched to **{state['item_code']} - {state['item_description']}** ({state['uom']}). "

        if state["quantity"] is None:
            state["pending"] = "quantity"
            unit = state.get("uom") or "units"
            return self._say(prefix + f"How many do you need (in {unit})?")
        if not state["plant"]:
            state["pending"] = "plant"
            return self._say(prefix + "Which plant is it for?",
                             options=[{"label": f"{c} - {n}", "value": c} for c, n in self.md.plants.items()])
        if not state["need_by"]:
            state["pending"] = "need_by"
            return self._say(prefix + "When do you need it by?")
        if state.get("urgency_hint") and state.get("urgency") is None:
            state["pending"] = "urgency_confirm"
            return self._say(
                prefix + "You mentioned this is urgent. Should I mark it as an **Emergency**? Emergency is for breakdowns "
                "and line stoppages: Procurement may then order without quotes (up to the emergency limit).",
                options=[{"label": "Yes, it's an emergency", "value": "emergency"}, {"label": "No, normal priority", "value": "normal"}],
            )
        if state.get("urgency") is None:
            state["pending"] = "urgency"
            return self._say(prefix + "Is this an emergency (breakdown / line stoppage), or normal priority?",
                             options=[{"label": "Normal", "value": "normal"}, {"label": "Emergency", "value": "emergency"}])
        if state["item_status"] == "new" and state.get("estimate") is None:
            state["pending"] = "estimate"
            return self._say(prefix + "Since this is a new item, what is your estimated price per unit (INR, excl. GST)? "
                             "Procurement uses it to size the sourcing request.")
        if state["item_status"] == "new" and not state.get("uom"):
            state["uom"] = "NOS"

        state["pending"] = "confirm"
        return self._say(prefix + "Please check the request below. Shall I create the PR?",
                         options=[{"label": "Yes, create PR", "value": "confirm"}, {"label": "Start over", "value": "restart"}])

    def _identify(self, state: dict[str, Any]) -> dict[str, Any] | None:
        """Match the item text. Returns a question when the requester must choose, None when matched."""
        text = state["item_text"]
        candidates = self.matcher.rank(text, limit=4)
        best = candidates[0] if candidates else None
        close = [c for c in candidates[1:] if best and best.score - c.score < 0.05]
        if best and best.score >= AUTO_MATCH and not close:
            self._set_item(state, best.item_code)
            return None
        offered = [c for c in candidates if c.score >= SUGGEST_MATCH][:3]
        state["pending"] = "item_choice"
        if offered:
            state["options"] = [{"label": f"{c.item_code} - {c.description}", "value": f"item:{c.item_code}"} for c in offered]
            state["options"] += [{"label": "None of these (new item)", "value": "new"}, {"label": "Let me rephrase", "value": "rephrase"}]
            if len(offered) > 1:
                question = f"I found more than one possible item for \"{text}\". Which one is it?"
            else:
                question = (f"For \"{text}\", do you mean **{offered[0].item_code} - {offered[0].description}**? "
                            "Say yes, or pick another option.")
            return {"text": question, "options": state["options"]}
        state["options"] = [{"label": "Yes, raise it as a new item", "value": "new"}, {"label": "Let me rephrase", "value": "rephrase"}]
        return {
            "text": f"I couldn't find \"{text}\" in the item master, and I won't guess. Is this a new item? "
                    "New items go to sourcing and Procurement finds suppliers. Or rephrase it if you think we stock it.",
            "options": state["options"],
        }

    # ------------------------------------------------------------- create PR
    def _create_pr(self, state: dict[str, Any]) -> dict[str, Any]:
        if state["pr_no"] is None:
            count = self.db.scalar(select(func.count()).select_from(PurchaseRequestRow).where(PurchaseRequestRow.source == "chat")) or 0
            state["pr_no"] = f"PR-C-{count + 1:05d}"
            while self.db.scalar(select(PurchaseRequestRow).where(PurchaseRequestRow.pr_no == state["pr_no"])):
                count += 1
                state["pr_no"] = f"PR-C-{count + 1:05d}"
        row = PurchaseRequestRow(
            pr_no=state["pr_no"], pr_line=state["line_no"], created_at=datetime.now().replace(second=0, microsecond=0),
            requester=state["requester"], department=state.get("department", ""), plant=state["plant"],
            item_code=state["item_code"] or "", description=state["item_description"] or state["item_text"],
            quantity=state["quantity"], uom=state.get("uom") or "NOS", estimated_unit_price=state.get("estimate"),
            need_by_date=date.fromisoformat(state["need_by"]), urgency=state["urgency"], source="chat",
        )
        self.db.add(row)
        self.db.flush()
        audit(self.db, state["requester"], "pr.create", "pr", f"{row.pr_no}/{row.pr_line}", "Raised through chat",
              item_code=row.item_code or "NEW", quantity=row.quantity, plant=row.plant)
        self.db.commit()
        preview = self._preview(row)
        state["stage"], state["pending"], state["options"] = "done", None, []
        return self._say(
            f"Created **{row.pr_no} line {row.pr_line}**. {preview}\n\nA buyer will review it in the workspace.",
            options=[{"label": "Add another item to this PR", "value": "add_line"}, {"label": "New request", "value": "new_pr"}],
            pr={"pr_no": row.pr_no, "pr_line": row.pr_line},
        )

    def _preview(self, row: PurchaseRequestRow) -> str:
        """Indicative decision: run the engine over all open chat PRs (so clubbing is respected)."""
        open_rows = list(self.db.scalars(select(PurchaseRequestRow).where(PurchaseRequestRow.status == "open")))
        result, _ = run_engine(self.db, [pr_row_to_line(r) for r in open_rows])
        lr = next(l for l in result.lines if l.line.key == (row.pr_no, row.pr_line))
        req = result.requirement_for(lr)
        if req is None:
            return "It could not be decided yet: " + "; ".join(lr.errors)
        clubbed = f" It has been clubbed with {len(req.lines) - 1} other open line(s)." if len(req.lines) > 1 else ""
        who = f" with {req.supplier_name}" if req.supplier_name else ""
        return (f"Indicative route under policy v{req.policy.version}: **{req.route}**{who}, "
                f"value about INR {fmt_inr(req.value)}.{clubbed}")

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _say(text: str, options: list[dict[str, str]] | None = None, pr: dict | None = None) -> dict[str, Any]:
        return {"text": text, "options": options or [], "pr": pr}

    @staticmethod
    def _draft(state: dict[str, Any]) -> dict[str, Any]:
        return {
            "item_code": state.get("item_code") or ("NEW" if state.get("item_status") == "new" else None),
            "item_description": state.get("item_description") or state.get("item_text"),
            "quantity": state.get("quantity"), "uom": state.get("uom"), "plant": state.get("plant"),
            "need_by": state.get("need_by"), "urgency": state.get("urgency"),
            "estimated_unit_price": state.get("estimate"), "pr_no": state.get("pr_no"), "line_no": state.get("line_no"),
        }
