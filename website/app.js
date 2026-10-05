import {
  DEFAULT_POLICY,
  STRATEGIES,
  TYPES,
  auditSnapshot,
  calibrationBins,
  captureCurve,
  demoScore,
  evaluate,
  normalizePolicy,
  policyFromParams,
  policyParams,
  precisionRecallCurve,
  queueCSV,
} from "./domain.js";

/** @typedef {import('./domain.js').Policy} Policy */
/** @typedef {import('./domain.js').Dataset} Dataset */
/** @typedef {import('./domain.js').Case} Case */
/** @type {Dataset} */
let data;
let policy = { ...DEFAULT_POLICY };
let baseline = {
  ...DEFAULT_POLICY,
  strategy: /** @type {import('./domain.js').Strategy} */ ("probability"),
};
const pages = {
  overview: "Overview",
  queue: "Review queue",
  scenarios: "Scenario studio",
  models: "Model insights",
  scoring: "Scoring lab",
  evidence: "Evidence & drift",
};
/** @typedef {keyof typeof pages} Page */
/** @type {Page} */
let page = "overview";
let search = "",
  typeFilter = "all",
  bandFilter = "all",
  statusFilter = "all",
  queuePage = 1;
const pageSize = 12;
let showLoss = true,
  showProbability = true;
/** @type {Record<string,{status:string,note:string}>} */
let notes = {};
let activeCase = "";
let toastTimer = 0;
/** @param {string} id @returns {HTMLElement} */
function el(id) {
  const element = document.getElementById(id);
  if (!element) throw new Error(`Missing element: ${id}`);
  return element;
}
/** @param {string} id @returns {HTMLInputElement} */
const input = (id) => /** @type {HTMLInputElement} */ (el(id));
/** @param {unknown} text */
const escape = (text) =>
  String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
/** @param {number} n @param {number} [digits] */
const money = (n, digits = 0) =>
  new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: digits,
  }).format(n);
/** @param {number} n @param {number} [digits] */
const pct = (n, digits = 1) => `${(n * 100).toFixed(digits)}%`;
/** @param {number} n */
const count = (n) => n.toLocaleString("en-US");
/** @param {string} message */
function toast(message) {
  el("toast").textContent = message;
  el("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => {
    el("toast").hidden = true;
  }, 5000);
}
/** @param {string} title @param {string} value @param {string} foot @param {boolean} [accent] */
const kpi = (title, value, foot, accent = false) =>
  `<article class="kpi${accent ? " accent" : ""}"><div class="kpi-label">${title}<span aria-hidden="true">↗</span></div><div class="kpi-number">${value}</div><div class="kpi-foot">${foot}</div></article>`;
/** @param {string} title @param {string} subtitle @param {string} [action] */
const panelHead = (title, subtitle, action = "") =>
  `<div class="panel-header"><div><h2>${title}</h2><p>${subtitle}</p></div>${action}</div>`;
/** @param {string} title @param {string} subtitle @param {string} [actions] */
const heading = (title, subtitle, actions = "") =>
  `<div class="page-heading"><div><div class="eyebrow">RISK INTELLIGENCE / ${escape(pages[page].toUpperCase())}</div><h1>${title}</h1><p>${subtitle}</p></div><div class="heading-actions">${actions}</div></div>`;
const exportButton =
  '<button class="button" data-action="export-audit">Export analysis <span aria-hidden="true">↓</span></button>';

function updateURL() {
  const params = policyParams(policy);
  for (const [key, value] of policyParams(baseline))
    params.set(`b_${key}`, value);
  if (page === "queue") {
    if (typeFilter !== "all") params.set("type", typeFilter);
    if (bandFilter !== "all") params.set("band", bandFilter);
  }
  history.replaceState(null, "", `${location.pathname}?${params}#${page}`);
}

function policyControls() {
  return `<form class="policy-controls" id="policy-controls" aria-label="Analysis policy"><label class="field">Rank transactions by<select id="policy-strategy">${Object.entries(
    STRATEGIES,
  )
    .map(
      ([value, label]) =>
        `<option value="${value}"${policy.strategy === value ? " selected" : ""}>${label}</option>`,
    )
    .join(
      "",
    )}</select></label><label class="field">Review capacity<input id="policy-capacity" type="number" min="0" max="${data.transactions.length}" step="1" value="${policy.capacity}" required /><small>Up to ${count(data.transactions.length)} transactions</small></label><label class="field">Minimum probability<input id="policy-threshold" type="number" min="0" max="1" step="0.01" value="${policy.threshold}" required /><small>0 = rank every transaction</small></label><label class="field">Cost per review ($)<input id="policy-cost" type="number" min="0" max="1000" step="0.5" value="${policy.reviewCost}" required /></label><label class="field">Fraud loss fraction<input id="policy-loss" type="number" min="0" max="1" step="0.05" value="${policy.lossFraction}" required /><small>1 = full transaction amount</small></label></form>`;
}

/** @param {number} n */
function chartCapacity(n) {
  policy.capacity = Math.round(n);
  queuePage = 1;
  updateURL();
  if (document.getElementById("quick-capacity")) {
    input("quick-capacity").value = String(n);
    el("capacity-value").textContent = String(n);
  }
  if (document.getElementById("policy-capacity"))
    input("policy-capacity").value = String(n);
  renderContent();
}

