import { ChangeEvent, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { ErrorNote, PageHeader } from "../components/ui";
import { day, inr, price } from "../format";
import { Contract } from "../types";

interface Draft {
  contract_id: string;
  supplier_id: string;
  supplier_name: string;
  start_date: string;
  end_date: string;
  value_cap: number;
  consumed_value: number;
  plants: string[];
  lines: { item_code: string; description: string; uom: string; rate: number }[];
  source: string;
  warnings?: string[];
  already_registered?: boolean;
  method?: string;
}

const EMPTY: Draft = {
  contract_id: "", supplier_id: "", supplier_name: "", start_date: "", end_date: "", value_cap: 0, consumed_value: 0,
  plants: [], lines: [{ item_code: "", description: "", uom: "", rate: 0 }], source: "manual",
};

export default function ContractsPage() {
  const [contracts, setContracts] = useState<Contract[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const pdfRef = useRef<HTMLInputElement>(null);
  const regRef = useRef<HTMLInputElement>(null);

  const load = () => api.get<Contract[]>("/api/contracts").then(setContracts);
  useEffect(() => {
    load();
  }, []);

  async function onPdf(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const ex = await api.upload<Draft>("/api/contracts/extract", form);
      setDraft({ ...ex, consumed_value: 0, source: `pdf:${file.name}` });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function onRegister(e: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? []);
    e.target.value = "";
    const register = files.find((f) => f.name.includes("register"));
    const lines = files.find((f) => f.name.includes("lines"));
    if (!register || !lines) {
      setError("Select both contract_register.csv and contract_lines.csv");
      return;
    }
    const form = new FormData();
    form.append("register", register);
    form.append("lines", lines);
    try {
      await api.upload("/api/contracts/register", form);
      load();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function save() {
    if (!draft) return;
    setError(null);
    try {
      await api.post("/api/contracts", draft);
      setDraft(null);
      load();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  const setLine = (i: number, patch: Partial<Draft["lines"][number]>) =>
    draft && setDraft({ ...draft, lines: draft.lines.map((l, j) => (j === i ? { ...l, ...patch } : l)) });

  return (
    <div className="page">
      <PageHeader
        title="Rate contracts"
        subtitle="The register as at its last update, plus contracts signed later that exist only as PDFs (4.4). PDF-only contracts start with zero consumption."
        actions={
          <>
            <input ref={pdfRef} type="file" accept=".pdf" hidden onChange={onPdf} />
            <input ref={regRef} type="file" accept=".csv" multiple hidden onChange={onRegister} />
            <button className="btn" onClick={() => regRef.current?.click()}>Load register CSVs</button>
            <button className="btn" onClick={() => setDraft({ ...EMPTY })}>Add manually</button>
            <button className="btn primary" disabled={busy} onClick={() => pdfRef.current?.click()}>
              {busy ? "Reading PDF…" : "Add from signed PDF"}
            </button>
          </>
        }
      />
      <ErrorNote error={error} />

      {draft && (
        <div className="card stack">
          <h3>{draft.source.startsWith("pdf") ? `Review the extracted contract (${draft.method})` : "New contract"}</h3>
          {draft.already_registered && <div className="note warn">{draft.contract_id} is already in the register.</div>}
          {draft.warnings?.map((w, i) => <div key={i} className="note warn">{w}</div>)}
          <div className="form-grid">
            <label className="field"><span>Contract no.</span><input value={draft.contract_id} onChange={(e) => setDraft({ ...draft, contract_id: e.target.value })} /></label>
            <label className="field"><span>Supplier id</span><input value={draft.supplier_id} onChange={(e) => setDraft({ ...draft, supplier_id: e.target.value })} /></label>
            <label className="field"><span>Supplier name</span><input value={draft.supplier_name} onChange={(e) => setDraft({ ...draft, supplier_name: e.target.value })} /></label>
            <label className="field"><span>Valid from</span><input type="date" value={draft.start_date} onChange={(e) => setDraft({ ...draft, start_date: e.target.value })} /></label>
            <label className="field"><span>Valid to</span><input type="date" value={draft.end_date} onChange={(e) => setDraft({ ...draft, end_date: e.target.value })} /></label>
            <label className="field"><span>Value ceiling (INR)</span><input type="number" value={draft.value_cap} onChange={(e) => setDraft({ ...draft, value_cap: Number(e.target.value) })} /></label>
            <label className="field"><span>Already consumed (INR)</span><input type="number" value={draft.consumed_value} onChange={(e) => setDraft({ ...draft, consumed_value: Number(e.target.value) })} /></label>
            <label className="field"><span>Plants (blank = all)</span><input value={draft.plants.join(", ")} onChange={(e) => setDraft({ ...draft, plants: e.target.value.split(/[ ,;]+/).filter(Boolean) })} /></label>
          </div>
          <table className="table compact">
            <thead><tr><th>Item code</th><th>Description</th><th>UoM</th><th>Rate</th><th></th></tr></thead>
            <tbody>
              {draft.lines.map((l, i) => (
                <tr key={i}>
                  <td><input value={l.item_code} onChange={(e) => setLine(i, { item_code: e.target.value.toUpperCase() })} /></td>
                  <td><input value={l.description} onChange={(e) => setLine(i, { description: e.target.value })} /></td>
                  <td><input value={l.uom} onChange={(e) => setLine(i, { uom: e.target.value })} /></td>
                  <td><input type="number" step="0.01" value={l.rate} onChange={(e) => setLine(i, { rate: Number(e.target.value) })} /></td>
                  <td><button className="btn ghost" onClick={() => setDraft({ ...draft, lines: draft.lines.filter((_, j) => j !== i) })}>✕</button></td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="row gap">
            <button className="btn ghost small" onClick={() => setDraft({ ...draft, lines: [...draft.lines, { item_code: "", description: "", uom: "", rate: 0 }] })}>+ item</button>
          </div>
          <div className="row gap">
            <button className="btn primary" onClick={save}>Save to register</button>
            <button className="btn ghost" onClick={() => setDraft(null)}>Cancel</button>
          </div>
        </div>
      )}

      <div className="card flush">
        <table className="table">
          <thead>
            <tr><th>Contract</th><th>Supplier</th><th>Validity</th><th className="num">Ceiling</th><th className="num">Consumed</th><th className="num">Left</th><th>Items &amp; rates</th><th>Source</th></tr>
          </thead>
          <tbody>
            {contracts.map((c) => (
              <tr key={c.contract_id}>
                <td><strong>{c.contract_id}</strong></td>
                <td>{c.supplier_id} {c.supplier_name}</td>
                <td>{day(c.start_date)} – {day(c.end_date)}<div><span className={`badge ${c.validity === "active" ? "po" : "np"}`}>{c.validity}</span></div></td>
                <td className="num">{inr(c.value_cap)}</td>
                <td className="num">{inr(c.consumed_value)}</td>
                <td className="num">{inr(c.remaining)}</td>
                <td className="small">{c.lines.map((l) => `${l.item_code} @ ${price(l.rate)}`).join(", ")}</td>
                <td className="small">{c.source}<div className="muted">{c.added_by}</div></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
