import { createContext, useContext, useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { api, setApiUser } from "./api";
import AuditPage from "./pages/AuditPage";
import ChatPage from "./pages/ChatPage";
import ContractsPage from "./pages/ContractsPage";
import PolicyPage from "./pages/PolicyPage";
import SuppliersPage from "./pages/SuppliersPage";
import WorkspacePage from "./pages/WorkspacePage";

export interface User {
  name: string;
  role: "Requester" | "Buyer" | "Procurement";
  department: string;
}

const USERS: User[] = [
  { name: "Ravi Kulkarni", role: "Requester", department: "Maintenance" },
  { name: "Asha Menon", role: "Buyer", department: "Procurement" },
  { name: "Neha Rao", role: "Procurement", department: "Procurement" },
];

const UserContext = createContext<User>(USERS[0]);
export const useUser = () => useContext(UserContext);

const NAV = [
  { to: "/request", label: "Raise a request", hint: "Chat intake" },
  { to: "/workspace", label: "Buyer workspace", hint: "Decide & approve" },
  { to: "/policy", label: "Policy", hint: "Versions & changes" },
  { to: "/suppliers", label: "Preferred suppliers", hint: "Rankings & export" },
  { to: "/contracts", label: "Contracts", hint: "Register & PDFs" },
  { to: "/audit", label: "Audit log", hint: "Who did what" },
];

function loadUser(): User {
  try {
    const saved = localStorage.getItem("corvo.user");
    if (saved) return JSON.parse(saved) as User;
  } catch {
    /* storage unavailable */
  }
  return USERS[1];
}

export default function App() {
  const [user, setUser] = useState<User>(loadUser);
  const [aiOn, setAiOn] = useState<string | null>(null);
  setApiUser(user.name);

  useEffect(() => {
    try {
      localStorage.setItem("corvo.user", JSON.stringify(user));
    } catch {
      /* ignore */
    }
  }, [user]);

  useEffect(() => {
    api.get<{ ai: boolean; ai_provider: string }>("/api/health")
      .then((h) => setAiOn(h.ai ? `on - ${h.ai_provider}` : "off"))
      .catch(() => setAiOn("off"));
  }, []);

  return (
    <UserContext.Provider value={user}>
      <div className="shell">
        <aside className="sidebar">
          <div className="brand">
            <span className="brand-mark">C</span>
            <div>
              <strong>Corvo Procurement</strong>
              <small>PR to PO, by policy</small>
            </div>
          </div>
          <nav>
            {NAV.map((n) => (
              <NavLink key={n.to} to={n.to} className={({ isActive }) => (isActive ? "nav active" : "nav")}>
                <span>{n.label}</span>
                <small>{n.hint}</small>
              </NavLink>
            ))}
          </nav>
          <div className="sidebar-foot">
            <label className="field">
              <span>Acting as</span>
              <select
                value={user.name}
                onChange={(e) => setUser(USERS.find((u) => u.name === e.target.value) ?? USERS[0])}
              >
                {USERS.map((u) => (
                  <option key={u.name} value={u.name}>
                    {u.name} ({u.role})
                  </option>
                ))}
              </select>
            </label>
            <p className="muted small">
              AI reader: {aiOn === null ? "checking..." : aiOn === "off" ? "off (rule-based chat, structured policy editor)" : aiOn}
            </p>
          </div>
        </aside>
        <main className="content">
          <Routes>
            <Route path="/" element={<Navigate to={user.role === "Requester" ? "/request" : "/workspace"} replace />} />
            <Route path="/request" element={<ChatPage />} />
            <Route path="/workspace" element={<WorkspacePage />} />
            <Route path="/workspace/:runId" element={<WorkspacePage />} />
            <Route path="/policy" element={<PolicyPage />} />
            <Route path="/suppliers" element={<SuppliersPage />} />
            <Route path="/contracts" element={<ContractsPage />} />
            <Route path="/audit" element={<AuditPage />} />
          </Routes>
        </main>
      </div>
    </UserContext.Provider>
  );
}