function curveChart() {
  const capacities = [
    ...new Set([
      0,
      25,
      50,
      100,
      250,
      500,
      750,
      1000,
      data.transactions.length,
      policy.capacity,
    ]),
  ].sort((a, b) => a - b);
  const curve = captureCurve(data.transactions, capacities);
  const x = (/** @type {number} */ n) =>
    46 + (n / data.transactions.length) * 530;
  const y = (/** @type {number} */ n) => 214 - n * 178;
  const lossPath = curve
    .map((p, i) => `${i ? "L" : "M"}${x(p.capacity)},${y(p.loss)}`)
    .join(" ");
  const probabilityPath = curve
    .map((p, i) => `${i ? "L" : "M"}${x(p.capacity)},${y(p.probability)}`)
    .join(" ");
  const selected = evaluate(data.transactions, policy);
  return `<div class="legend"><button data-action="toggle-loss" aria-pressed="${showLoss}"><span class="legend-mark"></span>Expected-loss ranking</button><button data-action="toggle-probability" aria-pressed="${showProbability}"><span class="legend-mark orange"></span>Probability ranking</button></div><svg class="chart" viewBox="0 0 600 255" role="group" aria-labelledby="curve-title curve-description"><title id="curve-title">Fraud value captured by review capacity</title><desc id="curve-description">Full held-out cohort, no threshold, 100% loss fraction. At ${policy.capacity} reviews, the active policy captures ${pct(selected.valueCapture)}. Select a chart point or use the capacity control below. A data table follows.</desc>${[0, 0.25, 0.5, 0.75, 1].map((n) => `<line class="gridline" x1="46" x2="578" y1="${y(n)}" y2="${y(n)}"/><text x="34" y="${y(n) + 3}" text-anchor="end">${n * 100}%</text>`).join("")}${[0, 500, 1000, data.transactions.length].map((n) => `<text x="${x(n)}" y="238" text-anchor="middle">${count(n)}</text>`).join("")}<line x1="${x(policy.capacity)}" x2="${x(policy.capacity)}" y1="30" y2="214" stroke="#bbc9aa" stroke-dasharray="3 4"/>${showLoss ? `<path d="${lossPath} L578,214 L46,214 Z" fill="#edf3e4"/><path class="curve" d="${lossPath}"/>` : ""}${showProbability ? `<path class="curve orange" d="${probabilityPath}"/>` : ""}${curve
    .filter((p) => p.capacity > 0)
    .map(
      (p) =>
        `<g class="chart-point" role="button" tabindex="0" data-capacity="${p.capacity}" aria-label="Set capacity to ${p.capacity} reviews: expected loss ${pct(p.loss)}, probability ${pct(p.probability)}"><title>${p.capacity} reviews · Expected loss ${pct(p.loss)} · Probability ${pct(p.probability)}</title>${showLoss ? `<circle class="point" cx="${x(p.capacity)}" cy="${y(p.loss)}" r="${p.capacity === policy.capacity ? 6 : 3}"/>` : ""}${showProbability ? `<circle class="point orange" cx="${x(p.capacity)}" cy="${y(p.probability)}" r="3"/>` : ""}</g>`,
    )
    .join(
      "",
    )}</svg><div class="chart-note">X: reviews available · Y: observed fraud dollars in the queue. Curves use the full cohort and no minimum threshold.</div><details class="chart-data"><summary>View chart data</summary><div class="table-wrap" tabindex="0" role="region" aria-label="Analysis data table"><table class="data-table"><thead><tr><th>Reviews</th><th>Expected loss</th><th>Probability</th></tr></thead><tbody>${curve.map((p) => `<tr><td>${p.capacity}</td><td>${pct(p.loss)}</td><td>${pct(p.probability)}</td></tr>`).join("")}</tbody></table></div></details>`;
}

function overviewShell() {
  return `${heading("Every review counts.", "Make the tradeoff between risk, exposure, and analyst capacity visible.", exportButton + '<a class="button primary" href="#queue">Open review queue <span aria-hidden="true">↗</span></a>')}<section class="hero" aria-label="Workspace introduction"><div><span class="small-label">FROM MODEL SCORE TO BETTER PRIORITIES</span><h2>Find the risk.<br/>Focus on what matters.</h2><p>A capacity-aware workspace for exploring fraud decisions. Follow the dollars exposed, inspect the evidence, and test a better policy.</p><a class="button lime small" href="#scenarios">Explore a scenario <span aria-hidden="true">↗</span></a></div><div class="hero-graphic" aria-hidden="true"><div class="orbit"></div><div class="orbit"></div><div class="orbit"></div><span class="hero-number">${count(data.summary.transactions)}<small>TRANSACTIONS / HELD OUT</small></span></div></section><section class="panel range-row overview-capacity" aria-label="Review capacity"><label for="quick-capacity">Adjust analyst capacity</label><input id="quick-capacity" type="range" min="0" max="${data.transactions.length}" step="1" value="${policy.capacity}"/><output id="capacity-value" for="quick-capacity">${policy.capacity}</output></section><div id="page-content"></div>`;
}

function overviewContent() {
  const metrics = evaluate(data.transactions, policy);
  const probability = evaluate(data.transactions, {
    ...policy,
    strategy: "probability",
  });
  const delta = (metrics.valueCapture - probability.valueCapture) * 100;
  const byType = TYPES.map((type) => ({
    type,
    loss: data.transactions
      .filter((r) => r.type === type)
      .reduce(
        (s, r) => s + r.amount * r.fraud_probability * policy.lossFraction,
        0,
      ),
  })).sort((a, b) => b.loss - a.loss);
  const max = Math.max(1, ...byType.map((r) => r.loss));
  return `<section class="kpi-grid" aria-label="Current policy results">${kpi("FRAUD VALUE CAPTURED", pct(metrics.valueCapture), `${delta >= 0 ? "+" : ""}${delta.toFixed(1)} points vs. probability ranking`, true)}${kpi("MODELED EXPOSURE", money(metrics.expectedExposure), "Expected loss in the selected queue")}${kpi("REVIEWS ALLOCATED", count(metrics.reviews), `${count(metrics.eligible)} eligible · ${count(policy.capacity)} capacity`)}${kpi("FRAUD CASE RECALL", pct(metrics.recall), `${metrics.casesCaptured} of ${metrics.totalFraud} labeled fraud cases`)}</section><div class="two-columns"><section class="panel">${panelHead("Capacity changes the outcome.", "Explore how many fraud dollars each ranking brings into review.", '<span class="tag">HELD-OUT EVALUATION</span>')}${curveChart()}</section><section class="panel">${panelHead("Where exposure lives.", "Estimated loss by transaction type, across the full cohort.", '<span class="section-number">02</span>')}${byType.map((r) => `<div class="exposure-item"><div class="exposure-heading"><button data-type="${r.type}" aria-label="View ${r.type.replaceAll("_", " ")} review cases">${r.type.replaceAll("_", " ")}</button><span>${money(r.loss)}</span></div><svg class="exposure-bar" viewBox="0 0 100 7" preserveAspectRatio="none" width="100%" aria-hidden="true"><rect width="100" height="7" fill="#eef0e8"/><rect width="${(r.loss / max) * 100}" height="7" fill="#527146" rx="2"/></svg></div>`).join("")}<div class="inline-note">A lower-probability transaction can rank higher when more dollars are exposed. Select a type to inspect its cases.</div></section></div><section class="panel table-panel">${panelHead("The next cases to review.", "Ranked by " + STRATEGIES[policy.strategy].toLowerCase() + ". Select a case to understand its priority.", '<a class="text-link" href="#queue">View full queue <span aria-hidden="true">↗</span></a>')}${caseTable(metrics.queue.slice(0, 5))}<div class="table-footer"><span>Policy: ${escape(STRATEGIES[policy.strategy])} · ${pct(policy.threshold, 0)} minimum probability</span><span class="quality-stamp">TRACEABLE BY DESIGN</span></div></section><p class="fine-print">Capture and recall use known historical labels. Exposure uses model probabilities. Neither measures money actually recovered. Change cost, threshold, and loss assumptions in Scenario studio.</p>`;
}

