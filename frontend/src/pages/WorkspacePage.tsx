import { ChangeEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, download } from "../api";
import RequirementPanel from "../components/RequirementPanel";
import { Empty, ErrorNote, PageHeader, RouteBadge, StatusBadge } from "../components/ui";
import { dateTime, inr, price, ROUTE_LABEL } from "../format";
import { RunDetail, RunSummary, Supplier } from "../types";

type StatusFilter = "pending" | "done" | "all";

export default function WorkspacePage() {
  const { runId } = useParams();
  const navigate = useNavigate();
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [run, setRun] = useState<RunDetail | null>(null);
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [openPrs, setOpenPrs] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [status, setStatus] = useState<StatusFilter>("pending");
  const [route, setRoute] = useState<string>("");
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const loadRuns = useCallback(async () => {
    const [r, prs] = await Promise.all([
      api.get<RunSummary[]>("/api/runs"),
      api.get<{ status: string }[]>("/api/prs"),
    ]);
    setRuns(r);
    setOpenPrs(prs.filter((p) => p.status === "open").length);
    return r;
  }, []);

  const loadRun = useCallback(async (id: number) => setRun(await api.get<RunDetail>(`/api/runs/${id}`)), []);

  useEffect(() => {
    api.get<Supplier[]>("/api/suppliers").then(setSuppliers);
    loadRuns().then((r) => {
      if (!runId && r.length) navigate(`/workspace/${r[0].id}`, { replace: true });
    });
  }, [loadRuns, navigate, runId]);

  useEffect(() => {
    if (runId) loadRun(Number(runId)).catch((e) => setError(e.message));
    setSelected(null);
  }, [runId, loadRun]);

  async function act(fn: () => Promise<{ id: number } | RunSummary | { run_id: number }>) {
    setBusy(true);
    setError(null);
    try {
      const res = await fn();
      await loadRuns();
      const id = "run_id" in res ? res.run_id : res.id;
      navigate(`/workspace/${id}`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function onUpload(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    act(() => api.upload("/api/process-batch", form));
    e.target.value = "";
  }

  const lineDetails = useMemo(() => {
    const map = new Map<string, RunDetail["lines"][number]["detail"][]>();
    run?.lines.forEach((l) => {
      if (!l.detail.requirement_id) return;
      map.set(l.detail.requirement_id, [...(map.get(l.detail.requirement_id) ?? []), l.detail]);
    });
    return map;
  }, [run]);

  const visible = useMemo(() => {
    if (!run) return [];
    const q = query.toLowerCase();
    return run.requirements.filter(
      (r) =>
        (status === "all" || (status === "pending" ? r.status === "pending" : r.status !== "pending")) &&
        (!route || r.route === route) &&
        (!q || `${r.group_id} ${r.item_code} ${r.item_description} ${r.lines.join(" ")} ${r.supplier_id ?? ""}`.toLowerCase().includes(q)),
    );
  }, [run, status, route, query]);

  const notProcessed = run?.lines.filter((l) => l.row.route === "NOT_PROCESSED") ?? [];
  const current = run?.requirements.find((r) => r.group_id === selected) ?? null;
  const pending = run?.requirements.filter((r) => r.status === "pending").length ?? 0;

  return (
    <div className={current ? "page with-panel" : "page"}>
      <PageHeader
        title="Buyer workspace"
        subtitle="Each requirement shows the policy decision and why. Approve to create the PO or sourcing request."
        actions={
          <>
            <input ref={fileRef} type="file" accept=".csv" hidden onChange={onUpload} />
            {openPrs > 0 && (
              <button className="btn" disabled={busy} onClick={() => act(() => api.post("/api/prs/process"))}>
                Process {openPrs} chat PR{openPrs > 1 ? "s" : ""}
              </button>
            )}
            <button className={runs.length ? "btn" : "btn primary"} disabled={busy} onClick={() => fileRef.current?.click()}>
              Upload PR batch CSV
            </button>
          </>
        }
      />
      <ErrorNote error={error} />

      {runs.length === 0 ? (
        <Empty>No batches processed yet. Upload a PR batch CSV (e.g. pr_batches/sample_pr_batch.csv) or raise requests through chat.</Empty>
      ) : (
        <>
          <div className="run-bar card">
            <label className="field">
              <span>Batch</span>
              <select value={runId ?? ""} onChange={(e) => navigate(`/workspace/${e.target.value}`)}>
                {runs.map((r) => (
                  <option key={r.id} value={r.id}>
                    #{r.id} {r.name} · {dateTime(r.created_at)} · policy {r.policy_versions.join(", ")}
                  </option>
                ))}
              </select>
            </label>
            {run && (
              <div className="run-stats">
                <div><strong>{run.summary.lines}</strong><span>PR lines</span></div>
                <div><strong>{run.summary.requirements}</strong><span>requirements</span></div>
                <div><strong>{run.summary.purchase_orders}</strong><span>POs</span></div>
                <div><strong>{inr(run.summary.total_value)}</strong><span>INR total</span></div>
                <div><strong>{pending}</strong><span>to review</span></div>
              </div>
            )}
            {run && (
              <div className="row gap">
                <button className="btn" onClick={() => download(`/api/runs/${run.id}/export.csv`, `decisions_run${run.id}.csv`)}>
                  Export decisions CSV
                </button>
                <button className="btn ghost" disabled={busy} title="Re-run this input under the policy versions active now"
                  onClick={() => act(() => api.post(`/api/runs/${run.id}/reprocess`))}>
                  Reprocess under current policy
                </button>
              </div>
            )}
          </div>

          {run && (
            <>
              <div className="filters">
                <div className="segmented">
                  {(["pending", "done", "all"] as StatusFilter[]).map((s) => (
                    <button key={s} className={status === s ? "on" : ""} onClick={() => setStatus(s)}>
                      {s === "pending" ? `To review (${pending})` : s === "done" ? "Decided" : "All"}
                    </button>
                  ))}
                </div>
                <select value={route} onChange={(e) => setRoute(e.target.value)}>
                  <option value="">All routes</option>
                  {Object.entries(run.summary.routes).filter(([r]) => r !== "NOT_PROCESSED").map(([r, n]) => (
                    <option key={r} value={r}>{ROUTE_LABEL[r] ?? r} ({n} lines)</option>
                  ))}
                </select>
                <input placeholder="Search item, PR, supplier…" value={query} onChange={(e) => setQuery(e.target.value)} />
              </div>

              <div className="card flush">
                <table className="table clickable">
                  <thead>
                    <tr>
                      <th>Req.</th><th>Item</th><th>Plant</th><th>Lines</th><th className="num">Value (INR)</th>
                      <th>Decision</th><th>Supplier / suggestions</th><th>Clause</th><th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visible.map((r) => (
                      <tr key={r.group_id} className={r.group_id === selected ? "selected" : ""} onClick={() => setSelected(r.group_id)}>
                        <td>{r.group_id}<div className="small muted">{r.created_at.replace("T", " ")}</div></td>
                        <td><code>{r.item_code}</code> <span className="small">{r.item_description}</span></td>
                        <td>{r.plant}</td>
                        <td>{r.lines.length}</td>
                        <td className="num">{inr(r.value)}</td>
                        <td><RouteBadge route={r.final?.route ?? r.route} />{r.po_id && <div className="small muted">{r.po_id}</div>}</td>
                        <td className="small">
                          {r.supplier_id ? `${r.supplier_id} @ ${price(r.unit_price)}` : r.suggestions.map((s) => s.supplier_id).join(", ") || "-"}
                        </td>
                        <td className="small">v{r.policy_version} §{r.rule?.clause}</td>
                        <td><StatusBadge status={r.status} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {visible.length === 0 && <Empty>Nothing here. {status === "pending" && "All requirements in this batch are decided."}</Empty>}
              </div>

              {notProcessed.length > 0 && (
                <div className="card">
                  <h3>Lines not processed ({notProcessed.length})</h3>
                  <p className="small muted">These need the requester to correct or confirm the PR; they are exported with route NOT_PROCESSED.</p>
                  <table className="table compact">
                    <thead><tr><th>PR / line</th><th>Description</th><th>Reason</th></tr></thead>
                    <tbody>
                      {notProcessed.map((l) => (
                        <tr key={`${l.row.pr_no}-${l.row.pr_line}`}>
                          <td>{l.row.pr_no}/{l.row.pr_line}</td>
                          <td>{l.detail.description} ({l.detail.quantity} {l.detail.uom}, {l.detail.plant})</td>
                          <td className="small">{l.row.reason.replace("Not processed: ", "")}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
        </>
      )}

      {run && current && (
        <RequirementPanel
          runId={run.id}
          req={current}
          lines={lineDetails.get(current.group_id) ?? []}
          poMembers={current.po_id ? run.requirements.filter((r) => r.po_id === current.po_id) : [current]}
          suppliers={suppliers}
          onChanged={() => loadRun(run.id).then(() => loadRuns())}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
}
