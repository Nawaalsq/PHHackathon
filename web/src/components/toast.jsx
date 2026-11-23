import { createContext, useCallback, useContext, useRef, useState } from "react";

const ToastCtx = createContext(() => {});

// Lightweight toast feedback: push(message, "ok" | "error"), auto-dismisses.
export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const idRef = useRef(0);
  const push = useCallback((message, type = "ok") => {
    const id = ++idRef.current;
    setToasts((t) => [...t, { id, message, type }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 3200);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="fixed bottom-5 right-5 z-[200] space-y-2">
        {toasts.map((t) => (
          <div key={t.id}
               className={`toast rounded-lg px-4 py-2.5 text-sm shadow-lg shadow-slate-900/10 ${t.type === "error" ? "toast-error" : ""}`}>
            {t.message}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export const useToast = () => useContext(ToastCtx);