/** @param {Case[]} rows */
function caseTable(rows) {
  if (!rows.length)
    return '<div class="empty-state"><h2>No cases in this view</h2><p>Adjust the minimum probability, capacity, or filters to bring cases into view.</p><button class="button" data-action="reset-filters">Clear filters</button></div>';
  return `<div class="table-wrap" tabindex="0" role="region" aria-label="Ranked cases, scroll horizontally if needed"><table class="data-table"><thead><tr><th>Rank</th><th>Transaction</th><th>Type</th><th class="number">Amount</th><th class="number">Probability</th><th class="number">Expected loss</th><th>Risk band</th><th>Local status</th></tr></thead><tbody>${rows.map((r) => `<tr><td class="mono">${String(r.rank).padStart(2, "0")}</td><td><button class="case-link" data-case="${escape(r.transaction_id)}" aria-label="Inspect ${escape(r.transaction_id)}">${escape(r.transaction_id)} <span aria-hidden="true">↗</span></button></td><td class="type-label">${escape(r.type.replaceAll("_", " "))}</td><td class="number">${money(r.amount, 2)}</td><td class="number"><div class="probability">${pct(r.fraud_probability)}<svg class="prob-track" viewBox="0 0 100 4" aria-hidden="true"><rect width="100" height="4" fill="#e5eadc"/><rect width="${r.fraud_probability * 100}" height="4" fill="#6a844f"/></svg></div></td><td class="number">${money(r.expected_loss, 2)}</td><td><span class="tag ${r.risk_band}">${escape(r.risk_band)}</span></td><td><span class="tag">${escape(notes[r.transaction_id]?.status ?? "unreviewed")}</span></td></tr>`).join("")}</tbody></table></div>`;
}

function queueShell() {
  return `${heading("Put the right cases first.", "Inspect the ranked queue. Filters narrow the selected queue without changing its policy.", '<button class="button" data-action="export-queue">Export filtered CSV <span aria-hidden="true">↓</span></button>' + exportButton)}${policyControls()}<section class="filters" aria-label="Queue filters"><label class="field search-field">Search transaction ID<input id="queue-search" type="search" placeholder="Find a transaction…" value="${escape(search)}" autocomplete="off" /></label><label class="field">Transaction type<select id="queue-type"><option value="all">All types</option>${TYPES.map((t) => `<option${typeFilter === t ? " selected" : ""}>${t}</option>`).join("")}</select></label><label class="field">Risk band<select id="queue-band">${["all", "critical", "high", "guarded", "low"].map((b) => `<option value="${b}"${bandFilter === b ? " selected" : ""}>${b === "all" ? "All risk bands" : b}</option>`).join("")}</select></label><label class="field">Local review status<select id="queue-status">${["all", "unreviewed", "investigating", "resolved"].map((s) => `<option${statusFilter === s ? " selected" : ""} value="${s}">${s === "all" ? "All statuses" : s}</option>`).join("")}</select></label></section><div id="page-content"></div>`;
}

function filteredQueue() {
  return evaluate(data.transactions, policy).queue.filter(
    (r) =>
      (typeFilter === "all" || r.type === typeFilter) &&
      (bandFilter === "all" || r.risk_band === bandFilter) &&
      (statusFilter === "all" ||
        (notes[r.transaction_id]?.status ?? "unreviewed") === statusFilter) &&
      r.transaction_id.toLowerCase().includes(search.toLowerCase().trim()),
  );
}

function queueContent() {
  const metrics = evaluate(data.transactions, policy);
  const filtered = filteredQueue();
  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  queuePage = Math.min(queuePage, totalPages);
  return `<div class="mini-kpis">${kpi("SELECTED QUEUE", count(metrics.reviews), `${count(metrics.eligible)} pass the minimum probability`)}${kpi("VALUE CAPTURE", pct(metrics.valueCapture), "Full selected queue, before display filters")}${kpi("MODELED COST", money(metrics.modeledCost), "Review spend + missed labeled fraud loss")}</div><div class="filter-summary" role="status">${count(filtered.length)} matching cases in the ${count(metrics.reviews)}-review queue. <button class="text-link" data-action="reset-filters">Clear filters</button></div><section class="panel table-panel">${panelHead("Analyst review queue", "Select a transaction for its financial rationale and historical context.", '<span class="tag">BROWSER-LOCAL NOTES</span>')}${caseTable(filtered.slice((queuePage - 1) * pageSize, queuePage * pageSize))}<div class="table-footer"><span>${filtered.length ? `${(queuePage - 1) * pageSize + 1}–${Math.min(queuePage * pageSize, filtered.length)} of ${count(filtered.length)} cases` : "0 cases"}</span><div class="pagination"><button class="button small" data-action="previous"${queuePage === 1 ? " disabled" : ""} aria-label="Previous queue page">←</button><span>Page ${queuePage} / ${totalPages}</span><button class="button small" data-action="next"${queuePage === totalPages ? " disabled" : ""} aria-label="Next queue page">→</button></div></div></section><p class="fine-print">Status and notes are stored only in this browser, independently of PostgreSQL analyst state. Every exported row comes from this synthetic snapshot. Risk bands describe probability; queue rank follows the chosen financial policy.</p>`;
}

function scenarioShell() {
  return `${heading("What if you changed the policy?", "Compare a baseline with an alternative on the same held-out transactions.", exportButton + '<button class="button primary" data-action="freeze-baseline">Use current as baseline <span aria-hidden="true">←</span></button>')}${policyControls()}<div id="page-content"></div>`;
}

