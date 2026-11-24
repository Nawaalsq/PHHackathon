// Decision colors, used consistently in badges, chart series and table rows.
// Re-stepped to pass CVD/normal-vision separation (validated, light surface):
// pended moved orange->violet, rejected moved gray->sky so adjacent pairs read apart.
export const DECISION_COLORS = {
  paid: "#10b981",     // emerald-500
  partial: "#f59e0b",  // amber-500
  pended: "#8b5cf6",   // violet-500
  denied: "#ef4444",   // red-500
  rejected: "#0ea5e9", // sky-500
};

export const DECISIONS = ["paid", "partial", "pended", "denied", "rejected"];

// Accent for interactive elements and single-series charts.
export const ACCENT = "#0d9488"; // teal-600

// Chart chrome on light surfaces.
export const CHART = {
  grid: "#d7ebe7",     // teal-tinted light
  tick: "#5f837e",     // muted teal
  tooltipBg: "#ffffff",
  border: "#cce4de",   // teal-tinted border
};

// Glow shadows for accent elements (cards, tiles, AI panels) on light surfaces.
export const GLOW = {
  sky: "0 0 0 3px rgba(13, 148, 136, 0.07), 0 6px 20px rgba(13, 148, 136, 0.10)",
  violet: "0 0 0 3px rgba(139, 92, 246, 0.07), 0 6px 20px rgba(139, 92, 246, 0.10)",
  emerald: "0 0 0 3px rgba(16, 185, 129, 0.07), 0 6px 20px rgba(16, 185, 129, 0.10)",
  red: "0 0 0 3px rgba(239, 68, 68, 0.07), 0 6px 20px rgba(239, 68, 68, 0.10)",
  amber: "0 0 0 3px rgba(217, 119, 6, 0.08), 0 6px 20px rgba(217, 119, 6, 0.10)",
};

// Money: full below 1M, compact above — 950 | 12,345 | 839.2M | 1.2B
export const fmtMoney = (x) => {
  if (x === null || x === undefined) return "-";
  const n = Number(x);
  if (!Number.isFinite(n)) return "-";
  const a = Math.abs(n);
  if (a >= 1e9) return `${n < 0 ? "-" : ""}${(a / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `${n < 0 ? "-" : ""}${(a / 1e6).toFixed(1)}M`;
  return n.toLocaleString(undefined, { maximumFractionDigits: 0 });
};

// Compact money for narrow tiles: $950 | $12.4K | $839.2M | $1.2B
export const fmtMoneyCompact = (x) => {
  if (x === null || x === undefined) return "-";
  const n = Number(x);
  if (!Number.isFinite(n)) return "-";
  const a = Math.abs(n);
  if (a >= 1e9) return `${n < 0 ? "-" : ""}$${(a / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `${n < 0 ? "-" : ""}$${(a / 1e6).toFixed(1)}M`;
  if (a >= 1e4) return `${n < 0 ? "-" : ""}$${(a / 1e3).toFixed(1)}K`;
  return n.toLocaleString(undefined, { maximumFractionDigits: 0 });
};

// Generic compact number (counts and money, tooltips/labels): 950 | 15.2K | 200.1M | 1.2B
export const fmtCompact = (x) => {
  if (x === null || x === undefined) return "-";
  const n = Number(x);
  if (!Number.isFinite(n)) return "-";
  const a = Math.abs(n);
  if (a >= 1e9) return `${n < 0 ? "-" : ""}${(a / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `${n < 0 ? "-" : ""}${(a / 1e6).toFixed(1)}M`;
  if (a >= 1e4) return `${n < 0 ? "-" : ""}${(a / 1e3).toFixed(1)}K`;
  return n.toLocaleString(undefined, { maximumFractionDigits: 0 });
};

export const fmtPct = (x, digits = 1) =>
  x === null || x === undefined ? "-" : `${(x * 100).toFixed(digits)}%`;

// Per-page accent hue: rail active state, command-bar chip, page headers.
// Colors follow the four workflow groups: DECIDE teal, SIMULATE violet,
// INVESTIGATE amber, GOVERN green.
export const SECTION_ACCENTS = {
  "/": { name: "Overview", color: "#0d9488" },         // teal-600
  "/claims": { name: "Claim review", color: "#0f766e" },    // teal-700
  "/members": { name: "Member profiles", color: "#14b8a6" }, // teal-500
  "/whatif": { name: "What-if", color: "#6366f1" },         // indigo-500
  "/drafting": { name: "AI rule drafting", color: "#7c3aed" }, // violet-600
  "/conflicts": { name: "Rule conflicts", color: "#8b5cf6" },  // violet-500
  "/fwa": { name: "FWA queue", color: "#d97706" },          // amber-600
  "/providers": { name: "Provider triage", color: "#ca8a04" }, // yellow-600
  "/rules": { name: "Rules", color: "#0f766e" },            // teal-700
  "/evaluation": { name: "Evaluation", color: "#059669" },  // emerald-600
};

export const sectionAccent = (path) => SECTION_ACCENTS[path]?.color ?? "#0d9488";

// Navigation grouped by workflow — each group is one demo beat.
export const NAV_GROUPS = [
  { label: "Decide", pitch: "Every decision traceable", paths: ["/", "/claims", "/members"] },
  { label: "Simulate", pitch: "Test changes before go-live", paths: ["/whatif", "/drafting", "/conflicts"] },
  { label: "Investigate", pitch: "Focus reviewer time", paths: ["/fwa", "/providers"] },
  { label: "Govern", pitch: "Scorecard and rule book", paths: ["/rules", "/evaluation"] },
];
