import React, { useState } from "react";
import { Routes, Route, useLocation, useNavigate } from "react-router-dom";
import Header from "./layout/Header.jsx";
import Tour from "./components/Tour.jsx";
import Overview from "./pages/Overview.jsx";
import ClaimReview from "./pages/ClaimReview.jsx";
import Members from "./pages/Members.jsx";
import FwaQueue from "./pages/FwaQueue.jsx";
import ProviderTriage from "./pages/ProviderTriage.jsx";
import WhatIf from "./pages/WhatIf.jsx";
import RuleDrafting from "./pages/RuleDrafting.jsx";
import Conflicts from "./pages/Conflicts.jsx";
import Rules from "./pages/Rules.jsx";
import Evaluation from "./pages/Evaluation.jsx";
import {
  IconDashboard, IconFileText, IconUser, IconFlag, IconShield, IconArrows,
  IconSparkles, IconAlert, IconList, IconCheck,
} from "./components/icons.jsx";

// Show UI crashes on screen instead of a blank dark page.
class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }
  static getDerivedStateFromError(error) {
    return { error };
  }
  componentDidCatch(error, info) {
    console.error("UI crash:", error, info);
  }
  render() {
    if (this.state.error) {
      return (
        <div className="p-6 m-6 rounded-xl border border-red-900/50 bg-red-950/30 text-red-200 text-sm">
          <div className="font-medium mb-2">UI error:</div>
          <pre className="whitespace-pre-wrap text-xs">{String(this.state.error?.message || this.state.error)}</pre>
          <button className="mt-3 border border-zinc-600 rounded-md px-2 py-1 hover:bg-zinc-800"
                  onClick={() => this.setState({ error: null })}>
            Retry
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

const NAV = [
  { to: "/", label: "Overview", icon: <IconDashboard size={15} /> },
  { to: "/claims", label: "Claim review", icon: <IconFileText size={15} /> },
  { to: "/members", label: "Member profiles", icon: <IconUser size={15} /> },
  { to: "/fwa", label: "FWA queue", icon: <IconFlag size={15} /> },
  { to: "/providers", label: "Provider triage", icon: <IconShield size={15} /> },
  { to: "/whatif", label: "What-if", icon: <IconArrows size={15} /> },
  { to: "/drafting", label: "AI rule drafting", icon: <IconSparkles size={15} /> },
  { to: "/conflicts", label: "Rule conflicts", icon: <IconAlert size={15} /> },
  { to: "/rules", label: "Rules", icon: <IconList size={15} /> },
  { to: "/evaluation", label: "Evaluation", icon: <IconCheck size={15} /> },
];

const TOUR_STEPS = [
  { selector: "[data-tour='nav-groups']", title: "Navigate by workflow",
    text: "Four groups, one demo story: DECIDE (traceable decisions), SIMULATE (test rule changes before go-live), INVESTIGATE (focus reviewer time), GOVERN (scorecard and rule book)." },
  { selector: "[data-tour='hero']", title: "Every decision, traceable to its rule",
    text: "Live numbers from the rules engine: claims decided, by decision. Nothing here is a black box — each decision names the clause that drove it." },
  { selector: "[data-tour='money']", title: "Where the money flows",
    text: "Billed → payable → plan pays → held for review. Denied and rejected claims keep most of their value unpaid." },
  { selector: "[data-tour='charts']", title: "Charts are clickable",
    text: "Click any decision bar to drill into those claims in Claim review. The whole app is one connected drill-down." },
];

export default function App() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const [tour, setTour] = useState(() => !localStorage.getItem("tourDone"));

  const startTour = () => {
    if (pathname !== "/") navigate("/");
    setTour(true);
  };
  const endTour = () => {
    setTour(false);
    localStorage.setItem("tourDone", "1");
  };

  return (
    <div className="min-h-screen">
      <Header nav={NAV} onTour={startTour} />
      <div className="p-6 max-w-[1500px] mx-auto">
        <ErrorBoundary>
          {/* key on pathname re-triggers the mount transition on navigation */}
          <div key={pathname} className="page-enter">
            <Routes>
              <Route path="/" element={<Overview />} />
              <Route path="/claims" element={<ClaimReview />} />
              <Route path="/members" element={<Members />} />
              <Route path="/fwa" element={<FwaQueue />} />
              <Route path="/providers" element={<ProviderTriage />} />
              <Route path="/whatif" element={<WhatIf />} />
              <Route path="/drafting" element={<RuleDrafting />} />
              <Route path="/conflicts" element={<Conflicts />} />
              <Route path="/rules" element={<Rules />} />
              <Route path="/evaluation" element={<Evaluation />} />
            </Routes>
          </div>
        </ErrorBoundary>
      </div>
      {tour && pathname === "/" && <Tour steps={TOUR_STEPS} onDone={endTour} />}
      <footer className="max-w-[1500px] mx-auto px-6 pb-6 pt-2 mt-4 border-t border-zinc-800/60 text-[11px] text-zinc-500 flex flex-wrap gap-x-5 gap-y-1">
        <span className="font-medium text-zinc-400">ClaimLens</span>
        <span>Synthetic CMS data (DE-SynPUF) — simulation on fictional terms only</span>
        <span>A flag is a prompt for review, not a finding</span>
        <span>The rules engine decides — AI drafts and explains</span>
      </footer>
    </div>
  );
}
