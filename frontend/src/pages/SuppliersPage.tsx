import { useEffect, useMemo, useState } from "react";
import { api, download } from "../api";
import { PreferredTable } from "../components/RequirementPanel";
import { Empty, ErrorNote, PageHeader } from "../components/ui";
import { PreferredEntry } from "../types";

interface Lists {
  as_of: string;
  policy_version: string;
  weights: Record<string, number>;
  items: { item_code: string; description: string; category: string; entries: PreferredEntry[] }[];
}

export default function SuppliersPage() {
  const [asOf, setAsOf] = useState(new Date().toISOString().slice(0, 10));
  const [data, setData] = useState<Lists | null>(null);
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.get<Lists>(`/api/preferred-suppliers?as_of=${asOf}`).then(setData).catch((e) => setError(e.message));
  }, [asOf]);

  const items = useMemo(() => {
    const q = query.toLowerCase();
    return (data?.items ?? []).filter((i) => !q || `${i.item_code} ${i.description} ${i.category}`.toLowerCase().includes(q));
  }, [data, query]);

  return (
    <div className="page">
      <PageHeader
        title="Preferred supplier lists"
        subtitle="Built from PO history and contracts as of the chosen date (policy section 6). Blocked and inactive suppliers are never listed."
        actions={
          <button className="btn primary" onClick={() => download(`/api/preferred-suppliers/export.csv?as_of=${asOf}&top=3`, `preferred_suppliers_${asOf}.csv`)}>
            Export top 3 as CSV
          </button>
        }
      />
      <ErrorNote error={error} />
      <div className="filters">
        <label className="field inline">
          <span>As of</span>
          <input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} />
        </label>
        <input placeholder="Filter items…" value={query} onChange={(e) => setQuery(e.target.value)} />
      </div>
      {data && (
        <div className="card explain">
          <strong>How the ranking works (policy v{data.policy_version})</strong>
          <p className="small">
            score = {data.weights.frequency} × frequency (POs in the window ÷ most POs of any listed supplier)
            {" + "}{data.weights.recency} × recency (1 if the last PO was within 90 days, 0.5 within a year)
            {" + "}{data.weights.contract} × held a contract for the item
            {" + "}{data.weights.performance} × delivery &amp; quality (½ on-time %, ½ quality ÷ 5).
            Reference price = the supplier's latest PO price in the window, else its latest contract rate.
          </p>
        </div>
      )}
      {items.map((it) => (
        <div className="card" key={it.item_code}>
          <h3><code>{it.item_code}</code> {it.description} <span className="muted small">{it.category}</span></h3>
          {it.entries.length ? <PreferredTable entries={it.entries} /> : <Empty>No eligible supplier has bought or contracted this item.</Empty>}
        </div>
      ))}
    </div>
  );
}
