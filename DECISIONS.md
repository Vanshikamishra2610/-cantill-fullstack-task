# DECISIONS.md

Places where the policy (v5.0), the brief or the data is silent or ambiguous, what I decided and why.
Each decision is implemented; where it is a tunable choice it lives in the policy JSON
(`backend/app/policies/policy_v5.0.json`), so Procurement can change it without code.

All 14 worked examples and the 3 preferred-supplier rankings are reproduced exactly (route,
supplier, price, value, clubbing, consolidation grouping and suggestions). They are written as tests in
`backend/tests/test_worked_examples.py`.

---

## 1. Lines I do not process (route `NOT_PROCESSED`)

A line is held back, with the reason in the export, when deciding it would mean guessing.

| Situation | Sample | Decision | Why |
|---|---|---|---|
| Quantity zero, negative or missing | PR-26-00172 (qty 0) | Not processed | There is nothing to buy. A zero-value line would also distort clubbing totals. |
| Plant not in the plant master | PR-26-00173 (`PUN2`) | Not processed | `PUN2` could be a typo for PUN1, or a new site. Picking one would send material to the wrong plant and apply the wrong plant rules. |
| Suspected double submission | PR-26-00175 duplicates PR-26-00174 | Second line not processed; first is decided | Same requester, plant, item, quantity (1 servo drive, 65.8k) and need-by date, created 4 minutes apart in a different PR. It is almost certainly raised twice. Ordering a second high-value spare by mistake costs far more than asking the requester to resubmit it. Rule: same requester + plant + item + quantity + need-by date within `duplicates.window_minutes` (60). Lines in the *same* PR, or with any field different, are not treated as duplicates, so legitimate split PRs are still clubbed (2.1). |
| Same PR number + line number twice in a file | - | Second row not processed | The row cannot be told apart from the first one in the export. |
| Description resembles stocked items but is not clearly one | e.g. "gloves" (cotton or nitrile?) | Not processed, candidates listed | Policy 1.1 asks for a match and 1.2 covers lines that match *nothing*. A vague line matches *something*: making it a new item would source an item we already stock, and picking one would be a guess. Chat intake asks the requester instead (see 6). |
| Unit does not match the item master | - | Not processed | Quantities in the wrong unit make the value meaningless. |
| Missing or unreadable creation time / need-by date | - | Not processed | Clubbing and contract validity both depend on these dates. |

## 2. Item matching (1.1, 1.2)

- **The item code wins if it is valid.** If a code is given but is not in the item master, I fall back to the description, and say so in the notes.
- **Free text** is matched by IDF-weighted token overlap, after normalising short forms (`brg` → bearing, `hyd`, `pneu`, `"` → inch, `rod` → electrode) and noise ("urgent", "as per sample", "(repeat)", "for maintenance"). Size and model tokens (`6308`, `M10`, `50x100`) count double.
- **A different size is not a match.** "Ball bearing 6309" is *not* matched to 6308: when the request names a spec the item lacks, that item is rejected. Sharing only a number ("100") is not a match either.
- Thresholds (`matching.min_score`, `vague_score`, `ambiguity_margin`) are policy parameters.
- Every free-text line in the sample resolves to the expected item. The two genuinely new items (water purifier, ultrasonic gauge) score zero against the master and become `NEW_ITEM_SOURCING`.

## 3. Urgency

- **The `urgency` field decides, not the description.** "Anchor fastener - urgent", "nitrile coated gloves - urgent" and "Forklift - urgent" carry `urgency = Normal`, so they are treated as Normal. Emergency lets a buyer skip quotes up to 5 lakh (5.3). If free text could trigger that, anyone could bypass quotes by typing "urgent".
- In chat, when a requester says "urgent", the assistant asks whether it is an **Emergency** and explains what that means. "Breakdown" or "line down" is taken as an emergency.
- A requirement is an emergency if any of its lines is (2.3).

## 4. Clubbing (2.2, 2.3)

- "Within 14 calendar days" compares calendar dates of creation (not 14×24 hours). Need-by within ±7 days is compared with the *first* line's need-by, as written.
- **New items are never clubbed** (1.2), even when two lines have identical descriptions.
- **Contract validity uses the requirement date (first line).** Laptops PR-26-00038 (16 Nov) and PR-26-00044 (20 Nov, PUN1) are one requirement dated 16 Nov. RC-2026-033 starts on 20 Nov, so it does not apply and the requirement goes to RFQ. This follows 2.3 literally. The alternative, splitting the requirement or re-dating it, would undo the anti-splitting intent of 2.1. A buyer can override with a reason if they want the contract rate for the later laptops.
- Lines of one multi-line PR (e.g. PR-26-00164) are decided independently. They are different items, so they are separate requirements.

## 5. Valuation and contracts (3, 4)

