import { ChangeEvent, useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { useUser } from "../App";
import { Operation, OperationsBuilder } from "../components/PolicyEditor";
import { ErrorNote, PageHeader, StatusBadge } from "../components/ui";
import { dateTime, day } from "../format";
import { Catalogue, PolicyVersion } from "../types";

interface VersionList {
  versions: PolicyVersion[];
  ai_reader: boolean;
  in_force_today: string;
}

type Source = "upload" | "describe" | "editor" | "json";

export default function PolicyPage() {
  const user = useUser();
  const [list, setList] = useState<VersionList | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [version, setVersion] = useState<PolicyVersion | null>(null);
  const [catalogue, setCatalogue] = useState<Catalogue | null>(null);
  const [proposing, setProposing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async (select?: number) => {
    const l = await api.get<VersionList>("/api/policies");
    setList(l);
    const target = select ?? l.versions.find((v) => v.status === "draft")?.id ?? l.versions.find((v) => v.status === "active")?.id;
    if (target) setSelectedId(target);
  }, []);

  useEffect(() => {
    reload();
    api.get<Catalogue>("/api/policies/catalogue").then(setCatalogue);
  }, [reload]);

  useEffect(() => {
    if (selectedId) api.get<PolicyVersion>(`/api/policies/${selectedId}`).then(setVersion).catch((e) => setError(e.message));
  }, [selectedId]);

  const latestActive = list?.versions.filter((v) => v.status === "active").sort((a, b) => a.effective_from.localeCompare(b.effective_from)).at(-1);

  return (
    <div className="page">
      <PageHeader
        title="Procurement policy"
        subtitle={list ? `In force today: v${list.in_force_today}. Each requirement is decided by the version in force on its requirement date.` : ""}
        actions={<button className="btn primary" onClick={() => setProposing(true)}>Propose a change</button>}
      />
      <ErrorNote error={error} />
      <div className="policy-layout">
        <aside className="card versions">
          <h3>Versions</h3>
          <ul>
            {list?.versions.map((v) => (
              <li key={v.id} className={v.id === selectedId ? "on" : ""} onClick={() => { setSelectedId(v.id); setProposing(false); }}>
                <div className="row between">
                  <strong>v{v.version}</strong>
                  <StatusBadge status={v.status} />
                </div>
                <div className="small">effective {day(v.effective_from)}</div>
                <div className="small muted">{v.source_kind} · {v.created_by}</div>
              </li>
            ))}
          </ul>
        </aside>
        <section className="policy-main">
          {proposing && catalogue && latestActive ? (
            <ProposeChange
              aiReader={!!list?.ai_reader}
              catalogue={catalogue}
              baseId={latestActive.id}
              onCreated={(id) => { setProposing(false); reload(id); }}
              onCancel={() => setProposing(false)}
            />
          ) : version ? (
            <VersionView
              version={version}
              actor={user.name}
              onDecided={() => reload(version.id).then(() => api.get<PolicyVersion>(`/api/policies/${version.id}`).then(setVersion))}
            />
          ) : null}
        </section>
      </div>
    </div>
  );
}

function VersionView({ version, actor, onDecided }: { version: PolicyVersion; actor: string; onDecided: () => void }) {
  const [effective, setEffective] = useState(version.effective_from);
  const [comment, setComment] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [showSource, setShowSource] = useState(false);
  useEffect(() => {
    setEffective(version.effective_from);
  }, [version]);

  async function decide(action: "approve" | "reject" | "withdraw") {
    setError(null);
    try {
      await api.post(`/api/policies/${version.id}/${action}`, { effective_from: action === "approve" ? effective : null, comment });
      onDecided();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const isDraft = version.status === "draft";
  return (
    <div className="stack">
      <div className="card">
        <div className="row between">
          <div>
            <h2>Version {version.version} <StatusBadge status={version.status} /></h2>
            <p className="muted">{version.summary}</p>
            <p className="small muted">
              From {version.source_kind}{version.source_name && ` (${version.source_name})`} by {version.created_by}, {dateTime(version.created_at)}
              {version.decided_by && ` · ${version.status} by ${version.decided_by}${version.decided_at ? `, ${dateTime(version.decided_at)}` : ""}`}
              {version.decision_comment && ` - “${version.decision_comment}”`}
            </p>
          </div>
        </div>

        {(isDraft || version.source_kind !== "seed") && (
          <>
            <h3>{isDraft ? "What will change" : "What changed"}</h3>
            {version.changes.length === 0 ? <p className="muted">No differences from the version it is based on.</p> : (
              <ul className="changes">
                {version.changes.map((c, i) => <li key={i} className={`change ${c.kind}`}>{c.text}</li>)}
              </ul>
            )}
            {version.warnings.map((w, i) => <div key={i} className="note warn">{w}</div>)}
            {version.source_text && (
              <button className="btn ghost small" onClick={() => setShowSource(!showSource)}>{showSource ? "Hide" : "Show"} source text</button>
            )}
            {showSource && <pre className="source">{version.source_text}</pre>}
          </>
        )}

        {isDraft && (
          <div className="approve-box">
            <label className="field">
              <span>Effective from</span>
              <input type="date" value={effective} onChange={(e) => setEffective(e.target.value)} />
            </label>
            <label className="field wide">
              <span>Comment</span>
              <input value={comment} onChange={(e) => setComment(e.target.value)} placeholder="e.g. Approved per CPO mail of 18 Nov" />
            </label>
            <div className="row gap">
              <button className="btn primary big" onClick={() => decide("approve")}>Approve as {actor} and activate from {day(effective)}</button>
              <button className="btn" onClick={() => decide("reject")}>Reject</button>
            </div>
          </div>
        )}
        {version.status === "active" && version.source_kind !== "seed" && (
          <details className="small">
            <summary>Withdraw this version</summary>
            <div className="row gap">
              <input value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Reason (required)" />
              <button className="btn" disabled={!comment.trim()} onClick={() => decide("withdraw")}>Withdraw</button>
            </div>
          </details>
        )}
        <ErrorNote error={error} />
      </div>

      {version.plain && (
        <>
          <div className="card">
            <h3>Decision rules, in order (first rule that fits decides)</h3>
            <ol className="rules">
              {version.plain.rules.map((r) => <li key={r.id}><strong>{r.title}</strong><div className="small">{r.text}</div></li>)}
            </ol>
            {version.plain.overrides.length > 0 && (
              <>
                <h3>Conditional settings</h3>
                <ul>{version.plain.overrides.map((o) => <li key={o.id}>{o.text}</li>)}</ul>
              </>
            )}
          </div>
          <details className="card">
            <summary><strong>All settings</strong></summary>
            <table className="table compact">
              <tbody>
                {Object.entries(version.plain.parameters).map(([k, v]) => (
                  <tr key={k}><td><code>{k}</code></td><td>{JSON.stringify(v)}</td></tr>
                ))}
              </tbody>
            </table>
          </details>
        </>
      )}
    </div>
  );
}

function ProposeChange({ aiReader, catalogue, baseId, onCreated, onCancel }: {
  aiReader: boolean; catalogue: Catalogue; baseId: number; onCreated: (id: number) => void; onCancel: () => void;
}) {
  const [source, setSource] = useState<Source>(aiReader ? "upload" : "editor");
  const [text, setText] = useState("");
  const [base, setBase] = useState<PolicyVersion | null>(null);
  const [ops, setOps] = useState<Operation[]>([]);
  const [meta, setMeta] = useState({ version: "", effective_from: new Date().toISOString().slice(0, 10), summary: "" });
  const [json, setJson] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.get<PolicyVersion>(`/api/policies/${baseId}`).then((b) => {
      setBase(b);
      setJson(JSON.stringify(b.document, null, 2));
    });
  }, [baseId]);

  async function run(fn: () => Promise<PolicyVersion>) {
    setBusy(true);
    setError(null);
    try {
      onCreated((await fn()).id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function onFile(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    run(() => api.upload("/api/policies/drafts/upload", form));
    e.target.value = "";
  }

  return (
    <div className="card stack">
      <div className="row between">
        <h2>Propose a policy change</h2>
        <button className="btn ghost" onClick={onCancel}>Cancel</button>
      </div>
      <p className="muted small">
        Changes are based on v{base?.version}. Nothing takes effect until a draft is reviewed and approved with an effective date.
      </p>
      <div className="segmented">
        <button className={source === "upload" ? "on" : ""} onClick={() => setSource("upload")}>Upload document</button>
        <button className={source === "describe" ? "on" : ""} onClick={() => setSource("describe")}>Describe in words</button>
        <button className={source === "editor" ? "on" : ""} onClick={() => setSource("editor")}>Structured editor</button>
        <button className={source === "json" ? "on" : ""} onClick={() => setSource("json")}>Edit JSON</button>
      </div>
      {!aiReader && (source === "upload" || source === "describe") && (
        <div className="note warn">The AI reader is not configured on this server (set GEMINI_API_KEY for the free Gemini tier, or ANTHROPIC_API_KEY). Use the structured editor meanwhile.</div>
      )}

      {source === "upload" && (
        <label className="dropzone">
          <input type="file" accept=".pdf,.docx,.txt,.md" onChange={onFile} disabled={busy} hidden />
          <strong>{busy ? "Reading the document…" : "Choose an amendment or new policy (.pdf, .docx, .txt)"}</strong>
          <span className="small muted">The reader proposes the changes; you review them in plain language before approving.</span>
        </label>
      )}
      {source === "describe" && (
        <>
          <textarea rows={5} value={text} onChange={(e) => setText(e.target.value)}
            placeholder={"e.g. From 1 Dec 2026, at Chakan (CHK1) Safety & PPE items up to 3,00,000 can be bought on a spot PO if bought before. Version 5.1."} />
          <button className="btn primary" disabled={busy || !text.trim()} onClick={() => run(() => api.post("/api/policies/drafts/chat", { text }))}>
            {busy ? "Reading…" : "Propose this change"}
          </button>
        </>
      )}
      {source === "editor" && base?.document && (
        <>
          <OperationsBuilder rules={base.document.rules} catalogue={catalogue} operations={ops} setOperations={setOps} />
          {ops.length > 0 && (
            <div>
              <h4>Changes in this draft</h4>
              <ol className="ops-list">
                {ops.map((o, i) => (
                  <li key={i}>
                    <code>{o.op}</code> {String(o.path ?? o.rule_id ?? (o.rule as { clause?: string })?.clause ?? (o.override as { id?: string })?.id ?? "")}
                    {"value" in o && ` = ${JSON.stringify(o.value)}`}
                    <button className="btn ghost small" onClick={() => setOps(ops.filter((_, j) => j !== i))}>remove</button>
                  </li>
                ))}
              </ol>
            </div>
          )}
          <div className="form-grid">
            <label className="field"><span>New version</span><input value={meta.version} placeholder="auto (next minor)" onChange={(e) => setMeta({ ...meta, version: e.target.value })} /></label>
            <label className="field"><span>Effective from</span><input type="date" value={meta.effective_from} onChange={(e) => setMeta({ ...meta, effective_from: e.target.value })} /></label>
            <label className="field wide"><span>Summary</span><input value={meta.summary} onChange={(e) => setMeta({ ...meta, summary: e.target.value })} /></label>
          </div>
          <button className="btn primary" disabled={busy || ops.length === 0}
            onClick={() => run(() => api.post("/api/policies/drafts/operations", { operations: ops, version: meta.version || null, effective_from: meta.effective_from, summary: meta.summary }))}>
            Create draft for review
          </button>
        </>
      )}
      {source === "json" && (
        <>
          <p className="small muted">Edit the full policy document. Change <code>version</code> and <code>effective_from</code>. It is validated before the draft is created.</p>
          <textarea className="code" rows={24} value={json} onChange={(e) => setJson(e.target.value)} />
          <button className="btn primary" disabled={busy} onClick={() => run(async () => {
            let document: unknown;
            try { document = JSON.parse(json); } catch (e) { throw new Error(`Invalid JSON: ${(e as Error).message}`); }
            return api.post<PolicyVersion>("/api/policies/drafts/document", { document });
          })}>Create draft for review</button>
        </>
      )}
      <ErrorNote error={error} />
    </div>
  );
}
