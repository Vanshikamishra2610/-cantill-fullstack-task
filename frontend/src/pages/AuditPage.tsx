import { useEffect, useState } from "react";
import { api } from "../api";
import { PageHeader } from "../components/ui";
import { dateTime, inr } from "../format";
import { AuditEntry, DocumentRecord } from "../types";

export default function AuditPage() {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [docs, setDocs] = useState<DocumentRecord[]>([]);
  const [tab, setTab] = useState<"log" | "docs">("log");

  useEffect(() => {
    api.get<AuditEntry[]>("/api/audit?limit=500").then(setEntries);
    api.get<DocumentRecord[]>("/api/documents").then(setDocs);
  }, []);

  return (
    <div className="page">
      <PageHeader title="Audit log" subtitle="Every policy change, batch run, approval and override, with who and when." />
      <div className="segmented">
        <button className={tab === "log" ? "on" : ""} onClick={() => setTab("log")}>Actions ({entries.length})</button>
        <button className={tab === "docs" ? "on" : ""} onClick={() => setTab("docs")}>POs &amp; sourcing requests ({docs.length})</button>
      </div>
      <div className="card flush">
        {tab === "log" ? (
          <table className="table">
            <thead><tr><th>When</th><th>Who</th><th>Action</th><th>On</th><th>Comment</th></tr></thead>
            <tbody>
              {entries.map((e) => (
                <tr key={e.id}>
                  <td className="small">{dateTime(e.at)}</td>
                  <td>{e.actor}</td>
                  <td><code>{e.action}</code></td>
                  <td>{e.entity} {e.entity_id}</td>
                  <td className="small">{e.comment}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <table className="table">
            <thead><tr><th>Document</th><th>Kind</th><th>Requirements</th><th>Supplier / invited</th><th>Plant</th><th className="num">Value</th><th>By</th></tr></thead>
            <tbody>
              {docs.map((d) => (
                <tr key={d.id}>
                  <td><strong>{d.id}</strong></td>
                  <td>{d.kind}{"override_of" in d.payload && <span className="badge np">override</span>}</td>
                  <td className="small">{d.group_ids.join(", ")}</td>
                  <td className="small">{d.supplier_id ?? d.suppliers_invited.join(", ")}</td>
                  <td>{d.plant}</td>
                  <td className="num">{inr(d.value)}</td>
                  <td className="small">{d.created_by}<div className="muted">{dateTime(d.created_at)}</div></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