- **Ceiling consumption starts from the register on every run.** Each processing run starts from the register's consumed values (as at 31 Oct), with PDF-only contracts at zero (4.2, 4.4). Requirements that end in a contract PO consume ceiling in requirement-date order, at the contract-rate value. Re-running a batch therefore gives the same answer, which is needed for run 1 and run 2 of the blind test. Approvals in the workspace do not move the register; in production the register's monthly update would.
- **A contract whose supplier is not Active does not apply at all** (4.1). That includes valuation: RC-2026-005 (Metalink, Blocked) is ignored, so aluminium plate is valued at the standard price, which reproduces worked example 10 (80,300). The same applies to expired contracts (RC-2025-020).
- **Ceiling check is "consumed + this requirement ≤ ceiling"** (≤, as written). `contracts.ceiling_tolerance_pct` (0) can allow an overrun if Procurement ever wants that.
- **Last PO price (3.1b)** comes from the most recent PO for the item **at any plant and from any supplier**. Worked example 11, a CHK1 requirement valued at a PUN1 PO price, confirms plants are ignored. For two POs on the same day, the higher PO number wins (also matches example 11). The value is only an estimate for choosing the route, so a Blocked supplier's PO still counts as price evidence. It never makes the supplier eligible.
- **"Bought from an Active supplier in the last 365 days"** uses the supplier's *current* status.
- **A PO's price and the requirement value can differ.** The unit price on a spot or emergency PO is the 1st preferred supplier's reference price, while the requirement value (used for the limits) stays the 3.1 valuation. Worked example 6 shows exactly this: valued at 21.19 (latest PO, S028) but ordered from S027 at 21.59.
- **Plant-restricted contracts:** every kit contract covers both plants. Contracts carry an optional plant list so a future single-plant contract is supported.
- **PDF-only contracts** (RC-2026-031, RC-2026-033) are read from their signed PDFs at setup by the same extractor the buyer uses on the Contracts page. The import is logged in the audit trail as a system action.

## 6. Chat intake

- **The assistant never invents an item.** Matching is done by the deterministic matcher against the item master; the LLM only extracts fields from the message. Above 0.75 with a clear margin the item is matched (and shown); between 0.4 and 0.75, or with near ties, the requester picks from up to 3 candidates or "none of these". Below 0.4 the assistant says it could not find the item and offers to raise it as a new item, then asks for an estimated unit price (needed for 1.2 valuation).
- **Follow-ups come in a fixed order:** item → quantity → plant → need-by date → urgency → (estimate for new items) → confirmation. Anything already stated is not asked again.
- **Dates:** "next Friday" means the coming Friday; the date is always echoed back in the summary for confirmation. Past dates are refused.
- Chat PRs are numbered `PR-C-00001` so they never collide with batch PR numbers. After creation the assistant shows an *indicative* decision, computed with all open chat PRs so clubbing is respected. The final decision is made when a buyer processes them in the workspace.

## 7. Decision rules (5)

- **A rule that needs a supplier must have one.** If a PO rule matches but there is no usable contract / no preferred supplier (possible after an amendment shortens a window), the rule is skipped and the next rule decides. If no rule fits at all, the requirement goes to RFQ, the safe default, and the reason says so.
- **Rule 5.7** is enforced in three places: the preferred list (only allowed statuses), the category top-up, and buyer overrides (the API refuses a PO to, or an invitation of, a Blocked or Inactive supplier).
- **Capex** (5.1) is checked before contracts (worked example 12). A capex item on a contract would still go to capex sourcing; the note "Needs capex approval" is carried on the sourcing request.
- **When fewer suppliers are available than requested, I suggest the ones that exist.** Example 11 shows only 4 suppliers for an RFP that asks for 5, because only 4 Active suppliers list Electrical Spares; grease gets 2 for the same reason.
- New items get no suggestions (6.4).

## 8. Preferred supplier ranking (6.2) - my method

The policy names four factors and leaves the weighting to the system:

```
score = 0.35 × frequency     POs for the item in the 548-day window ÷ the most POs any listed supplier has
      + 0.25 × recency       1 if the last PO was within 90 days, 0.5 within 365 days, else 0 (the buyers' buckets)
      + 0.20 × contract      1 if the supplier has held a contract for the item that started on/before the date
      + 0.20 × performance   ½ × on-time % + ½ × quality rating ÷ 5
ties: higher performance, then supplier id
```

Why this shape:
- **History first (frequency + recency = 60%).** A direct PO is a bet that the supplier can deliver this exact item again at a known price. Proven, recent supply is the best evidence. Frequency is *relative* to the busiest supplier for the item, so one PO matters for a rarely bought item and little for a fast mover.
- **A contract is strong but not decisive (20%).** A contract holder has been vetted and negotiated with. It still loses to a supplier we buy from far more often: for bearings 6308, S014 (5 POs) ranks above contract-holder S013 (1 PO), exactly as Corvo's buyers ranked it in example 3.
- **Performance breaks near-ties (20%).** On-time and quality are company-wide ratings, not item-specific, so they refine rather than dominate.
- **Validation:** the weights reproduce all three published rankings (nitrile gloves, pneumatic cylinder, MIG wire) and every ranked suggestion in the worked examples (3, 9, 10, 11, 12). The 6.4 category top-up orders by the same performance measure. Example 10 (S003, S002, S005 ahead of S001) needs an on-time weight between about 15% and 58%. I chose the simple, explainable 50/50.
- **Reference price** (6.3) is the supplier's latest PO price in the window, otherwise its latest contract rate. Buyers see every component, the score and the source of the reference price (PO number or contract) on each requirement and on the Preferred suppliers page.
- All weights, buckets and windows are policy parameters, so Procurement can retune them by amendment.
- **Export:** for every item, top 3 (`item_code, rank, supplier_id, reference_price`) as of the chosen date, using the policy version in force that day. Items with no eligible supplier (no history or contract with an Active supplier) are left out, since there is nothing to rank.

