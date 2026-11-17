import { FormEvent, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useUser } from "../App";
import { ErrorNote, PageHeader, RichText } from "../components/ui";
import { day, inr } from "../format";
import { ChatOption, ChatReply } from "../types";

interface Message {
  role: "user" | "assistant";
  text: string;
}

interface OpenPr {
  pr_no: string;
  pr_line: number;
  created_at: string;
  requester: string;
  plant: string;
  item_code: string;
  description: string;
  quantity: number;
  uom: string;
  need_by_date: string;
  urgency: string;
  status: string;
}

const GREETING =
  'Hi! Tell me what you need, e.g. **"need 200 nitrile gloves for Chakan by next Friday"**. ' +
  "I'll find the item in the item master, ask for anything missing, and raise the PR.";

export default function ChatPage() {
  const user = useUser();
  const [messages, setMessages] = useState<Message[]>([{ role: "assistant", text: GREETING }]);
  const [options, setOptions] = useState<ChatOption[]>([]);
  const [draft, setDraft] = useState<ChatReply["draft"] | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [prs, setPrs] = useState<OpenPr[]>([]);
  const endRef = useRef<HTMLDivElement>(null);

  const loadPrs = () => api.get<OpenPr[]>("/api/prs").then(setPrs).catch(() => undefined);
  useEffect(() => {
    loadPrs();
  }, []);
  // Braces matter: an effect must return nothing or a cleanup function, and newer
  // browsers make scrollIntoView return a value, which React would try to call.
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function send(text: string, option?: string) {
    if (!text.trim() && !option) return;
    setBusy(true);
    setError(null);
    setMessages((m) => [...m, { role: "user", text }]);
    setInput("");
    try {
      const reply = await api.post<ChatReply>("/api/chat", {
        session_id: sessionId,
        message: text,
        option: option ?? null,
        department: user.department,
      });
      setSessionId(reply.session_id);
      setMessages((m) => [...m, { role: "assistant", text: reply.text }]);
      setOptions(reply.options);
      setDraft(reply.draft);
      if (reply.pr) loadPrs();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    send(input);
  }

  function restart() {
    setSessionId(null);
    setMessages([{ role: "assistant", text: GREETING }]);
    setOptions([]);
    setDraft(null);
  }

  const mine = prs.filter((p) => p.requester === user.name);

  return (
    <div className="page">
      <PageHeader
        title="Raise a purchase request"
        subtitle={`Chatting as ${user.name} (${user.department}). Describe what you need in your own words.`}
        actions={<button className="btn ghost" onClick={restart}>New conversation</button>}
      />
      <div className="chat-layout">
        <section className="card chat">
          <div className="messages">
            {messages.map((m, i) => (
              <div key={i} className={`bubble ${m.role}`}>
                <RichText text={m.text} />
              </div>
            ))}
            {busy && <div className="bubble assistant typing">...</div>}
            <div ref={endRef} />
          </div>
          {options.length > 0 && (
            <div className="quick-replies">
              {options.map((o, i) => (
                <button
                  key={o.value}
                  className={i === 0 ? "btn primary" : "btn"}
                  disabled={busy}
                  onClick={() => send(o.label, o.value)}
                >
                  {o.label}
                </button>
              ))}
            </div>
          )}
          <form className="composer" onSubmit={onSubmit}>
            <input
              autoFocus
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Type your request or answer..."
              disabled={busy}
            />
            <button className="btn primary" disabled={busy || !input.trim()}>Send</button>
          </form>
          <ErrorNote error={error} />
        </section>

        <aside className="side-stack">
          <section className="card">
            <h3>Request so far</h3>
            <dl className="kv">
              <dt>Item</dt>
              <dd>
                {draft?.item_code ? (
                  <>
                    <code>{draft.item_code}</code> {draft.item_description}
                  </>
                ) : (
                  <span className="muted">not identified yet</span>
                )}
              </dd>
              <dt>Quantity</dt>
              <dd>{draft?.quantity ? `${draft.quantity} ${draft.uom ?? ""}` : <span className="muted">-</span>}</dd>
              <dt>Plant</dt>
              <dd>{draft?.plant ?? <span className="muted">-</span>}</dd>
              <dt>Need by</dt>
              <dd>{draft?.need_by ? day(draft.need_by) : <span className="muted">-</span>}</dd>
              <dt>Urgency</dt>
              <dd>{draft?.urgency ?? <span className="muted">-</span>}</dd>
              {draft?.item_code === "NEW" && (
                <>
                  <dt>Est. unit price</dt>
                  <dd>{draft.estimated_unit_price ? `INR ${inr(draft.estimated_unit_price)}` : "-"}</dd>
                </>
              )}
            </dl>
          </section>
          <section className="card">
            <h3>My requests</h3>
            {mine.length === 0 ? (
              <p className="muted small">None yet.</p>
            ) : (
              <ul className="pr-list">
                {mine.slice(0, 8).map((p) => (
                  <li key={`${p.pr_no}-${p.pr_line}`}>
                    <div>
                      <strong>{p.pr_no}/{p.pr_line}</strong> <span className="muted">{p.item_code}</span>
                    </div>
                    <div className="small">
                      {p.quantity} {p.uom} {p.description} - {p.plant}, by {day(p.need_by_date)}
                    </div>
                    <span className={`badge status-${p.status === "open" ? "pending" : "approved"}`}>
                      {p.status === "open" ? "waiting for buyer" : "processed"}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </aside>
      </div>
    </div>
  );
}
