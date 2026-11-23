// ICD-9 3-digit prefix -> readable name (covers the most common codes in the
// DE-SynPUF data). Unknown codes fall back to the raw code.
const DX_MAP = {
  "250": "Diabetes",
  "401": "Hypertension",
  "428": "Heart failure",
  "414": "Ischemic heart disease",
  "427": "Heart rhythm disorder",
  "496": "COPD",
  "492": "Emphysema",
  "466": "Bronchitis",
  "585": "Chronic kidney disease",
  "599": "Urinary tract disorder",
  "296": "Depression / bipolar",
  "311": "Depression",
  "331": "Alzheimer's / dementia",
  "290": "Dementia",
  "714": "Rheumatoid arthritis",
  "715": "Osteoarthritis",
  "733": "Osteoporosis",
  "433": "Stroke (carotid artery)",
  "434": "Stroke (arterial occlusion)",
  "436": "Stroke (acute, ill-defined)",
  "430": "Subarachnoid hemorrhage",
  "431": "Intracerebral hemorrhage",
  "435": "TIA",
  "V58": "Long-term drug therapy",
  "V45": "Post-surgical follow-up",
  "196": "Cancer (secondary)",
  "162": "Lung cancer",
  "153": "Colon cancer",
  "174": "Breast cancer",
  "185": "Prostate cancer",
  "188": "Bladder cancer",
  "203": "Multiple myeloma",
  "486": "Pneumonia",
  "780": "General symptoms",
  "786": "Respiratory symptoms",
  "272": "Lipid disorder",
  "280": "Iron deficiency anemia",
  "285": "Anemia",
  "276": "Fluid/electrolyte disorder",
  "456": "Esophageal varices",
  "562": "Diverticulosis",
  "558": "Enteritis/colitis",
  "536": "Stomach disorder",
  "707": "Chronic skin ulcer",
  "682": "Skin infection",
  "038": "Septicemia",
  "482": "Bacterial pneumonia",
  "518": "Lung disease",
  "507": "Aspiration pneumonia",
};

// First-3-chars prefix match (V-codes use the first 3 chars too, e.g. "V58").
export function dxName(code) {
  if (!code) return "";
  const c = String(code).toUpperCase();
  const key = c.startsWith("E") || c.startsWith("V") ? c.slice(0, 3) : c.slice(0, 3);
  return DX_MAP[key] ?? code;
}