/** @param {Policy} p @param {string} label @param {boolean} active */
function scenarioCard(p, label, active) {
  const m = evaluate(data.transactions, p);
  return `<section class="panel scenario-card${active ? " active" : ""}"><div class="scenario-head"><h2>${label}</h2><span class="tag">${active ? "EDITING" : "FROZEN"}</span></div><div class="scenario-body"><div class="scenario-meta">${STRATEGIES[p.strategy]}<br/>${p.capacity} capacity · ${pct(p.threshold)} minimum score<br/>${money(p.reviewCost, 2)} / review · ${pct(p.lossFraction, 0)} loss fraction</div><div class="small-label">FRAUD VALUE CAPTURED</div><div class="scenario-result">${pct(m.valueCapture)} <small>of labeled fraud dollars</small></div><dl class="detail-grid"><div><dt>REVIEWS</dt><dd>${m.reviews}</dd></div><div><dt>PRECISION</dt><dd>${pct(m.precision)}</dd></div><div><dt>CASE RECALL</dt><dd>${pct(m.recall)}</dd></div><div><dt>REVIEW SPEND</dt><dd>${money(m.reviewSpend)}</dd></div><div><dt>MISSED FRAUD LOSS</dt><dd>${money(m.missedLoss)}</dd></div><div><dt>TOTAL MODELED COST</dt><dd>${money(m.modeledCost)}</dd></div></dl></div></section>`;
}

function scenariosContent() {
  const a = evaluate(data.transactions, baseline),
    b = evaluate(data.transactions, policy);
  const delta = (b.valueCapture - a.valueCapture) * 100;
  return `<div class="delta-banner"><strong>${delta >= 0 ? "+" : ""}${delta.toFixed(1)} pp</strong><p><b>Change in fraud value capture.</b><br/>The alternative costs ${money(Math.abs(b.modeledCost - a.modeledCost))} ${b.modeledCost <= a.modeledCost ? "less" : "more"} in this retrospective model.</p></div><div class="equal-columns">${scenarioCard(baseline, "A / Baseline", false)}${scenarioCard(policy, "B / Alternative", true)}</div><div class="callout"><strong>Keep the assumptions visible.</strong> Modeled cost assumes that reviewing a fraudulent transaction prevents its full loss fraction. Actual investigation and recovery rates are not measured here. Capture uses the full labeled fraud value denominator, even when a threshold excludes transactions.</div><section class="panel">${panelHead("Choose a useful starting point.", "These presets change the alternative. The baseline stays fixed.")}<div class="pill-row"><button class="button" data-preset="focused">Focused · 100 reviews</button><button class="button" data-preset="balanced">Balanced · 250 reviews</button><button class="button" data-preset="coverage">Coverage · 750 reviews</button><button class="button" data-preset="threshold">Validation threshold · ${data.summary.selected_threshold}</button><button class="button subtle" data-action="reset-policy">Reset alternative</button></div><p class="fine-print">At a fixed cost and loss fraction, expected loss and net review value give the same ordering. Net review value subtracts review cost; this top-k queue can include cases with negative net value. Add a minimum probability to restrict eligibility.</p></section>`;
}

/** @param {{x:number,y:number}[]} points @param {string} label @param {string} xLabel @param {string} yLabel @param {number} [limit] */
function xyChart(points, label, xLabel, yLabel, limit = 1) {
  const x = (/** @type {number} */ v) => 45 + (v / limit) * 510,
    y = (/** @type {number} */ v) => 215 - (v / limit) * 175;
  return `<svg class="chart" viewBox="0 0 600 267" role="img" aria-label="${escape(label)}">${[0, 0.25, 0.5, 0.75, 1].map((n) => `<line class="gridline" x1="45" x2="555" y1="${y(n * limit)}" y2="${y(n * limit)}"/><text x="35" y="${y(n * limit) + 3}" text-anchor="end">${pct(n * limit, 0)}</text><text x="${x(n * limit)}" y="237" text-anchor="middle">${pct(n * limit, 0)}</text>`).join("")}<path class="curve" d="${points.map((p, i) => `${i ? "L" : "M"}${x(p.x)},${y(p.y)}`).join(" ")}"/>${points.length < 20 ? `<line x1="45" x2="555" y1="215" y2="40" stroke="#afba9c" stroke-dasharray="4 4"/>${points.map((p) => `<circle class="point" cx="${x(p.x)}" cy="${y(p.y)}" r="4"><title>${xLabel}: ${pct(p.x)}; ${yLabel}: ${pct(p.y)}</title></circle>`).join("")}` : ""}<text x="300" y="260" text-anchor="middle">${escape(xLabel)}</text></svg><p class="chart-note">Vertical axis: ${escape(yLabel)}. ${escape(label)}</p>`;
}

