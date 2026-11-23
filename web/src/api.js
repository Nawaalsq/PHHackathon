// API base: same origin by default (FastAPI serves the built frontend),
// or point it at a separately hosted backend with VITE_API_URL at build time.
const API_BASE = import.meta.env.VITE_API_URL || "";

// Central fetch wrapper: injects the dataset query-string and unwraps errors.
export async function getJson(path, params = {}) {
  const qs = new URLSearchParams(
    Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "")
  );
  const res = await fetch(`${API_BASE}${path}${qs.toString() ? `?${qs}` : ""}`);
  if (!res.ok) throw new Error(await errMsg(res));
  return res.json();
}

export async function postJson(path, body) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await errMsg(res));
  return res.json();
}

export async function delJson(path) {
  const res = await fetch(`${API_BASE}${path}`, { method: "DELETE" });
  if (!res.ok) throw new Error(await errMsg(res));
  return res.json();
}

async function errMsg(res) {
  const body = await res.json().catch(() => ({}));
  const detail = body.detail;
  if (typeof detail === "string") return detail;
  if (detail?.message) return detail.message;
  return res.statusText || `HTTP ${res.status}`;
}