## 9. PO consolidation (7)

- **The 24-hour window is measured from the first requirement on the PO,** not chained. PR-26-00159 (26 h 57 min after PR-26-00156) opens a new PO even though it is within 24 h of PR-26-00158.
- **PO numbers** are sequential per processing run (`PO-0001`…) in order of PO creation. Because PR-26-00174 (08:10 on 2 Nov, a spot PO) is the earliest PO in the batch, my numbers are the worked examples' numbers + 1 (hydraulic oil is PO-0002, not PO-0001). The examples were evidently numbered without the servo-drive PRs at the end of the file. The groupings, which requirements share a PO, match exactly.
- When a buyer approves a requirement that ends in a PO, the whole consolidated PO is created (all its requirements), since a PO is one document.

## 10. Policy versions and amendments

- **A requirement is decided by the version in force on its requirement date** (latest approved version with `effective_from` ≤ that date). An amendment effective 15 Nov therefore changes only requirements dated 15 Nov or later, and the export shows the version per row. If two versions share an effective date, the one approved later wins.
- A requirement dated before the first version's effective date uses the earliest version (never seen in practice; v5.0 is effective from 1 Apr 2026).
- **Nothing activates without approval.** The AI reader proposes a *draft*: the changes in plain language, the source text, and any sentence it could not express ("Not applied: …"). The approver can change the effective date before approving. If the amendment states no date, the draft defaults to today and warns.
- **A new kind of rule is a new rule entry.** It can test any requirement fact (plant, category, item type, value, urgency, contract availability, history, lead time…) and is inserted where it should take precedence. Plant- or category-specific *settings* (e.g. a longer clubbing window at CHK1) are `overrides`. Neither needs code.
- **Withdrawing** an approved version needs a reason; the baseline cannot be withdrawn.
- Every draft, approval, rejection and withdrawal is in the audit log with who and when.

## 11. Workspace and records (8.1)

- The engine's decision is stored unchanged. An override stores the buyer's decision next to it with the required comment (at least 5 characters), and both appear in the requirement history and the audit log.
- `export.csv` gives the engine's decisions (the format for scoring); `?include_overrides=true` gives the buyer-final view, with the override reason in `reason`.
- There is no login in this prototype. The user picker in the sidebar sets who is acting, and every API call records that name. Real deployment would put SSO in front of the API.

## 12. Blind test notes (19 Nov 2026)

All three files were produced on the hosted app; no code was changed.

- **Run 1** (`submission/vanshika_mishra__run1.csv`): `hidden_pr_batch.csv` uploaded to the workspace, all 190 lines decided under v5.0.
- **Run 2** (`submission/vanshika_mishra__run2.csv`): the amendment PDF was uploaded through Policy → Propose a change. The review screen showed that the AI's reading was incomplete, so that draft was rejected with the reasons recorded:
  - It added a Packaging spot rule but left rule 5.4 at 2,00,000, so Packaging at 1–2 lakh would still have got a spot PO.
  - Its rule 5.2A always routed to RFQ, ignoring "RFP at or above the RFP threshold".
- The amendment was then described in the policy chat. Two chat drafts were also rejected:
  - One was numbered 5.2, because the rejected draft still held 5.1.
  - One placed the Packaging rule after 5.5, where it could never apply.
- The approved v5.1 (effective 1 Dec 2026, approved by Procurement) changes:
  - Rule 5.4 is "below 2,00,000 with recent history, except Packaging at 1,00,000 or more".
  - Rules 5.5 and 5.6 use 8,00,000.
  - New rule 5.2A sits right after 5.2: CHK1 Electrical Spares at 50,000 or more go to RFQ, or to RFP at 8,00,000 or more. Contract POs under 5.2 still win.
- The full trail is in the Policy page and the audit log.
- The batch was then reprocessed.
- **Effective date:** the policy is chosen by each requirement's creation date, and a clubbed requirement uses its earliest line. So 91 lines created before 1 Dec stay on v5.0, and 99 lines use v5.1.
- **Changes against Run 1:**
  - 8 spot POs became RFQ under 5.2A (Chakan electrical).
  - 7 spot POs became RFQ under 5.5 (Packaging at 1,00,000 or more).
  - 4 RFQs became RFP under the 8,00,000 threshold.
  - PO numbers shift because fewer POs are raised.
- **Preferred suppliers** (`submission/vanshika_mishra__preferred.csv`): top 3 per item as of 2026-12-01, giving 68 items and 155 rows. Items with fewer than three eligible suppliers have fewer rows.
- Both runs were cross-checked against the engine run locally on the same batch (0 differences). For Run 2 the check used a hand-built v5.1.
