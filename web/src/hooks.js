import { useEffect, useRef, useState } from "react";

// Animate a number from 0 (or its previous value) to target with an ease-out.
// Returns null while animating is not applicable (non-numeric), so callers can
// render the value statically.
export function useCountUp(target, duration = 800) {
  const num = typeof target === "number" && Number.isFinite(target) ? target : null;
  const [value, setValue] = useState(num ?? 0);
  const fromRef = useRef(0);
  const rafRef = useRef(null);

  useEffect(() => {
    if (num === null) return undefined;
    const from = fromRef.current;
    const start = performance.now();
    const tick = (now) => {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3);   // ease-out cubic
      const v = from + (num - from) * eased;
      setValue(v);
      if (t < 1) {
        rafRef.current = requestAnimationFrame(tick);
      } else {
        fromRef.current = num;
      }
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      fromRef.current = num;
    };
  }, [num, duration]);

  return num === null ? null : value;
}
