import { useState } from "react";
import { Catalogue, Condition, Rule } from "../types";

/** One change operation, as understood by the backend (engine/amend.py). */
export type Operation = Record<string, unknown> & { op: string };

const ROUTES = ["CONTRACT_PO", "EMERGENCY_PO", "SPOT_PO", "RFQ", "RFP", "CAPEX_SOURCING", "NEW_ITEM_SOURCING"];

function parseValue(raw: string, op: string): unknown {
  const one = (s: string): unknown => {
    const t = s.trim();
    if (t === "true") return true;
    if (t === "false") return false;
    if (t !== "" && !Number.isNaN(Number(t.replace(/,/g, "")))) return Number(t.replace(/,/g, ""));
    return t;
  };
  return op === "in" || op === "not_in" ? raw.split(",").map(one) : one(raw);
}

const showValue = (v: unknown) => (Array.isArray(v) ? v.join(", ") : v === undefined ? "" : String(v));

interface Leaf {
  field: string;
  op: string;
  value: string;
}

function toLeaves(c: Condition): Leaf[] | null {
  if (c.field) return [{ field: c.field, op: c.op ?? "eq", value: showValue(c.value) }];
  if (c.all && c.all.every((x) => x.field)) return c.all.map((x) => ({ field: x.field!, op: x.op ?? "eq", value: showValue(x.value) }));
  return null; // nested any/not: edited as JSON
}

function fromLeaves(leaves: Leaf[]): Condition {
  const conds = leaves.map((l) => ({ field: l.field, op: l.op, value: parseValue(l.value, l.op) }));
  return conds.length === 1 ? conds[0] : { all: conds };
}

