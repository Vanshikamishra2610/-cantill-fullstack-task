import { FormEvent, useEffect, useState } from "react";
import { api } from "../api";
import { dateTime, day, inr, price, ROUTE_LABEL } from "../format";
import { AuditEntry, LineDetail, Requirement, Supplier } from "../types";
import { ErrorNote, RouteBadge, StatusBadge } from "./ui";

const ROUTES = ["CONTRACT_PO", "EMERGENCY_PO", "SPOT_PO", "RFQ", "RFP", "CAPEX_SOURCING", "NEW_ITEM_SOURCING"];

interface Props {
  runId: number;
  req: Requirement;
  lines: LineDetail[];
  poMembers: Requirement[];
  suppliers: Supplier[];
  onChanged: () => void;
  onClose: () => void;
}

export default function RequirementPanel({ runId, req, lines, poMembers, suppliers, onChanged, onClose }: Props) {
  const [mode, setMode] = useState<"view" | "override">("view");
  const [history, setHistory] = useState<AuditEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setMode("view");
    setError(null);
    api.get<AuditEntry[]>(`/api/runs/${runId}/requirements/${req.group_id}/history`).then(setHistory);
  }, [runId, req.group_id, req.status]);

  const isPo = req.decision === "PO";
  const approveLabel = isPo
    ? `Approve and create ${req.po_id}${poMembers.length > 1 ? ` (${poMembers.length} requirements)` : ""}`
    : `Approve and raise ${ROUTE_LABEL[req.route] ?? req.route}`;

  async function approve() {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/api/runs/${runId}/requirements/${req.group_id}/approve`, {});
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <aside className="panel">
      <div className="panel-head">
        <div>
          <div className="eyebrow">
            {req.group_id} · {req.plant} · requirement date {day(req.requirement_date)}
          </div>
          <h2>
            {req.item_code} <span className="muted">{req.item_description}</span>
          </h2>
          <div className="row gap">
            <RouteBadge route={req.final?.route ?? req.route} />
            <StatusBadge status={req.status} />
            {req.is_emergency && <span className="badge np">Emergency</span>}
          </div>
        </div>
        <button className="btn ghost" onClick={onClose} aria-label="Close">✕</button>
      </div>

      {req.status === "pending" && mode === "view" && (
        <div className="next-action">
          <button className="btn primary big" disabled={busy} onClick={approve}>{approveLabel}</button>
          <button className="btn" onClick={() => setMode("override")}>Override…</button>
        </div>
      )}
      {req.status !== "pending" && (
        <div className="note ok">
          {req.status === "approved" ? "Approved" : "Overridden"} - document <strong>{req.document_id}</strong>
          {req.final && (
            <>
              <br />
              Buyer decision: {ROUTE_LABEL[req.final.route]} {req.final.supplier_id && `to ${req.final.supplier_id} at ${price(req.final.unit_price)}`}
              <br />
              Reason: “{req.final.comment}” - {req.final.by}
            </>
          )}
        </div>
      )}
      <ErrorNote error={error} />

      {mode === "override" && (
        <OverrideForm runId={runId} req={req} suppliers={suppliers} onDone={onChanged} onCancel={() => setMode("view")} />
      )}

      <section className="why">
        <h3>Why</h3>
        <p>{req.reason}</p>
        <div className="chips">
          <span className="chip">Policy v{req.policy_version}</span>
          {req.rule && <span className="chip">Clause {req.rule.clause}: {req.rule.title}</span>}
          {req.contract_id && <span className="chip">Contract {req.contract_id}</span>}
          {req.reference && !req.contract_id && <span className="chip">Price from {req.reference}</span>}
        </div>
      </section>

      <section className="grid-2">
        <div className="stat">
          <span>Requirement value</span>
          <strong>INR {inr(req.value)}</strong>
          <small>
            {req.quantity} {req.uom} × {price(req.valuation_price)} ({req.price_source})
          </small>
        </div>
        {isPo ? (
          <div className="stat">
            <span>Supplier &amp; price</span>
            <strong>
              {req.supplier_id} {req.supplier_name}
            </strong>
            <small>
              {price(req.unit_price)} per {req.uom} · {req.po_id}
              {poMembers.length > 1 && ` with ${poMembers.filter((m) => m.group_id !== req.group_id).map((m) => m.group_id).join(", ")}`}
            </small>
          </div>
        ) : (
          <div className="stat">
            <span>Suggested suppliers</span>
            {req.suggestions.length === 0 ? (
              <small>{req.route === "NEW_ITEM_SOURCING" ? "New item: the buyer finds suppliers (6.4)." : "None found."}</small>
            ) : (
              <ol className="suggest">
                {req.suggestions.map((s) => (
                  <li key={s.supplier_id}>
                    <strong>{s.supplier_id}</strong> {s.name}{" "}
                    <small className="muted">
                      {s.source} · {s.on_time_pct}% on time · quality {s.quality_rating}
                    </small>
                  </li>
                ))}
              </ol>
            )}
          </div>
        )}
      </section>

      <section>
        <h3>PR lines ({lines.length})</h3>
        <table className="table compact">
          <thead>
            <tr><th>PR / line</th><th>Created</th><th>Requested as</th><th>Qty</th><th>Need by</th><th>Urgency</th></tr>
          </thead>
          <tbody>
            {lines.map((l) => (
              <tr key={`${l.pr_no}-${l.pr_line}`}>
                <td>{l.pr_no}/{l.pr_line}<div className="small muted">{l.requester}, {l.department}</div></td>
                <td>{l.created_at}</td>
                <td>
                  {l.item_code ? <code>{l.item_code}</code> : <em>“{l.description}”</em>}
                  {l.match?.method === "description" && (
                    <div className="small muted">matched by description, score {l.match.score.toFixed(2)}</div>
                  )}
                </td>
                <td>{l.quantity} {l.uom}</td>
                <td>{l.need_by_date}</td>
                <td>{l.urgency}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {req.contract_review.length > 0 && (
        <section>
          <h3>Contracts checked (4.1–4.3)</h3>
          <table className="table compact">
            <thead><tr><th>Contract</th><th>Supplier</th><th>Rate</th><th>This requirement</th><th>Ceiling left</th><th></th></tr></thead>
            <tbody>
              {req.contract_review.map((c) => (
                <tr key={c.contract_id}>
                  <td>{c.contract_id}</td>
                  <td>{c.supplier_id}</td>
                  <td>{price(c.rate)}</td>
                  <td>{inr(c.value_at_rate)}</td>
                  <td>{inr(c.remaining_before)} of {inr(c.ceiling)}</td>
                  <td>{c.usable ? <span className="badge po">usable</span> : <span className="badge np">no room</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {req.preferred.length > 0 && (
        <section>
          <h3>Preferred supplier list on {day(req.requirement_date)}</h3>
          <PreferredTable entries={req.preferred} />
        </section>
      )}

      {history.length > 0 && (
        <section>
          <h3>History</h3>
          <ul className="timeline">
            {history.map((h) => (
              <li key={h.id}>
                <strong>{h.action.replace("decision.", "")}</strong> by {h.actor} · {dateTime(h.at)}
                {h.comment && <div className="small">“{h.comment}”</div>}
              </li>
            ))}
          </ul>
        </section>
      )}
    </aside>
  );
}

export function PreferredTable({ entries }: { entries: Requirement["preferred"] }) {
  return (
    <table className="table compact">
      <thead>
        <tr>
          <th>#</th><th>Supplier</th><th title="POs in the look-back window">POs</th><th>Last PO</th>
          <th title="1 = within 90 days, 0.5 = within a year">Recency</th><th>Contract</th>
          <th title="on-time % and quality">Delivery / quality</th><th>Score</th><th>Ref. price</th>
        </tr>
      </thead>
      <tbody>
        {entries.map((e) => (
          <tr key={e.supplier_id}>
            <td>{e.rank}</td>
            <td><strong>{e.supplier_id}</strong> <span className="small">{e.supplier_name}</span></td>
            <td>{e.po_count}</td>
            <td>{day(e.last_po_date)}</td>
            <td>{e.recency}</td>
            <td>{e.holds_contract ? "yes" : "no"}</td>
            <td>{e.on_time_pct}% / {e.quality_rating}</td>
            <td><strong>{e.score.toFixed(3)}</strong></td>
            <td title={e.reference_source}>{price(e.reference_price)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function OverrideForm({ runId, req, suppliers, onDone, onCancel }: {
  runId: number; req: Requirement; suppliers: Supplier[]; onDone: () => void; onCancel: () => void;
}) {
  const [route, setRoute] = useState(req.route);
  const [supplierId, setSupplierId] = useState(req.supplier_id ?? req.preferred[0]?.supplier_id ?? "");
  const [unitPrice, setUnitPrice] = useState<string>(String(req.unit_price ?? req.valuation_price ?? ""));
  const [suggested, setSuggested] = useState(req.suggestions.map((s) => s.supplier_id).join(", "));
  const [comment, setComment] = useState("");
  const [error, setError] = useState<string | null>(null);
  const isPo = route.endsWith("_PO");
  const active = suppliers.filter((s) => s.status === "Active");

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api.post(`/api/runs/${runId}/requirements/${req.group_id}/override`, {
        route,
        comment,
        supplier_id: isPo ? supplierId : null,
        unit_price: isPo ? Number(unitPrice) : null,
        suggested_supplier_ids: isPo ? [] : suggested.split(/[,; ]+/).filter(Boolean),
      });
      onDone();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <form className="card override" onSubmit={submit}>
      <h3>Override the decision</h3>
      <p className="small muted">The engine's decision is kept alongside yours. A written reason is required.</p>
      <div className="form-grid">
        <label className="field">
          <span>Route</span>
          <select value={route} onChange={(e) => setRoute(e.target.value)}>
            {ROUTES.map((r) => <option key={r} value={r}>{ROUTE_LABEL[r]}</option>)}
          </select>
        </label>
        {isPo ? (
          <>
            <label className="field">
              <span>Supplier (Active only, 5.7)</span>
              <select value={supplierId} onChange={(e) => setSupplierId(e.target.value)}>
                {active.map((s) => (
                  <option key={s.supplier_id} value={s.supplier_id}>{s.supplier_id} {s.name}</option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Unit price (INR)</span>
              <input type="number" step="0.01" min="0" value={unitPrice} onChange={(e) => setUnitPrice(e.target.value)} />
            </label>
          </>
        ) : (
          <label className="field wide">
            <span>Suppliers to invite (ids, comma separated)</span>
            <input value={suggested} onChange={(e) => setSuggested(e.target.value)} />
          </label>
        )}
        <label className="field wide">
          <span>Reason for the override</span>
          <textarea required minLength={5} rows={3} value={comment} onChange={(e) => setComment(e.target.value)}
            placeholder="e.g. Supplier confirmed stock for the breakdown; quote attached to the PO" />
        </label>
      </div>
      <ErrorNote error={error} />
      <div className="row gap">
        <button className="btn primary" disabled={comment.trim().length < 5}>Override and create document</button>
        <button type="button" className="btn ghost" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}
