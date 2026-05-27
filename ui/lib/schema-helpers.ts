/**
 * Display helpers for schema names and domain categorization.
 *
 * The system creates schemas with names like `auto:bank_statement`,
 * `auto:master_services_agreement`, `auto:comprehensive_metabolic_panel`.
 * That's implementation noise — a layman doesn't need to see the
 * `auto:` prefix and snake_case in the UI.
 *
 * `humanizeSchemaName` produces a Title-Case readable name.
 * `categorizeSchema` returns a domain bucket by keyword heuristic so
 * the Knowledge Map can group the 35-schema list into something
 * scannable (Legal / Finance / HR / Medical / Engineering /
 * Communications / Reports / Dev).
 *
 * Both are pure functions of the schema's stored `name` — no API
 * dependency.
 */

/**
 * Acronyms that should render in ALL CAPS rather than Title Case.
 * Explicit whitelist — heuristics like "uppercase any short token"
 * mis-fire on common words ("bank", "job", "case", "test" are NOT
 * acronyms).
 */
const ACRONYMS = new Set([
  "rfc", "msa", "nda", "eob", "api", "sql", "id", "ip", "ui", "ux",
  "ai", "ml", "qa", "io", "url", "uri", "uuid", "ssn", "pii", "rs",
  "pdf", "pii", "kb", "k8s", "q1", "q2", "q3", "q4", "ytd", "mom",
  "yoy", "sla", "kpi", "okr", "ceo", "cto", "cfo", "hr", "it", "os",
]);

/**
 * `auto:master_services_agreement` → "Master Services Agreement".
 * `auto:rfc` → "RFC" (preserves known acronyms).
 * `master_services_agreement` (no prefix) → "Master Services Agreement".
 * Empty / null → "Untitled".
 */
export function humanizeSchemaName(name: string | null | undefined): string {
  if (!name) return "Untitled";
  const stripped = name.replace(/^auto:/, "");
  return stripped
    .split("_")
    .map((part) => {
      const lower = part.toLowerCase();
      if (ACRONYMS.has(lower)) return part.toUpperCase();
      return part.charAt(0).toUpperCase() + part.slice(1);
    })
    .join(" ");
}


export type SchemaDomain =
  | "legal"
  | "finance"
  | "hr"
  | "medical"
  | "engineering"
  | "communications"
  | "reports"
  | "dev";

export interface DomainMeta {
  key: SchemaDomain;
  label: string;
  emoji: string;
  /** Sort order — Legal first because that's the demo corpus's heart;
   *  Dev artifacts last (collapsed by default). */
  order: number;
  description: string;
}

export const DOMAINS: Record<SchemaDomain, DomainMeta> = {
  legal:          { key: "legal",          label: "Legal & Contracts",  emoji: "📜", order: 1, description: "MSAs, NDAs, amendments, subscriptions, anything contract-shaped." },
  finance:        { key: "finance",        label: "Finance",            emoji: "💰", order: 2, description: "Bank statements, invoices, expense reports, financial summaries." },
  hr:             { key: "hr",             label: "People & HR",        emoji: "👥", order: 3, description: "Offer letters, resumes, performance reviews, job postings." },
  medical:        { key: "medical",        label: "Medical",            emoji: "🩺", order: 4, description: "Lab panels, discharge summaries, explanation-of-benefits." },
  engineering:    { key: "engineering",    label: "Engineering",        emoji: "🛠",  order: 5, description: "RFCs, bug reports, incident postmortems, technical designs." },
  communications: { key: "communications", label: "Communications",     emoji: "✉️",  order: 6, description: "Emails, meeting notes, press releases, internal threads." },
  reports:        { key: "reports",        label: "Reports & Other",    emoji: "📰", order: 7, description: "Case studies, announcements, anything that doesn't fit the other buckets." },
  dev:            { key: "dev",            label: "Dev / test artifacts", emoji: "🧪", order: 99, description: "Test docs from development runs. Collapsed by default." },
};

/**
 * Bucket a schema name into one of the visible domains by keyword
 * matching against the stem (after stripping `auto:`).
 *
 * Match order matters — earlier rules win. Dev/test rules come FIRST
 * so `test_document` doesn't accidentally land in Reports; Legal
 * comes before Finance so `subscription_agreement` lands in Legal,
 * not Finance.
 *
 * Match by tokenized parts (split on `_`) rather than regex with
 * `\b` boundaries — JS regex word-boundary treats `_` as a word
 * char, so `\bamendment\b` doesn't match `amendment_negotiation`.
 */
export function categorizeSchema(name: string | null | undefined): SchemaDomain {
  const stem = (name ?? "").replace(/^auto:/, "").toLowerCase();
  const parts = new Set(stem.split(/[_-]+/).filter(Boolean));
  const has = (...keywords: string[]) => keywords.some((k) => parts.has(k));
  const stemHas = (...substrs: string[]) => substrs.some((s) => stem.includes(s));

  // Dev / test artifacts — match first so test_document doesn't end
  // up in Reports.
  if (has("test", "tiny", "sample", "fixture", "demo") || stemHas("resolver", "offset_citation")) {
    return "dev";
  }

  // Legal — contracts and contract-adjacent. Matches BEFORE Finance
  // so `subscription_agreement` lands here.
  if (has("agreement", "msa", "nda", "amendment", "contract", "negotiation", "subscription", "license", "legal") || stemHas("non_disclosure", "nondisclosure")) {
    return "legal";
  }

  // Finance
  if (has("bank", "invoice", "expense", "financial", "finance", "statement", "transaction", "ledger", "billing", "payment", "funding", "revenue", "pricing")) {
    return "finance";
  }

  // People / HR
  if (has("offer", "resume", "job", "hire", "hiring", "employee", "employment", "interview", "recruit", "posting") || stemHas("performance_review")) {
    return "hr";
  }

  // Medical
  if (has("lab", "discharge", "panel", "eob", "medical", "patient", "clinical", "metabolic", "diagnosis", "prescription", "benefits")) {
    return "medical";
  }

  // Engineering — bug / incident / rfc / postmortem are the real
  // engineering doc-types.
  if (has("rfc", "bug", "incident", "postmortem", "design", "spec", "technical", "architecture", "engineering")) {
    return "engineering";
  }

  // Communications — emails, meetings, anything thread-shaped.
  if (has("email", "meeting", "thread", "note", "notes", "memo", "message", "announcement", "press") || stemHas("press_release")) {
    return "communications";
  }

  // Reports — catchall (case_study, summary, vendor_evaluation, etc.).
  return "reports";
}


/**
 * Sorted list of visible domains EXCLUDING `dev`. Iterate this to
 * build the grouped catalog; render `dev` separately as a collapsed
 * footer.
 */
export const VISIBLE_DOMAINS: SchemaDomain[] = (
  Object.values(DOMAINS)
    .filter((d) => d.key !== "dev")
    .sort((a, b) => a.order - b.order)
    .map((d) => d.key)
);


/** Format a Postgres timestamp into a relative-time string ("3 days
 *  ago", "2 minutes ago"). Falls back to ISO date for distant past. */
export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const diff = Date.now() - then;
  const minutes = Math.floor(diff / 60_000);
  const hours = Math.floor(diff / 3_600_000);
  const days = Math.floor(diff / 86_400_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  if (days < 7) return `${days} day${days === 1 ? "" : "s"} ago`;
  // Beyond a week, fall back to a calendar date.
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric", month: "short", day: "numeric",
  });
}