function modelsContent() {
  const bins = calibrationBins(data.transactions),
    curve = precisionRecallCurve(data.transactions);
  const selected = data.summary.metrics[data.summary.best_model];
  const thresholds = [0.01, 0.04, 0.12, 0.5];
  return `<div class="kpi-grid">${kpi("AVERAGE PRECISION", selected.average_precision.toFixed(3), "Rare-event ranking across thresholds", true)}${kpi("ROC AUC", selected.roc_auc.toFixed(3), "Selected calibrated model")}${kpi("BRIER SCORE", selected.brier.toFixed(3), "Probability error · lower is better")}${kpi("FRAUD PREVALENCE", pct(data.summary.fraud_rate, 2), `${data.summary.fraud_cases} of ${count(data.summary.transactions)} transactions`)}</div><div class="model-chart-grid"><section class="panel">${panelHead("Rare events need a different lens.", "Precision–recall for the selected model on the held-out cohort.")}${xyChart(
    curve.map((p) => ({ x: p.recall, y: p.precision })),
    "Higher recall typically includes more false alerts.",
    "Recall",
    "Precision",
  )}<details class="chart-data"><summary>Show precision–recall data</summary><div class="table-wrap" tabindex="0" role="region" aria-label="Analysis data table"><table class="data-table"><thead><tr><th>Recall</th><th>Precision</th></tr></thead><tbody>${curve
    .filter((_, i) => i % 20 === 0 || i === curve.length - 1)
    .map(
      (p) => `<tr><td>${pct(p.recall)}</td><td>${pct(p.precision)}</td></tr>`,
    )
    .join(
      "",
    )}</tbody></table></div><p>Table samples every 20th distinct score threshold; chart uses all thresholds.</p></details></section><section class="panel">${panelHead("Are the probabilities credible?", "Eight equal-count bins · predicted versus observed fraud.")}${xyChart(
    bins.map((b) => ({ x: b.predicted, y: b.observed })),
    "Dashed diagonal represents perfect calibration.",
    "Predicted probability",
    "Observed fraud",
    Math.max(0.15, ...bins.map((b) => Math.max(b.predicted, b.observed))),
  )}<details class="chart-data"><summary>Show calibration data</summary><div class="table-wrap" tabindex="0" role="region" aria-label="Analysis data table"><table class="data-table"><thead><tr><th>Count</th><th>Predicted</th><th>Observed</th></tr></thead><tbody>${bins.map((b) => `<tr><td>${b.count}</td><td>${pct(b.predicted)}</td><td>${pct(b.observed)}</td></tr>`).join("")}</tbody></table></div></details></section></div><section class="panel table-panel">${panelHead("Measured model comparison.", "Selected on validation average precision before final test evaluation.")}<div class="table-wrap" tabindex="0" role="region" aria-label="Analysis data table"><table class="data-table"><thead><tr><th>Model</th><th class="number">Avg. precision</th><th class="number">ROC AUC</th><th class="number">Brier</th><th class="number">Log loss</th></tr></thead><tbody>${Object.entries(
    data.summary.metrics,
  )
    .map(
      ([name, m]) =>
        `<tr><td class="model-name">${escape(name)}${name === data.summary.best_model ? '<small class="tag">VALIDATION SELECTED</small>' : ""}</td><td class="number">${m.average_precision.toFixed(3)}</td><td class="number">${m.roc_auc.toFixed(3)}</td><td class="number">${m.brier.toFixed(3)}</td><td class="number">${m.log_loss.toFixed(3)}</td></tr>`,
    )
    .join(
      "",
    )}</tbody></table></div></section><section class="panel table-panel" aria-label="Threshold comparison">${panelHead("A threshold is an operating choice.", "Held-out outcomes at illustrative thresholds, without a capacity cap.")}<div class="table-wrap" tabindex="0" role="region" aria-label="Analysis data table"><table class="data-table"><thead><tr><th>Minimum probability</th><th class="number">Reviews</th><th class="number">Precision</th><th class="number">Recall</th><th class="number">Modeled cost</th><th>Explore</th></tr></thead><tbody>${thresholds
    .map((threshold) => {
      const m = evaluate(data.transactions, {
        ...DEFAULT_POLICY,
        threshold,
        capacity: data.transactions.length,
      });
      return `<tr><td>${threshold.toFixed(2)}</td><td class="number">${m.reviews}</td><td class="number">${pct(m.precision)}</td><td class="number">${pct(m.recall)}</td><td class="number">${money(m.modeledCost)}</td><td><button class="text-link" data-threshold="${threshold}">Use policy ↗</button></td></tr>`;
    })
    .join(
      "",
    )}</tbody></table></div></section><p class="fine-print">Training features exclude post-transaction balances and flagged-fraud labels. History is frozen at each hour boundary. These results measure this synthetic cohort; calibration does not establish real-world probability accuracy.</p>`;
}

function scoringShell() {
  return `${heading("Follow a score, step by step.", "A transparent development scorer, matching the existing Python API.")}<div class="callout"><strong>Development heuristic.</strong> This lab uses the deterministic API fallback, not the calibrated model behind the evaluation dashboard. No transaction is submitted to a server. Inputs stay in this tab.</div><div class="equal-columns"><section class="panel">${panelHead("Try a transaction.", "Change the type, amount, or event hour and inspect each scoring term.")}<form id="score-form" class="form-grid"><label class="field span-two">Transaction ID<input id="score-id" maxlength="120" value="lab-transfer-001" required /></label><label class="field">Transaction type<select id="score-type">${TYPES.map((t) => `<option${t === "TRANSFER" ? " selected" : ""}>${t}</option>`).join("")}</select></label><label class="field">Amount ($)<input id="score-amount" type="number" min="0" max="1000000000000" step="0.01" value="4200" required /></label><label class="field span-two">Event hour (whole hours since start)<input id="score-step" type="number" min="0" max="10000000" step="1" value="26" required /><small>Hours 0–4 within each day add the night-time term.</small></label><div class="span-two"><button class="button primary" type="submit" id="score-submit">Calculate development score <span aria-hidden="true">↗</span></button><p id="score-error" class="error-text" role="alert" hidden></p></div></form><div id="score-output" aria-live="polite"><div class="score-result"><p>Run the scorer to see its probability, exposure, and exact formula.</p></div></div></section><section class="panel">${panelHead("An explanation you can audit.", "Every contribution is an explicit term in the development heuristic.")}<div class="feature-list"><div class="feature-row"><span>Base probability</span><strong>1.5%</strong></div><div class="feature-row"><span>Type term · transfer / cash out / other</span><strong>34 / 22 / 3%</strong></div><div class="feature-row"><span>Amount term</span><strong>min(42%, amount / 100,000)</strong></div><div class="feature-row"><span>Night-time term · hour 0–4</span><strong>+8%</strong></div><div class="feature-row"><span>Stable ID term · first SHA-256 bytes</span><strong>0–4%</strong></div><div class="feature-row"><span>Final probability cap</span><strong>99%</strong></div></div><p class="fine-print">The formula mirrors <code>riskqueue.scoring.demo_probability</code>. Its deterministic output is useful for development and API testing. It does not represent measured fraud likelihood. Dashboard case explanations show ranking arithmetic and observed history; they do not invent SHAP contributions.</p><a class="text-link" href="https://github.com/cr-shah/riskqueue/blob/main/riskqueue/scoring.py" target="_blank" rel="noopener noreferrer">Inspect the Python scorer ↗</a></section></div>`;
}

