/** @typedef {'probability'|'expected_loss'|'expected_review_value'} Strategy */
/** @typedef {{capacity:number, threshold:number, reviewCost:number, lossFraction:number, strategy:Strategy}} Policy */
/** @typedef {{transaction_id:string, type:string, step:number, amount:number, isFraud:number, fraud_probability:number, sender_count_24h:number, sender_historical_median_amount:number, amount_to_sender_median:number, recipient_unique_senders_24h:number, first_time_recipient:number, prior_pair_count:number}} Transaction */
/** @typedef {Transaction & {expected_loss:number, expected_review_value:number, priority_score:number, rank:number, risk_band:string}} Case */
/** @typedef {{average_precision:number, roc_auc:number, brier:number, log_loss:number, precision:number, recall:number, f1:number}} ModelMetrics */
/** @typedef {{schemaVersion:number, provenance:{dataset:string, source:string, sha256:string, seed:number, split:string, model:string, calibration:string, notice:string}, summary:{transactions:number, fraud_cases:number, fraud_rate:number, best_model:string, selected_threshold:number, metrics:Record<string,ModelMetrics>, monitoring:{amount_psi:number,status:string}, capacity_results:Array<{strategy:string,capacity:number,fraud_value_capture:number}>, threshold_policies:Array<{policy:string,threshold:number,reviews:number,precision:number,recall:number,modeled_cost:number}>}, transactions:Transaction[]}} Dataset */

/** @type {Policy} */
export const DEFAULT_POLICY = {
  capacity: 250,
  threshold: 0,
  reviewCost: 4,
  lossFraction: 1,
  strategy: "expected_loss",
};
export const TYPES = ["CASH_IN", "CASH_OUT", "DEBIT", "PAYMENT", "TRANSFER"];
export const STRATEGIES = {
  probability: "Fraud probability",
  expected_loss: "Expected loss",
  expected_review_value: "Net review value",
};

/** @param {unknown} value @param {number} fallback @param {number} min @param {number} max */
function bounded(value, fallback, min, max) {
  if (value === null || value === undefined || value === "") return fallback;
  const number = Number(value);
  return Number.isFinite(number)
    ? Math.min(max, Math.max(min, number))
    : fallback;
}

/** @param {Partial<Policy>} policy @param {number} length @returns {Policy} */
export function normalizePolicy(policy, length) {
  return {
    capacity: Math.round(bounded(policy.capacity, 250, 0, length)),
    threshold: bounded(policy.threshold, 0, 0, 1),
    reviewCost: bounded(policy.reviewCost, 4, 0, 1000),
    lossFraction: bounded(policy.lossFraction, 1, 0, 1),
    strategy:
      policy.strategy && Object.hasOwn(STRATEGIES, policy.strategy)
        ? policy.strategy
        : "expected_loss",
  };
}

/** @param {number} probability */
export function riskBand(probability) {
  if (probability >= 0.75) return "critical";
  if (probability >= 0.4) return "high";
  if (probability >= 0.1) return "guarded";
  return "low";
}

/** Exact stable top-k policy, including the Python engine's amount tie-break. @param {Transaction[]} rows @param {Policy} policy @returns {Case[]} */
export function buildQueue(rows, policy) {
  const p = normalizePolicy(policy, rows.length);
  return rows
    .filter((row) => row.fraud_probability >= p.threshold)
    .map((row) => {
      const loss = row.fraud_probability * row.amount * p.lossFraction;
      const value = loss - p.reviewCost;
      return {
        ...row,
        expected_loss: loss,
        expected_review_value: value,
        priority_score:
          p.strategy === "probability"
            ? row.fraud_probability
            : p.strategy === "expected_loss"
              ? loss
              : value,
        rank: 0,
        risk_band: riskBand(row.fraud_probability),
      };
    })
    .sort((a, b) => b.priority_score - a.priority_score || b.amount - a.amount)
    .slice(0, p.capacity)
    .map((row, index) => ({ ...row, rank: index + 1 }));
}

/** Retrospective costs assume a reviewed fraudulent transaction's loss is fully prevented. @param {Transaction[]} rows @param {Policy} policy */
export function evaluate(rows, policy) {
  const p = normalizePolicy(policy, rows.length);
  const queue = buildQueue(rows, p);
  const totalFraud = rows.reduce((sum, row) => sum + row.isFraud, 0);
  const totalFraudValue = rows.reduce(
    (sum, row) => sum + row.amount * row.isFraud,
    0,
  );
  const casesCaptured = queue.reduce((sum, row) => sum + row.isFraud, 0);
  const valueCaptured = queue.reduce(
    (sum, row) => sum + row.amount * row.isFraud,
    0,
  );
  const missedLoss =
    Math.max(0, totalFraudValue - valueCaptured) * p.lossFraction;
  return {
    queue,
    reviews: queue.length,
    eligible: rows.filter((row) => row.fraud_probability >= p.threshold).length,
    totalFraud,
    totalFraudValue,
    casesCaptured,
    valueCaptured,
    precision: queue.length ? casesCaptured / queue.length : 0,
    recall: totalFraud ? casesCaptured / totalFraud : 0,
    valueCapture: totalFraudValue
      ? Math.min(1, valueCaptured / totalFraudValue)
      : 0,
    expectedExposure: queue.reduce((sum, row) => sum + row.expected_loss, 0),
    reviewSpend: queue.length * p.reviewCost,
    missedLoss,
    modeledCost: queue.length * p.reviewCost + missedLoss,
  };
}

