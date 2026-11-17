/** Indian digit grouping, no decimals: 1234567 -> "12,34,567". */
export function inr(value: number | string | null | undefined, decimals = 0): string {
  if (value === null || value === undefined || value === "") return "-";
  const n = typeof value === "string" ? Number(value) : value;
  if (Number.isNaN(n)) return String(value);
  return n.toLocaleString("en-IN", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

/** Prices keep paise when they have them. */
export function price(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "-";
  const n = Number(value);
  return n.toLocaleString("en-IN", { maximumFractionDigits: 2 });
}

/** Server timestamps (runs, audit, policy actions) are UTC without a zone suffix. */
export function dateTime(iso: string): string {
  const d = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`);
  return d.toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function day(iso: string | null | undefined): string {
  if (!iso) return "-";
  return new Date(iso).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
}

export const ROUTE_LABEL: Record<string, string> = {
  CONTRACT_PO: "Contract PO",
  EMERGENCY_PO: "Emergency PO",
  SPOT_PO: "Spot PO",
  RFQ: "RFQ",
  RFP: "RFP",
  CAPEX_SOURCING: "Capex sourcing",
  NEW_ITEM_SOURCING: "New item sourcing",
  NOT_PROCESSED: "Not processed",
};

export const routeClass = (route: string) =>
  route.endsWith("_PO") ? "po" : route === "NOT_PROCESSED" ? "np" : "src";