function evidenceContent() {
  const psi = data.summary.monitoring.amount_psi;
  return `<div class="equal-columns"><section class="panel">${panelHead("A change worth investigating.", "Offline distribution-shift demonstration.", '<span class="tag orange">WATCH</span>')}<div class="small-label">AMOUNT POPULATION STABILITY INDEX</div><div class="scenario-result">${psi.toFixed(2)}</div><svg viewBox="0 0 300 24" width="100%" role="img" aria-label="PSI ${psi.toFixed(2)} in watch range, stable below 0.1 and alert above 0.25"><rect x="0" y="8" width="100" height="9" fill="#bccf98"/><rect x="100" y="8" width="150" height="9" fill="#eed8a1"/><rect x="250" y="8" width="50" height="9" fill="#e5b09a"/><line x1="${(psi / 0.3) * 300}" x2="${(psi / 0.3) * 300}" y1="0" y2="24" stroke="#234a3c" stroke-width="3"/></svg><div class="drift-labels"><span>STABLE &lt; 0.10</span><span>WATCH</span><span>ALERT ≥ 0.25</span></div><p class="fine-print">The simulated comparison multiplies reference transaction amounts by 1.8. PSI ${psi.toFixed(3)} signals input drift; it does not demonstrate that model accuracy has declined. This is a saved evaluation, not a live monitor.</p><div class="inline-note">Investigate upstream data quality and observed fraud outcomes before deciding whether retraining is warranted.</div></section><section class="panel">${panelHead("Know exactly what you are using.", "A reproducible snapshot with a traceable source.")}<div class="feature-list"><div class="feature-row"><span>Dataset</span><strong>Synthetic demo · seed ${data.provenance.seed}</strong></div><div class="feature-row"><span>Held-out records</span><strong>${count(data.transactions.length)}</strong></div><div class="feature-row"><span>Evaluation split</span><strong>70 / 15 / 15 chronological</strong></div><div class="feature-row"><span>Probability calibration</span><strong>Sigmoid · validation period</strong></div><div class="feature-row"><span>Public accounts / secrets</span><strong>Excluded from site payload</strong></div></div><p class="fine-print">SHA-256 of the exact source CSV:</p><div class="provenance-hash">${escape(data.provenance.sha256)}</div><div class="dialog-actions"><button class="button" data-action="export-audit">Download evidence JSON ↓</button><a class="button subtle" href="https://github.com/cr-shah/riskqueue/blob/main/docs/MODEL_CARD.md" target="_blank" rel="noopener noreferrer">Model card ↗</a></div></section></div><div class="equal-columns"><section class="panel">${panelHead("The cloud architecture behind RiskQueue.", "Existing Python services remain available independently of this public site.")}<div class="pipeline">${[
    [
      "01",
      "Preserve & validate",
      "S3 raw events → Lambda → SQS. Contract validation and immutable ingestion.",
    ],
    [
      "02",
      "Score & prioritize",
      "History-only features → calibrated model → expected-loss decision policy.",
    ],
    [
      "03",
      "Serve & audit",
      "PostgreSQL operational queue. Snowflake analytical history and Prefect batch monitoring.",
    ],
  ]
    .map(
      ([n, title, body]) =>
        `<div class="pipeline-step"><span class="step-number">${n}</span><div><h3>${title}</h3><p>${body}</p></div></div>`,
    )
    .join(
      "",
    )}</div><p class="fine-print">GitHub Pages serves this public analysis workspace. It is not connected to cloud queues, databases, or transaction feeds.</p></section><section class="panel">${panelHead("Explain the decision, carefully.", "Observed context supports investigation, not a causal claim.")}<div class="feature-list"><div class="feature-row"><span>Amount versus sender history</span><strong>Prior median amount</strong></div><div class="feature-row"><span>Sender velocity</span><strong>Prior 24-hour transactions</strong></div><div class="feature-row"><span>New recipient relationship</span><strong>Prior pair count</strong></div><div class="feature-row"><span>Recipient breadth</span><strong>Distinct prior senders</strong></div></div><p class="fine-print">Inspect a queue case to see these actual feature values and the expected-loss formula. Feature values describe input context. Without saved model attribution artifacts, this workspace makes no claim about individual model contributions.</p><a class="text-link" href="#queue">Inspect the queue ↗</a></section></div>`;
}