/** @param {URLSearchParams} params @param {number} length */
export function policyFromParams(params, length) {
  return normalizePolicy(
    {
      capacity: Number(params.get("capacity") ?? 250),
      threshold: Number(params.get("threshold") ?? 0),
      reviewCost: Number(params.get("cost") ?? 4),
      lossFraction: Number(params.get("loss") ?? 1),
      strategy: /** @type {Strategy} */ (
        params.get("strategy") ?? "expected_loss"
      ),
    },
    length,
  );
}

/** @param {Policy} policy */
export function policyParams(policy) {
  return new URLSearchParams({
    capacity: String(policy.capacity),
    threshold: String(policy.threshold),
    cost: String(policy.reviewCost),
    loss: String(policy.lossFraction),
    strategy: policy.strategy,
  });
}

/** @param {Transaction[]} rows @param {number[]} capacities */
export function captureCurve(rows, capacities) {
  return capacities.map((capacity) => ({
    capacity,
    loss: evaluate(rows, { ...DEFAULT_POLICY, capacity }).valueCapture,
    probability: evaluate(rows, {
      ...DEFAULT_POLICY,
      capacity,
      strategy: "probability",
    }).valueCapture,
  }));
}

/** @param {Transaction[]} rows */
export function calibrationBins(rows) {
  const sorted = [...rows].sort(
    (a, b) => a.fraud_probability - b.fraud_probability,
  );
  return Array.from({ length: 8 }, (_, i) => {
    const group = sorted.slice(
      Math.floor((i * rows.length) / 8),
      Math.floor(((i + 1) * rows.length) / 8),
    );
    return {
      count: group.length,
      predicted:
        group.reduce((s, r) => s + r.fraud_probability, 0) /
        Math.max(1, group.length),
      observed:
        group.reduce((s, r) => s + r.isFraud, 0) / Math.max(1, group.length),
    };
  });
}

/** @param {Transaction[]} rows */
export function precisionRecallCurve(rows) {
  const sorted = [...rows].sort(
    (a, b) => b.fraud_probability - a.fraud_probability,
  );
  const total = rows.reduce((sum, row) => sum + row.isFraud, 0);
  let positives = 0;
  const points = [{ recall: 0, precision: 1 }];
  sorted.forEach((row, i) => {
    positives += row.isFraud;
    if (
      i === sorted.length - 1 ||
      sorted[i + 1].fraud_probability !== row.fraud_probability
    )
      points.push({
        recall: positives / Math.max(1, total),
        precision: positives / (i + 1),
      });
  });
  return points;
}

/** Match riskqueue.scoring.demo_probability; this is a development heuristic, not the trained model. @param {{transaction_id:string,step:number,type:string,amount:number}} input */
export async function demoScore(input) {
  if (
    !input.transaction_id.trim() ||
    input.transaction_id.length > 120 ||
    !TYPES.includes(input.type) ||
    !Number.isInteger(input.step) ||
    input.step < 0 ||
    input.step > 10_000_000 ||
    !Number.isFinite(input.amount) ||
    input.amount < 0 ||
    input.amount > 1e12
  )
    throw new Error(
      "Enter a valid ID, transaction type, non-negative amount (up to $1 trillion), and whole hour (up to 10 million).",
    );
  const hash = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(input.transaction_id),
  );
  const jitter = (new DataView(hash).getUint16(0) / 65535) * 0.04;
  const type = { TRANSFER: 0.34, CASH_OUT: 0.22 }[input.type] ?? 0.03;
  const probability = Math.min(
    0.99,
    0.015 +
      type +
      Math.min(0.42, input.amount / 100_000) +
      (input.step % 24 < 5 ? 0.08 : 0) +
      jitter,
  );
  return {
    probability,
    loss: probability * input.amount,
    band: riskBand(probability),
    components: {
      base: 0.015,
      type,
      amount: Math.min(0.42, input.amount / 100_000),
      hour: input.step % 24 < 5 ? 0.08 : 0,
      jitter,
    },
  };
}

/** Spreadsheet-safe CSV. @param {Case[]} rows */
export function queueCSV(rows) {
  const fields = [
    "rank",
    "transaction_id",
    "type",
    "amount",
    "fraud_probability",
    "expected_loss",
    "expected_review_value",
    "risk_band",
  ];
  /** @param {unknown} value */
  const quote = (value) => {
    let text = String(value);
    if (/^[=+\-@\t\r]/.test(text)) text = `'${text}`;
    return `"${text.replaceAll('"', '""')}"`;
  };
  return [
    fields.join(","),
    ...rows.map((row) =>
      fields
        .map((key) => quote(row[/** @type {keyof Case} */ (key)]))
        .join(","),
    ),
  ].join("\r\n");
}

/** @param {Dataset} data @param {Policy} policy */
export function auditSnapshot(data, policy) {
  const { queue, ...metrics } = evaluate(data.transactions, policy);
  return {
    schemaVersion: 1,
    provenance: data.provenance,
    policy,
    assumptions: {
      reviewPreventsFraudLoss: true,
      lossFraction: policy.lossFraction,
      reviewCost: policy.reviewCost,
    },
    metrics,
    selectedTransactionIds: queue.map((row) => row.transaction_id),
  };
}