export function RuleForm({ initial, catalogue, onSubmit, submitLabel }: {
  initial?: Rule; catalogue: Catalogue; onSubmit: (rule: Rule) => void; submitLabel: string;
}) {
  const [clause, setClause] = useState(initial?.clause ?? "");
  const [title, setTitle] = useState(initial?.title ?? "");
  const startLeaves = initial ? toLeaves(initial.when) : [{ field: "plant", op: "eq", value: "" }];
  const [leaves, setLeaves] = useState<Leaf[]>(startLeaves ?? []);
  const [rawWhen, setRawWhen] = useState(startLeaves ? "" : JSON.stringify(initial?.when, null, 1));
  const [outcome, setOutcome] = useState(
    initial?.outcome ?? { decision: "SOURCING_REQUEST", route: "RFQ", supplier: "suggest", suggest_count: 3, note: null },
  );
  const [enabled, setEnabled] = useState(initial?.enabled ?? true);
  const [error, setError] = useState<string | null>(null);

  const setLeaf = (i: number, patch: Partial<Leaf>) => setLeaves(leaves.map((l, j) => (j === i ? { ...l, ...patch } : l)));

  function submit() {
    try {
      const when = rawWhen ? (JSON.parse(rawWhen) as Condition) : fromLeaves(leaves);
      if (!clause.trim() || !title.trim()) throw new Error("clause and title are required");
      onSubmit({
        id: initial?.id ?? `R-${clause.trim()}`,
        clause: clause.trim(),
        title: title.trim(),
        when,
        outcome: { ...outcome, suggest_count: Number(outcome.suggest_count) || 0 },
        enabled,
      });
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <div className="rule-form">
      <div className="form-grid">
        <label className="field"><span>Clause</span><input value={clause} onChange={(e) => setClause(e.target.value)} placeholder="5.3A" /></label>
        <label className="field wide"><span>Title</span><input value={title} onChange={(e) => setTitle(e.target.value)} /></label>
      </div>
      <h4>When (all of)</h4>
      {rawWhen ? (
        <textarea rows={5} value={rawWhen} onChange={(e) => setRawWhen(e.target.value)} />
      ) : (
        <>
          {leaves.map((l, i) => (
            <div className="cond-row" key={i}>
              <select value={l.field} onChange={(e) => setLeaf(i, { field: e.target.value })}>
                {Object.keys(catalogue.facts).map((f) => <option key={f} value={f} title={catalogue.facts[f]}>{f}</option>)}
              </select>
              <select value={l.op} onChange={(e) => setLeaf(i, { op: e.target.value })}>
                {catalogue.ops.map((o) => <option key={o}>{o}</option>)}
              </select>
              <input value={l.value} onChange={(e) => setLeaf(i, { value: e.target.value })} placeholder={catalogue.facts[l.field]} />
              <button type="button" className="btn ghost" onClick={() => setLeaves(leaves.filter((_, j) => j !== i))}>✕</button>
            </div>
          ))}
          <button type="button" className="btn ghost small" onClick={() => setLeaves([...leaves, { field: "value", op: "lt", value: "" }])}>
            + condition
          </button>
        </>
      )}
      <h4>Then</h4>
      <div className="form-grid">
        <label className="field"><span>Decision</span>
          <select value={outcome.decision} onChange={(e) => setOutcome({ ...outcome, decision: e.target.value,
            supplier: e.target.value === "PO" ? "preferred" : "suggest" })}>
            {catalogue.decisions.map((d) => <option key={d}>{d}</option>)}
          </select>
        </label>
        <label className="field"><span>Route</span>
          <input list="routes" value={outcome.route} onChange={(e) => setOutcome({ ...outcome, route: e.target.value.toUpperCase() })} />
          <datalist id="routes">{ROUTES.map((r) => <option key={r} value={r} />)}</datalist>
        </label>
        <label className="field"><span>Supplier</span>
          <select value={outcome.supplier} onChange={(e) => setOutcome({ ...outcome, supplier: e.target.value })}>
            {catalogue.supplier_modes.map((m) => <option key={m}>{m}</option>)}
          </select>
        </label>
        <label className="field"><span>Suggest how many</span>
          <input type="number" min={0} value={outcome.suggest_count} onChange={(e) => setOutcome({ ...outcome, suggest_count: Number(e.target.value) })} />
        </label>
        <label className="field wide"><span>Note shown to buyers</span>
          <input value={outcome.note ?? ""} onChange={(e) => setOutcome({ ...outcome, note: e.target.value || null })} />
        </label>
        <label className="check"><input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /> enabled</label>
      </div>
      {error && <div className="note error">{error}</div>}
      <button type="button" className="btn" onClick={submit}>{submitLabel}</button>
    </div>
  );
}

/** Build a list of change operations without writing JSON. */
export function OperationsBuilder({ rules, catalogue, operations, setOperations }: {
  rules: Rule[]; catalogue: Catalogue; operations: Operation[]; setOperations: (ops: Operation[]) => void;
}) {
  const [kind, setKind] = useState<"param" | "edit" | "add" | "remove" | "override">("param");
  const [path, setPath] = useState(Object.keys(catalogue.parameters)[0]);
  const [value, setValue] = useState("");
  const [ruleId, setRuleId] = useState(rules[0]?.id ?? "");
  const [before, setBefore] = useState(rules[0]?.id ?? "");
  const [ovField, setOvField] = useState("plant");
  const [ovValue, setOvValue] = useState("");
  const add = (op: Operation) => setOperations([...operations, op]);
  const rule = rules.find((r) => r.id === ruleId);

  return (
    <div className="ops-builder">
      <div className="segmented">
        {[["param", "Change a setting"], ["edit", "Edit a rule"], ["add", "Add a rule"], ["remove", "Remove a rule"], ["override", "Setting for one plant/category"]].map(([k, label]) => (
          <button key={k} type="button" className={kind === k ? "on" : ""} onClick={() => setKind(k as typeof kind)}>{label}</button>
        ))}
      </div>

      {kind === "param" && (
        <div className="form-grid">
          <label className="field wide"><span>Setting</span>
            <select value={path} onChange={(e) => setPath(e.target.value)}>
              {Object.entries(catalogue.parameters).map(([p, d]) => <option key={p} value={p}>{p} (default {JSON.stringify(d)})</option>)}
            </select>
          </label>
          <label className="field"><span>New value</span><input value={value} onChange={(e) => setValue(e.target.value)} placeholder="e.g. 21" /></label>
          <button type="button" className="btn" disabled={!value} onClick={() => add({ op: "set_parameter", path, value: parseJsonish(value) })}>Add change</button>
        </div>
      )}

      {(kind === "edit" || kind === "remove") && (
        <label className="field"><span>Rule</span>
          <select value={ruleId} onChange={(e) => setRuleId(e.target.value)}>
            {rules.map((r) => <option key={r.id} value={r.id}>{r.clause} - {r.title}</option>)}
          </select>
        </label>
      )}
      {kind === "edit" && rule && (
        <RuleForm key={rule.id} initial={rule} catalogue={catalogue} submitLabel="Add change"
          onSubmit={(r) => add({ op: "update_rule", rule_id: rule.id, rule: r })} />
      )}
      {kind === "remove" && (
        <button type="button" className="btn" onClick={() => add({ op: "remove_rule", rule_id: ruleId })}>Add change</button>
      )}
      {kind === "add" && (
        <>
          <label className="field"><span>Insert before</span>
            <select value={before} onChange={(e) => setBefore(e.target.value)}>
              {rules.map((r) => <option key={r.id} value={r.id}>{r.clause} - {r.title}</option>)}
              <option value="">(at the end)</option>
            </select>
          </label>
          <p className="small muted">A rule only for one plant or category goes before the general rule it takes precedence over.</p>
          <RuleForm catalogue={catalogue} submitLabel="Add change"
            onSubmit={(r) => add({ op: "add_rule", rule: r, ...(before ? { before } : {}) })} />
        </>
      )}
      {kind === "override" && (
        <div className="form-grid">
          <label className="field"><span>When</span>
            <select value={ovField} onChange={(e) => setOvField(e.target.value)}>
              {["plant", "category", "item_type", "item_code"].map((f) => <option key={f}>{f}</option>)}
            </select>
          </label>
          <label className="field"><span>is</span><input value={ovValue} onChange={(e) => setOvValue(e.target.value)} placeholder="CHK1" /></label>
          <label className="field wide"><span>Setting</span>
            <select value={path} onChange={(e) => setPath(e.target.value)}>
              {Object.keys(catalogue.parameters).map((p) => <option key={p}>{p}</option>)}
            </select>
          </label>
          <label className="field"><span>Value</span><input value={value} onChange={(e) => setValue(e.target.value)} /></label>
          <button type="button" className="btn" disabled={!ovValue || !value} onClick={() => add({
            op: "add_override",
            override: { id: `OV-${ovValue}-${path}`.replace(/[^A-Za-z0-9.-]/g, "_"), description: `${path} for ${ovField} ${ovValue}`,
              when: { field: ovField, op: "eq", value: ovValue }, set: { [path]: parseJsonish(value) } },
          })}>Add change</button>
        </div>
      )}
    </div>
  );
}

function parseJsonish(v: string): unknown {
  try {
    return JSON.parse(v);
  } catch {
    return v;
  }
}
