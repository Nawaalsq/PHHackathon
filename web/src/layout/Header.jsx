import { useEffect, useState } from "react";
import { useLocation, Link } from "react-router-dom";
import { getJson } from "../api.js";
import { useStore } from "../store.jsx";
import { NAV_GROUPS, sectionAccent } from "../theme.js";
import { IconSettings, IconHelp } from "../components/icons.jsx";

// Immersive header: ClaimLens logo, centered grouped pill nav, controls.
// On non-overview pages a second row shows the page identity + live KPIs.
export default function Header({ nav, onTour }) {
  const { pathname } = useLocation();
  const { dataset, setDataset, keysDir, setKeysDir, summary } = useStore();
  const [datasets, setDatasets] = useState([]);
  const [keyDirs, setKeyDirs] = useState([]);
  const [ai, setAi] = useState(null);
  const [settingsOpen, setSettingsOpen] = useState(false);

  const current = nav.find((n) => n.to === pathname) ?? nav[0];
  const accent = sectionAccent(pathname);

  useEffect(() => {
    getJson("/api/datasets")
      .then((rows) => {
        setDatasets(rows);
        // If the stored dataset isn't served (e.g. local-only dataset while
        // deployed, stale localStorage), fall back to the first precomputed one.
        const readyRows = rows.filter((d) => d.precomputed);
        if (readyRows.length && !readyRows.some((d) => d.id === dataset)) {
          pickDataset(readyRows[0].id);
        }
      })
      .catch(() => {});
    getJson("/api/keys").then((ks) => {
      setKeyDirs(ks);
      // stored keys dir may not exist on this deployment — fall back to a sane default
      setKeysDir((cur) =>
        ks.length && !ks.some((k) => k.id === cur)
          ? (ks.find((k) => k.id === "answer_keys/sample2_seeded" && k.complete) ?? ks.find((k) => k.complete) ?? ks[0]).id
          : cur);
    }).catch(() => {});
    getJson("/api/ai/status").then(setAi).catch(() => {});
  }, [dataset]); // eslint-disable-line react-hooks/exhaustive-deps

  // picking a dataset auto-matches its answer keys: clean/sample1_dev_seeded -> answer_keys/sample1_dev_seeded
  const pickDataset = (id) => {
    setDataset(id);
    const base = id.replace("clean/", "");
    const stem = base.replace(/_(seeded|hard)$/, "");
    const match = keyDirs.find((k) => k.id === `answer_keys/${base}`)
               ?? keyDirs.find((k) => k.id === `answer_keys/${stem}`);
    if (match) setKeysDir(match.id);
  };

  const ready = datasets.filter((d) => d.precomputed);

  return (
    <div className="sticky top-0 z-20 bg-zinc-950/80 backdrop-blur border-b border-zinc-800/70">
      <div className="max-w-[1500px] mx-auto px-5 py-2.5">
        {/* row 1: logo · grouped pill nav · controls */}
        <div className="flex items-center gap-4">
          {/* logo */}
          <Link to="/" className="flex items-center gap-2.5 shrink-0 group">
            <span className="icon-chip w-9 h-9 rounded-xl">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                   strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" /><path d="m9 12 2 2 4-4" />
              </svg>
            </span>
            <span className="text-lg font-semibold tracking-tight hero-text">ClaimLens</span>
          </Link>

          <div className="flex-1" />

          {/* grouped pill nav */}
          <nav data-tour="nav-groups" className="hidden lg:flex items-center gap-3">
            {NAV_GROUPS.map((g) => (
              <div key={g.label} className="flex items-center gap-1">
                <span className="text-[10px] uppercase tracking-wider font-semibold text-zinc-500 mr-0.5">
                  {g.label}
                </span>
                {g.paths.map((p) => {
                  const item = nav.find((n) => n.to === p);
                  if (!item) return null;
                  const active = pathname === p;
                  const c = sectionAccent(p);
                  return (
                    <Link key={p} to={p} title={item.label}
                          className={`rounded-full px-2.5 py-1 text-xs transition-all ${
                            active ? "font-medium" : "text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800/60"
                          }`}
                          style={active ? { backgroundColor: `${c}18`, color: c, border: `1px solid ${c}45` }
                            : { border: "1px solid transparent" }}>
                      {item.label}
                    </Link>
                  );
                })}
                <span className="w-px h-4 bg-zinc-800 mx-1 last:hidden" />
              </div>
            ))}
          </nav>

          <div className="flex-1" />

          {/* controls */}
          <div className="flex items-center gap-2">
            {/* dataset selector, visible in the header (also in the settings popup) */}
            {ready.length > 0 && (
              <label className="hidden md:flex items-center gap-1.5 text-[10px] uppercase tracking-wider font-semibold text-zinc-500">
                Data
                <select value={dataset} onChange={(e) => pickDataset(e.target.value)}
                        className="bg-zinc-900 border border-zinc-700 rounded-md px-1.5 py-1 text-xs normal-case tracking-normal text-zinc-200 max-w-[180px]">
                  {ready.map((d) => (
                    <option key={d.id} value={d.id}>{d.id.replace("clean/", "")}</option>
                  ))}
                </select>
              </label>
            )}
            <div className="flex items-center gap-2 text-xs text-zinc-500">
              <span className={`h-2 w-2 rounded-full ${ai?.ok ? "bg-emerald-400" : "bg-zinc-600"}`}
                    style={ai?.ok ? { animation: "pulse-dot 2s ease-in-out infinite" } : undefined}
                    title={ai ? `AI: ${ai.backend}` : "AI status…"} />
              <span className="hidden xl:inline">{ai?.backend ?? "…"}</span>
            </div>
            <button onClick={() => onTour?.()} title="Take the tour"
                    className="w-8 h-8 rounded-lg border border-zinc-700 text-zinc-400 hover:text-teal-700 hover:bg-teal-500/10 flex items-center justify-center transition-colors">
              <IconHelp size={15} />
            </button>
            <div className="relative">
              <button onClick={() => setSettingsOpen((o) => !o)} title="Settings"
                      className={`w-8 h-8 rounded-lg border flex items-center justify-center transition-colors ${
                        settingsOpen ? "border-teal-900/60 bg-teal-500/10 text-teal-700" : "border-zinc-700 text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/60"
                      }`}>
                <IconSettings size={15} />
              </button>
              {settingsOpen && (
                <div className="absolute right-0 top-10 w-72 glass rounded-xl p-3 shadow-xl shadow-slate-900/10 z-30">
                  <div className="text-xs font-medium text-zinc-200 mb-2">Dataset</div>
                  <select value={dataset} onChange={(e) => pickDataset(e.target.value)}
                          className="w-full bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1.5 text-xs text-zinc-200 mb-3">
                    {ready.map((d) => (
                      <option key={d.id} value={d.id}>{d.id.replace("clean/", "")}</option>
                    ))}
                  </select>
                  <div className="text-xs font-medium text-zinc-200 mb-2">Answer keys (evaluation only)</div>
                  {keyDirs.length ? (
                    <select value={keysDir} onChange={(e) => setKeysDir(e.target.value)}
                            className="w-full bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1.5 text-xs text-zinc-200">
                      {keyDirs.map((k) => (
                        <option key={k.id} value={k.id}>
                          {k.id.replace("answer_keys/", "")}{k.complete ? "" : " (missing CSVs)"}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <input value={keysDir} onChange={(e) => setKeysDir(e.target.value)}
                           placeholder="answer_keys/sample2_seeded"
                           className="w-full bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1.5 text-xs text-zinc-200" />
                  )}
                  <div className="text-[11px] text-zinc-500 mt-2">
                    Used by the Evaluation tab and the FWA workload curve. The engine never reads these.
                  </div>
                  <button onClick={() => setSettingsOpen(false)}
                          className="mt-2 text-xs text-sky-300 hover:underline">Done</button>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* row 2 (non-overview): page identity + live KPIs */}
        {pathname !== "/" && (
          <div className="flex items-center gap-3 mt-2 pt-2 border-t border-zinc-800/60">
            <span className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0"
                  style={{ backgroundColor: `${accent}14`, color: accent, border: `1px solid ${accent}30` }}>
              {current.icon}
            </span>
            <h1 className="text-base font-semibold tracking-tight">{current.label}</h1>
            <div className="flex-1" />
            {summary && (
              <div className="hidden md:flex items-center gap-2 text-xs text-zinc-500 num">
                <span className="glass rounded-full px-3 py-1">
                  <span className="text-zinc-200 font-semibold">{(summary.claims ?? 0).toLocaleString()}</span> claims
                </span>
                <span className="glass rounded-full px-3 py-1">
                  <span className="text-zinc-200 font-semibold">${((summary.billed ?? 0) / 1e6).toFixed(1)}M</span> billed
                </span>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
