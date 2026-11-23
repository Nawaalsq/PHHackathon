import { useEffect, useState } from "react";

// Spotlight tour overlay: dims everything except the element matching each
// step's selector, with a floating explanation card. No dependencies.
export default function Tour({ steps, onDone }) {
  const [i, setI] = useState(0);
  const [rect, setRect] = useState(null);
  const step = steps[i];

  useEffect(() => {
    if (!step) return undefined;
    const el = document.querySelector(step.selector);
    const update = () => {
      if (!el) { setRect(null); return; }
      const r = el.getBoundingClientRect();
      setRect({ top: r.top, left: r.left, width: r.width, height: r.height });
    };
    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [step]);

  if (!step) return null;
  const last = i === steps.length - 1;

  // card placement: below the target, else above, else centered
  let cardStyle;
  if (rect && rect.top + rect.height + 190 < window.innerHeight) {
    cardStyle = { top: rect.top + rect.height + 12, left: Math.max(16, rect.left), maxWidth: 380 };
  } else if (rect && rect.top > 200) {
    cardStyle = { top: rect.top - 180, left: Math.max(16, rect.left), maxWidth: 380 };
  } else {
    cardStyle = { top: "40%", left: "50%", transform: "translate(-50%, -50%)", maxWidth: 380 };
  }

  return (
    <div className="fixed inset-0 z-[100]" style={{ pointerEvents: "auto" }}
         onClick={(e) => e.stopPropagation()}>
      {rect && (
        <div className="absolute rounded-xl transition-all duration-300"
             style={{ top: rect.top - 6, left: rect.left - 6, width: rect.width + 12, height: rect.height + 12,
                      boxShadow: "0 0 0 9999px rgba(12, 43, 39, 0.62)", border: "2px solid #14b8a6",
                      borderRadius: "0.75rem" }} />
      )}
      <div className="absolute glass rounded-xl p-4 shadow-xl shadow-slate-900/20"
           style={cardStyle}>
        <div className="text-[10px] uppercase tracking-wider font-semibold text-teal-600 mb-1">
          Step {i + 1} of {steps.length}
        </div>
        <div className="text-sm font-medium text-zinc-200 mb-1">{step.title}</div>
        <div className="text-xs text-zinc-500 leading-relaxed">{step.text}</div>
        <div className="flex items-center gap-2 mt-3">
          <button onClick={() => (last ? onDone() : setI(i + 1))}
                  className="bg-teal-500/15 text-teal-700 border border-teal-900/40 rounded-md px-3 py-1.5 text-xs hover:bg-teal-500/25">
            {last ? "Finish" : "Next"}
          </button>
          {i > 0 && (
            <button onClick={() => setI(i - 1)}
                    className="text-xs text-zinc-400 hover:text-zinc-200">Back</button>
          )}
          <span className="flex-1" />
          <button onClick={onDone} className="text-xs text-zinc-500 hover:text-zinc-300">Skip tour</button>
        </div>
      </div>
    </div>
  );
}
