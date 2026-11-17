import { ReactNode } from "react";
import { ROUTE_LABEL, routeClass } from "../format";

export function RouteBadge({ route }: { route: string }) {
  return <span className={`badge ${routeClass(route)}`}>{ROUTE_LABEL[route] ?? route}</span>;
}

export function StatusBadge({ status }: { status: string }) {
  return <span className={`badge status-${status}`}>{status}</span>;
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <header className="page-header">
      <div>
        <h1>{title}</h1>
        {subtitle && <p className="muted">{subtitle}</p>}
      </div>
      {actions && <div className="actions">{actions}</div>}
    </header>
  );
}

export function ErrorNote({ error }: { error: string | null }) {
  return error ? <div className="note error">{error}</div> : null;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

/** Bold text written as **like this** in assistant messages. */
export function RichText({ text }: { text: string }) {
  return (
    <>
      {text.split("\n").map((para, i) => (
        <p key={i}>
          {para.split(/(\*\*[^*]+\*\*)/g).map((part, j) =>
            part.startsWith("**") ? <strong key={j}>{part.slice(2, -2)}</strong> : <span key={j}>{part}</span>,
          )}
        </p>
      ))}
    </>
  );
}
