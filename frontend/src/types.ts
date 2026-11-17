export interface Supplier {
  supplier_id: string;
  name: string;
  city: string;
  categories: string[];
  status: string;
  on_time_pct: number;
  quality_rating: number;
}

export interface PreferredEntry {
  rank: number;
  supplier_id: string;
  supplier_name: string;
  score: number;
  po_count: number;
  last_po_date: string | null;
  recency: number;
  frequency: number;
  holds_contract: boolean;
  performance: number;
  on_time_pct: number;
  quality_rating: number;
  reference_price: number | null;
  reference_source: string;
}

export interface SuggestedSupplier {
  supplier_id: string;
  name: string;
  on_time_pct: number;
  quality_rating: number;
  source: string;
  reference_price: number | null;
}

export interface ContractReview {
  contract_id: string;
  supplier_id: string;
  rate: number;
  value_at_rate: number;
  ceiling: number;
  remaining_before: number;
  usable: boolean;
}

export interface Requirement {
  group_id: string;
  status: "pending" | "approved" | "overridden";
  document_id: string | null;
  final: null | {
    decision: string;
    route: string;
    supplier_id: string | null;
    unit_price: number | null;
    suggested_supplier_ids: string[];
    comment: string;
    by: string;
    original_route: string;
  };
  item_code: string;
  item_description: string;
  category: string;
  item_type: string;
  uom: string;
  plant: string;
  requirement_date: string;
  created_at: string;
  is_emergency: boolean;
  policy_version: string;
  rule: { id: string; clause: string; title: string } | null;
  decision: string;
  route: string;
  supplier_id: string | null;
  supplier_name: string | null;
  unit_price: number | null;
  contract_id: string | null;
  reference: string | null;
  quantity: number;
  valuation_price: number;
  price_source: string;
  value: number;
  po_id: string | null;
  suggestions: SuggestedSupplier[];
  preferred: PreferredEntry[];
  contract_review: ContractReview[];
  facts: Record<string, unknown>;
  reason: string;
  notes: string[];
  lines: string[];
}

export interface ExportRow {
  pr_no: string;
  pr_line: number;
  resolved_item_code: string;
  group_id: string;
  po_id: string;
  decision: string;
  route: string;
  supplier_id: string;
  unit_price: number | string;
  group_value_inr: number | string;
  suggested_supplier_ids: string;
  policy_version: string;
  reason: string;
}

export interface LineDetail {
  pr_no: string;
  pr_line: number;
  created_at: string;
  requester: string;
  department: string;
  plant: string;
  item_code: string;
  description: string;
  quantity: string;
  uom: string;
  need_by_date: string;
  urgency: string;
  resolved_item_code: string | null;
  match: null | { status: string; method: string; score: number; candidates: { item_code: string; description: string; score: number }[] };
  errors: string[];
  notes: string[];
  requirement_id: string | null;
}

export interface RunSummary {
  id: number;
  name: string;
  source: string;
  created_by: string;
  created_at: string;
  policy_versions: string[];
  summary: {
    lines: number;
    requirements: number;
    purchase_orders: number;
    routes: Record<string, number>;
    not_processed: number;
    total_value: number;
  };
  status_counts: Record<string, number>;
}

export interface RunDetail extends RunSummary {
  requirements: Requirement[];
  lines: { row: ExportRow; detail: LineDetail }[];
}

export interface PolicyChange {
  kind: string;
  text: string;
}

export interface PolicyVersion {
  id: number;
  version: string;
  effective_from: string;
  status: "draft" | "active" | "rejected" | "withdrawn";
  based_on_id: number | null;
  source_kind: string;
  source_name: string;
  summary: string;
  changes: PolicyChange[];
  warnings: string[];
  created_by: string;
  created_at: string;
  decided_by: string | null;
  decided_at: string | null;
  decision_comment: string;
  document?: PolicyDocument;
  source_text?: string;
  plain?: {
    rules: { id: string; clause: string; title: string; text: string }[];
    overrides: { id: string; text: string; description: string }[];
    parameters: Record<string, unknown>;
  };
}

export interface Condition {
  field?: string;
  op?: string;
  value?: unknown;
  all?: Condition[];
  any?: Condition[];
  not?: Condition;
}

export interface Rule {
  id: string;
  clause: string;
  title: string;
  when: Condition;
  outcome: { decision: string; route: string; supplier: string; suggest_count: number; note: string | null };
  enabled: boolean;
}

export interface PolicyDocument {
  version: string;
  effective_from: string;
  title: string;
  summary: string;
  parameters: Record<string, unknown>;
  overrides: unknown[];
  rules: Rule[];
}

export interface Catalogue {
  facts: Record<string, string>;
  ops: string[];
  parameters: Record<string, unknown>;
  decisions: string[];
  supplier_modes: string[];
}

export interface Contract {
  contract_id: string;
  supplier_id: string;
  supplier_name: string;
  start_date: string;
  end_date: string;
  value_cap: number;
  consumed_value: number;
  remaining: number;
  plants: string[];
  source: string;
  added_by: string;
  added_at: string;
  validity: string;
  lines: { item_code: string; description: string; uom: string; rate: number }[];
}

export interface AuditEntry {
  id: number;
  at: string;
  actor: string;
  action: string;
  entity: string;
  entity_id: string;
  comment: string;
  details: Record<string, unknown>;
}

export interface DocumentRecord {
  id: string;
  kind: string;
  run_id: number;
  group_ids: string[];
  supplier_id: string | null;
  suppliers_invited: string[];
  plant: string;
  value: number;
  payload: Record<string, unknown>;
  created_by: string;
  created_at: string;
}

export interface ChatOption {
  label: string;
  value: string;
}

export interface ChatReply {
  session_id: string;
  text: string;
  options: ChatOption[];
  pr: { pr_no: string; pr_line: number } | null;
  stage: string;
  draft: {
    item_code: string | null;
    item_description: string | null;
    quantity: number | null;
    uom: string | null;
    plant: string | null;
    need_by: string | null;
    urgency: string | null;
    estimated_unit_price: number | null;
    pr_no: string | null;
    line_no: number;
  };
}