function render() {
  document.querySelectorAll("[data-page]").forEach((link) => {
    if (link.getAttribute("data-page") === page)
      link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  el("breadcrumb-page").textContent = pages[page];
  document.title = `${pages[page]} · RiskQueue`;
  const main = el("main");
  if (page === "overview") main.innerHTML = overviewShell();
  else if (page === "queue") main.innerHTML = queueShell();
  else if (page === "scenarios") main.innerHTML = scenarioShell();
  else if (page === "models")
    main.innerHTML =
      heading(
        "Look beyond the headline.",
        "Understand ranking, probability quality, and operating thresholds.",
        exportButton,
      ) + '<div id="page-content"></div>';
  else if (page === "scoring") main.innerHTML = scoringShell();
  else
    main.innerHTML =
      heading(
        "Make the evidence inspectable.",
        "Understand provenance, data drift, and the boundaries of each claim.",
        exportButton,
      ) + '<div id="page-content"></div>';
  renderContent();
}

function renderContent() {
  const target = document.getElementById("page-content");
  if (!target) return;
  if (page === "overview") target.innerHTML = overviewContent();
  else if (page === "queue") target.innerHTML = queueContent();
  else if (page === "scenarios") target.innerHTML = scenariosContent();
  else if (page === "models") target.innerHTML = modelsContent();
  else if (page === "evidence") target.innerHTML = evidenceContent();
}

/** @param {string} id */
function openCase(id) {
  const row = evaluate(data.transactions, policy).queue.find(
    (r) => r.transaction_id === id,
  );
  if (!row) return;
  activeCase = id;
  const note = notes[id] ?? { status: "unreviewed", note: "" };
  el("case-content").innerHTML =
    `<div class="dialog-head"><div><div class="eyebrow">CASE EVIDENCE / RANK ${row.rank}</div><h2 id="case-title">${escape(id)}</h2><p>${escape(row.type.replaceAll("_", " "))} · Event hour ${row.step} · Synthetic evaluation</p></div><button class="icon-button" data-close aria-label="Close case details">×</button></div><div class="dialog-kpis">${kpi("AMOUNT", money(row.amount, 2), "Transaction exposure")}${kpi("PROBABILITY", pct(row.fraud_probability), "Calibrated model score")}${kpi("EXPECTED LOSS", money(row.expected_loss, 2), `${pct(policy.lossFraction, 0)} loss fraction`, true)}</div><section class="dialog-section"><h3>Why this case is in the queue</h3><p>Rank ${row.rank} by ${escape(STRATEGIES[policy.strategy].toLowerCase())}. Its score meets the ${pct(policy.threshold)} eligibility threshold, and it fits within the ${policy.capacity}-review capacity.</p><div class="formula">${pct(row.fraud_probability, 3)} × ${money(row.amount, 2)} × ${policy.lossFraction}<br/>= ${money(row.expected_loss, 2)} expected loss<br/>− ${money(policy.reviewCost, 2)} review cost<br/>= ${money(row.expected_review_value, 2)} net review value</div></section><section class="dialog-section"><h3>Observed historical context</h3><dl class="detail-grid"><div><dt>AMOUNT / PRIOR SENDER MEDIAN</dt><dd>${row.amount_to_sender_median.toFixed(2)}×</dd></div><div><dt>SENDER TRANSACTIONS / 24H</dt><dd>${row.sender_count_24h}</dd></div><div><dt>FIRST-TIME RECIPIENT</dt><dd>${row.first_time_recipient ? "Yes" : "No"}</dd></div><div><dt>RECIPIENT DISTINCT SENDERS / 24H</dt><dd>${row.recipient_unique_senders_24h}</dd></div></dl><p>These are history-only model inputs, not measured feature contributions or causal explanations.</p><details class="chart-data"><summary>Reveal historical outcome (evaluation only)</summary><p>Known label: <strong>${row.isFraud ? "Fraud" : "Not fraud"}</strong>. Labels evaluate policies and never influence ranking.</p></details></section><section class="dialog-section"><h3>Your local review record</h3><form id="note-form"><div class="form-grid"><label class="field">Review status<select id="case-status">${["unreviewed", "investigating", "resolved"].map((s) => `<option${note.status === s ? " selected" : ""}>${s}</option>`).join("")}</select></label><label class="field span-two">Analyst note<textarea id="case-note" maxlength="2000" placeholder="Record an observation about this synthetic case…">${escape(note.note)}</textarea></label></div><div class="dialog-actions"><button class="button primary" type="submit">Save local review</button><button class="button" type="button" data-action="export-case">Export case evidence ↓</button></div><p class="fine-print">Saved only in this browser. This does not update the backend or change financial decisions.</p></form></section>`;
  /** @type {HTMLDialogElement} */ (el("case-dialog")).showModal();
}

/** @param {string} name @param {string} contents @param {string} mime */
function download(name, contents, mime) {
  const url = URL.createObjectURL(new Blob([contents], { type: mime }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  toast(`${name} downloaded`);
}

async function share() {
  updateURL();
  try {
    await navigator.clipboard.writeText(location.href);
    toast("Scenario link copied. Policy and baseline are included.");
  } catch {
    input("share-url").value = location.href;
    /** @type {HTMLDialogElement} */ (el("share-dialog")).showModal();
    input("share-url").select();
  }
}

document.addEventListener("click", (event) => {
  const target =
    event.target instanceof Element
      ? event.target.closest("button,a,[data-capacity]")
      : null;
  if (!target) return;
  if (target.getAttribute("href") === "#main") {
    event.preventDefault();
    el("main").focus();
    return;
  }
  if (target.hasAttribute("data-close")) {
    /** @type {HTMLDialogElement} */ (target.closest("dialog")).close();
    return;
  }
  if (!data) return;
  if (target.hasAttribute("data-case"))
    openCase(target.getAttribute("data-case") ?? "");
  if (target.hasAttribute("data-capacity"))
    chartCapacity(Number(target.getAttribute("data-capacity")));
  if (target.hasAttribute("data-type")) {
    typeFilter = target.getAttribute("data-type") ?? "all";
    queuePage = 1;
    location.hash = "queue";
  }
  if (target.hasAttribute("data-threshold")) {
    policy = {
      ...DEFAULT_POLICY,
      threshold: Number(target.getAttribute("data-threshold")),
      capacity: data.transactions.length,
    };
    location.hash = "scenarios";
  }
  const preset = target.getAttribute("data-preset");
  if (preset) {
    policy = {
      ...DEFAULT_POLICY,
      capacity: preset === "focused" ? 100 : preset === "coverage" ? 750 : 250,
      threshold: preset === "threshold" ? data.summary.selected_threshold : 0,
    };
    updateURL();
    render();
  }
  const action = target.getAttribute("data-action");
  if (action === "export-audit")
    download(
      "riskqueue-analysis.json",
      JSON.stringify(
        {
          ...auditSnapshot(data, policy),
          baseline: auditSnapshot(data, baseline),
        },
        null,
        2,
      ) + "\n",
      "application/json",
    );
  if (action === "export-queue")
    download(
      "riskqueue-queue.csv",
      queueCSV(filteredQueue()),
      "text/csv;charset=utf-8",
    );
  if (action === "previous") {
    queuePage--;
    renderContent();
  }
  if (action === "next") {
    queuePage++;
    renderContent();
  }
  if (action === "reset-filters") {
    search = "";
    typeFilter = "all";
    bandFilter = "all";
    statusFilter = "all";
    queuePage = 1;
    updateURL();
    render();
  }
  if (action === "toggle-loss") {
    showLoss = !showLoss;
    if (!showLoss && !showProbability) showProbability = true;
    renderContent();
    /** @type {HTMLElement|null} */ (
      el("main").querySelector('[data-action="toggle-loss"]')
    )?.focus();
  }
  if (action === "toggle-probability") {
    showProbability = !showProbability;
    if (!showLoss && !showProbability) showLoss = true;
    renderContent();
    /** @type {HTMLElement|null} */ (
      el("main").querySelector('[data-action="toggle-probability"]')
    )?.focus();
  }
  if (action === "freeze-baseline") {
    baseline = { ...policy };
    updateURL();
    renderContent();
    toast("Baseline saved. Change the alternative to compare.");
  }
  if (action === "reset-policy") {
    policy = { ...DEFAULT_POLICY };
    updateURL();
    render();
  }
  if (action === "export-case") {
    const row = evaluate(data.transactions, policy).queue.find(
      (r) => r.transaction_id === activeCase,
    );
    download(
      `${activeCase}-evidence.json`,
      JSON.stringify(
        {
          provenance: data.provenance,
          policy,
          case: row,
          localReview: notes[activeCase] ?? null,
          explanation:
            "Ranking arithmetic and observed historical inputs; no model attribution claimed.",
        },
        null,
        2,
      ),
      "application/json",
    );
  }
});

document.addEventListener("keydown", (event) => {
  if (
    event.target instanceof Element &&
    event.target.hasAttribute("data-capacity") &&
    (event.key === "Enter" || event.key === " ")
  ) {
    event.preventDefault();
    const capacity = event.target.getAttribute("data-capacity");
    chartCapacity(Number(capacity));
    /** @type {SVGElement|null} */ (
      el("main").querySelector(`[data-capacity="${capacity}"]`)
    )?.focus();
  }
});

document.addEventListener("input", (event) => {
  if (!(event.target instanceof HTMLInputElement)) return;
  if (event.target.id === "quick-capacity")
    chartCapacity(Number(event.target.value));
  if (event.target.id === "queue-search") {
    search = event.target.value;
    queuePage = 1;
    renderContent();
  }
});

document.addEventListener("change", (event) => {
  if (!(
    event.target instanceof HTMLInputElement ||
    event.target instanceof HTMLSelectElement
  ))
    return;
  if (event.target.closest("#policy-controls")) {
    const form = /** @type {HTMLFormElement} */ (el("policy-controls"));
    if (!form.reportValidity()) return;
    policy = normalizePolicy(
      {
        capacity: Number(input("policy-capacity").value),
        threshold: Number(input("policy-threshold").value),
        reviewCost: Number(input("policy-cost").value),
        lossFraction: Number(input("policy-loss").value),
        strategy: /** @type {import('./domain.js').Strategy} */ (
          input("policy-strategy").value
        ),
      },
      data.transactions.length,
    );
    queuePage = 1;
    updateURL();
    renderContent();
  }
  if (event.target.id === "queue-type") typeFilter = event.target.value;
  if (event.target.id === "queue-band") bandFilter = event.target.value;
  if (event.target.id === "queue-status") statusFilter = event.target.value;
  if (["queue-type", "queue-band", "queue-status"].includes(event.target.id)) {
    queuePage = 1;
    updateURL();
    renderContent();
  }
});

document.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (event.target instanceof Element && event.target.id === "policy-controls")
    return;
  if (event.target instanceof Element && event.target.id === "note-form") {
    const review = {
      status: input("case-status").value,
      note: input("case-note").value,
    };
    notes[activeCase] = review;
    try {
      localStorage.setItem(
        `riskqueue-reviews-${data.provenance.sha256}`,
        JSON.stringify(notes),
      );
      toast("Review saved in this browser.");
    } catch {
      toast("Browser storage unavailable. Review kept for this session only.");
    }
    /** @type {HTMLDialogElement} */ (el("case-dialog")).close();
    renderContent();
    const caseButton = [...el("main").querySelectorAll("[data-case]")].find(
      (button) => button.getAttribute("data-case") === activeCase,
    );
    const restoreTarget =
      caseButton instanceof HTMLElement ? caseButton : el("main");
    restoreTarget.focus();
  }
  if (event.target instanceof Element && event.target.id === "score-form") {
    const button = /** @type {HTMLButtonElement} */ (el("score-submit"));
    button.disabled = true;
    button.textContent = "Calculating…";
    el("score-error").hidden = true;
    try {
      const result = await demoScore({
        transaction_id: input("score-id").value,
        step: Number(input("score-step").value),
        type: input("score-type").value,
        amount: Number(input("score-amount").value),
      });
      el("score-output").innerHTML =
        `<div class="score-result"><span class="tag ${result.band}">${escape(result.band)} · DEVELOPMENT HEURISTIC</span><div class="kpi-number">${pct(result.probability, 3)}</div><p>Development probability · ${money(result.loss, 2)} expected exposure</p><div class="formula">${Object.entries(
          result.components,
        )
          .map(([name, value]) => `${escape(name)}: ${pct(value, 3)}`)
          .join(
            "<br/>",
          )}<br/>sum → capped at 99% → ${pct(result.probability, 3)}</div><p class="fine-print">Same ID and inputs produce the same result. This is not the trained model's probability.</p></div>`;
    } catch (error) {
      el("score-error").textContent =
        error instanceof Error
          ? error.message
          : "Scoring failed. Please try again.";
      el("score-error").hidden = false;
    } finally {
      button.disabled = false;
      button.innerHTML =
        'Calculate development score <span aria-hidden="true">↗</span>';
    }
  }
});

el("share-button").addEventListener("click", () => {
  if (data) void share();
});
window.addEventListener("hashchange", () => {
  if (!data) return;
  const next = location.hash.slice(1);
  page = Object.hasOwn(pages, next) ? /** @type {Page} */ (next) : "overview";
  updateURL();
  render();
  el("main").focus();
  window.scrollTo({ top: 0, behavior: "instant" });
});

async function load() {
  try {
    const response = await fetch("./data.json");
    if (!response.ok)
      throw new Error(`Snapshot request returned ${response.status}`);
    data = await response.json();
    if (
      data.schemaVersion !== 1 ||
      !Array.isArray(data.transactions) ||
      !data.transactions.length
    )
      throw new Error("This snapshot format is not supported.");
    const params = new URLSearchParams(location.search);
    policy = policyFromParams(params, data.transactions.length);
    const baselineParams = new URLSearchParams();
    for (const key of ["capacity", "threshold", "cost", "loss", "strategy"]) {
      const value = params.get(`b_${key}`);
      if (value !== null) baselineParams.set(key, value);
    }
    baseline = baselineParams.size
      ? policyFromParams(baselineParams, data.transactions.length)
      : { ...DEFAULT_POLICY, strategy: "probability" };
    typeFilter = TYPES.includes(params.get("type") ?? "")
      ? (params.get("type") ?? "all")
      : "all";
    bandFilter = ["critical", "high", "guarded", "low"].includes(
      params.get("band") ?? "",
    )
      ? (params.get("band") ?? "all")
      : "all";
    try {
      const saved = JSON.parse(
        localStorage.getItem(`riskqueue-reviews-${data.provenance.sha256}`) ??
          "{}",
      );
      if (saved && typeof saved === "object" && !Array.isArray(saved)) {
        for (const [id, value] of Object.entries(saved)) {
          if (
            value &&
            typeof value === "object" &&
            Object.hasOwn(value, "status") &&
            Object.hasOwn(value, "note")
          ) {
            const review = /** @type {{status:unknown,note:unknown}} */ (value);
            if (
              typeof review.status === "string" &&
              ["unreviewed", "investigating", "resolved"].includes(
                review.status,
              ) &&
              typeof review.note === "string"
            )
              notes[id] = {
                status: review.status,
                note: review.note.slice(0, 2000),
              };
          }
        }
      }
    } catch {
      notes = {};
    }
    const hash = location.hash.slice(1);
    page = Object.hasOwn(pages, hash) ? /** @type {Page} */ (hash) : "overview";
    render();
    updateURL();
  } catch (error) {
    el("main").innerHTML =
      '<div class="loading-state"><div class="loading-symbol">!</div><h1>The snapshot could not load.</h1><p>Check your connection and try loading the evaluation again.</p><p class="error-text" id="load-error"></p><button class="button primary" id="retry">Try again</button></div>';
    el("load-error").textContent =
      error instanceof Error ? error.message : "Unknown loading error";
    el("retry").addEventListener("click", () => void load());
  }
}
void load();
