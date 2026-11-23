import { createContext, useContext, useEffect, useState } from "react";
import { getJson } from "./api.js";

const Store = createContext(null);

export function StoreProvider({ children }) {
  const [dataset, setDatasetState] = useState(
    () => localStorage.getItem("dataset") || "clean/sample1_dev_seeded"
  );
  const [keysDir, setKeysDirState] = useState(
    () => localStorage.getItem("keysDir") || "answer_keys/sample2"
  );
  const [drafts, setDrafts] = useState([]);
  const [summary, setSummary] = useState(null);   // {claims, billed} for the command bar

  const setDataset = (d) => {
    localStorage.setItem("dataset", d);
    setDatasetState(d);
  };
  const setKeysDir = (k) => {
    localStorage.setItem("keysDir", k);
    setKeysDirState(k);
  };

  // Live headline numbers for the command bar, fetched once per dataset.
  useEffect(() => {
    let alive = true;
    setSummary(null);
    getJson("/api/overview", { dataset })
      .then((r) => { if (alive) setSummary({ claims: r.summary?.claims ?? null, billed: r.money?.allowed ?? null }); })
      .catch(() => {});
    return () => { alive = false; };
  }, [dataset]);

  return (
    <Store.Provider value={{ dataset, setDataset, keysDir, setKeysDir, drafts, setDrafts, summary }}>
      {children}
    </Store.Provider>
  );
}

export const useStore = () => useContext(Store);
