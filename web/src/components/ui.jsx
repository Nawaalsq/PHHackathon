import { useLocation } from "react-router-dom";
import { DECISION_COLORS, sectionAccent } from "../theme.js";
import { useCountUp } from "../hooks.js";

export function Card({ title, caption, children, className = "", glow = false, hover = true }) {
  return (
    <div className={`glass rounded-xl p-4 transition-all duration-200 ${
      hover ? "hover:-translate-y-px hover:shadow-lg hover:shadow-slate-900/10" : ""
    } ${glow ? "ai-glow" : ""} ${className}`}>
      {title && <div className="text-sm font-medium text-zinc-200 mb-1">{title}</div>}
      {caption && <div className="text-xs text-zinc-500 mb-3">{caption}</div>}
      {children}
    </div>
  );
}

export function StatTile({ label, value, sub, color, icon, glowColor, animate = false, delay = 0 }) {
  // Animate numeric values with a count-up; strings render statically.
  const animated = useCountUp(animate && typeof value === "number" ? value : null);
  const shown = animated !== null
    ? Math.round(animated).toLocaleString(undefined, { maximumFractionDigits: 0 })
    : value;
  return (
    <div className="glass rounded-xl px-4 py-3 transition-all duration-200 hover:-translate-y-px min-w-0 overflow-hidden"
         style={glowColor ? { boxShadow: glowColor, animationDelay: `${delay}ms` } : undefined}>
      <div className="flex items-center justify-between gap-2">
        <div className="text-xs text-zinc-500">{label}</div>
        {icon && <span className="opacity-50" style={color ? { color } : undefined}>{icon}</span>}
      </div>
      <div className="text-xl xl:text-2xl font-semibold num mt-0.5 truncate" style={color ? { color } : undefined}>
        {shown}
      </div>
      {sub && <div className="text-xs text-zinc-500 mt-0.5 num">{sub}</div>}
    </div>
  );
}

export function DecisionBadge({ decision }) {
  const c = DECISION_COLORS[decision] ?? "#71717a";
  return (
    <span
      className="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium"
      style={{ color: c, backgroundColor: `${c}1a` }}
    >
      {decision}
    </span>
  );
}

export function Loading({ text = "Loading…" }) {
  return (
    <div className="flex items-center gap-3 text-zinc-500 text-sm py-10 justify-center">
      <span className="h-4 w-4 rounded-full border-2 border-zinc-700 border-t-sky-400 animate-spin" />
      {text}
    </div>
  );
}

export function Skeleton({ className = "" }) {
  return <div className={`skeleton ${className}`} />;
}

// Full-page loading placeholder: KPI row + chart block + table block.
export function SkeletonPage({ tiles = 4 }) {
  return (
    <div className="space-y-4">
      <div className="grid gap-3" style={{ gridTemplateColumns: `repeat(${tiles}, minmax(0, 1fr))` }}>
        {Array.from({ length: tiles }).map((_, i) => <Skeleton key={i} className="h-20" />)}
      </div>
      <Skeleton className="h-64" />
      <Skeleton className="h-48" />
    </div>
  );
}

// One-line "what am I looking at" under the sticky header, on every page.
export function PageIntro({ children }) {
  return <p className="text-xs text-zinc-500 max-w-3xl">{children}</p>;
}

// Small inline bar behind a number, so the eye finds outliers instantly.
export function DataBar({ value, display, max, color = "#0d9488" }) {
  const v = typeof value === "number" ? value : 0;
  const pct = max > 0 ? Math.min(100, (v / max) * 100) : 0;
  return (
    <div className="relative flex items-center w-full min-w-[72px]">
      <div className="absolute inset-y-0 left-0 rounded"
           style={{ width: `${pct}%`, backgroundColor: `${color}22` }} />
      <span className="relative num px-1.5">{display ?? value}</span>
    </div>
  );
}

export function PageHeader({ icon, title, subtitle, actions }) {
  // accent follows the section the user is in
  const { pathname } = useLocation();
  const color = sectionAccent(pathname);
  return (
    <div className="flex items-center justify-between gap-4 mb-1">
      <div className="flex items-center gap-3">
        {icon && (
          <span className="w-9 h-9 rounded-lg flex items-center justify-center shrink-0"
                style={{ backgroundColor: `${color}14`, color, border: `1px solid ${color}30` }}>
            {icon}
          </span>
        )}
        <div>
          <h2 className="text-lg font-semibold tracking-tight leading-tight">{title}</h2>
          {subtitle && <div className="text-xs text-zinc-500 mt-0.5">{subtitle}</div>}
        </div>
      </div>
      {actions}
    </div>
  );
}

export function EmptyState({ text }) {
  return <div className="text-zinc-500 text-sm py-8 text-center">{text}</div>;
}

export function ErrorState({ error }) {
  return (
    <div className="rounded-xl border border-red-900/50 bg-red-950/30 text-red-300 text-sm p-4 my-4">
      {String(error.message || error)}
    </div>
  );
}

// Generic dense table. cols: [{key, label, render?, numeric?, width?}]
export function DataTable({ cols, rows, keyField, onRowClick, selectedKey, rowClassFn, maxHeight = "32rem", empty = "No rows." }) {
  if (!rows?.length) return <EmptyState text={empty} />;
  return (
    <div className="overflow-auto rounded-lg border border-zinc-800/80" style={{ maxHeight }}>
      <table className="w-full text-xs">
        <thead className="sticky top-0 bg-zinc-900">
          <tr className="text-left text-zinc-500 border-b border-zinc-800">
            {cols.map((c) => (
              <th key={c.key} className={`px-3 py-2 font-medium ${c.numeric ? "text-right num" : ""}`}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => {
            const k = keyField ? r[keyField] : i;
            return (
              <tr
                key={k ?? i}
                onClick={onRowClick ? () => onRowClick(r) : undefined}
                className={`border-b border-zinc-800/60 last:border-0 transition-colors ${
                  onRowClick ? "cursor-pointer hover:bg-zinc-800/40" : "hover:bg-zinc-800/20"
                } ${selectedKey !== undefined && k === selectedKey ? "bg-sky-500/10" : ""} ${
                  rowClassFn ? rowClassFn(r) : ""
                }`}
              >
                {cols.map((c) => (
                  <td key={c.key} className={`px-3 py-1.5 align-top ${c.numeric ? "text-right num" : ""}`}>
                    {c.render ? c.render(r) : r[c.key] ?? "-"}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function Pager({ page, pageSize, total, onPage }) {
  const pages = Math.ceil(total / pageSize);
  if (pages <= 1) return null;
  return (
    <div className="flex items-center gap-3 text-xs text-zinc-500 mt-2 num">
      <button
        className="rounded-md border border-zinc-700 px-2 py-1 hover:bg-zinc-800 transition-colors disabled:opacity-30"
        disabled={page <= 1}
        onClick={() => onPage(page - 1)}
      >
        ← Prev
      </button>
      Page {page} / {pages} · {total.toLocaleString()} rows
      <button
        className="rounded-md border border-zinc-700 px-2 py-1 hover:bg-zinc-800 transition-colors disabled:opacity-30"
        disabled={page >= pages}
        onClick={() => onPage(page + 1)}
      >
        Next →
      </button>
    </div>
  );
}

export function RuleTag({ id, clause }) {
  return (
    <span className="inline-block max-w-[28rem]">
      <span className="text-sky-300 num">{id}</span>
      {clause ? <span className="text-zinc-500"> · {clause.slice(0, 90)}</span> : null}
    </span>
  );
}
