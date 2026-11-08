import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import {
  DEFAULT_POLICY,
  auditSnapshot,
  buildQueue,
  capacityTradeoff,
  compareSelections,
  demoScore,
  evaluate,
  normalizePolicy,
  policyFromParams,
  policyParams,
  queueCSV,
  riskBand,
} from "../../website/domain.js";
const data = JSON.parse(
  readFileSync(new URL("../../dist/site/data.json", import.meta.url)),
);

test("every checked-in capacity result agrees with browser ranking", () => {
  for (const row of data.summary.capacity_results) {
    const actual = evaluate(data.transactions, {
      ...DEFAULT_POLICY,
      capacity: row.capacity,
      strategy: row.strategy,
    });
    assert.ok(
      Math.abs(actual.valueCapture - row.fraud_value_capture) < 1e-12,
      `${row.strategy} at ${row.capacity}`,
    );
  }
});
test("uncapped threshold policy agrees with Python-generated results", () => {
  for (const row of data.summary.threshold_policies) {
    const result = evaluate(data.transactions, {
      ...DEFAULT_POLICY,
      capacity: data.transactions.length,
      threshold: row.threshold,
    });
    assert.equal(result.reviews, row.reviews);
    assert.ok(Math.abs(result.modeledCost - row.modeled_cost) < 1e-8);
    assert.equal(result.precision, row.precision);
    assert.equal(result.recall, row.recall);
  }
});
test("capacity is exact and stable at arbitrary values, including zero", () => {
  assert.equal(
    buildQueue(data.transactions, { ...DEFAULT_POLICY, capacity: 137 }).length,
    137,
  );
  assert.equal(
    buildQueue(data.transactions, { ...DEFAULT_POLICY, capacity: 0 }).length,
    0,
  );
  assert.equal(
    evaluate(data.transactions, { ...DEFAULT_POLICY, capacity: 0 })
      .valueCapture,
    0,
  );
  assert.equal(
    evaluate(data.transactions, {
      ...DEFAULT_POLICY,
      capacity: data.transactions.length,
    }).valueCapture,
    1,
  );
});
test("scenario movement counts only cases that enter or leave the selected queue", () => {
  const baseline = {
    ...DEFAULT_POLICY,
    strategy: "probability",
    capacity: 250,
  };
  const alternative = { ...DEFAULT_POLICY, capacity: 250 };
  const movement = compareSelections(data.transactions, baseline, alternative);
  assert.ok(movement.added.length > 0);
  assert.equal(movement.added.length, movement.removed.length);
  assert.equal(movement.shared + movement.added.length, 250);
  assert.equal(movement.shared + movement.removed.length, 250);
  assert.equal(
    compareSelections(data.transactions, baseline, baseline).added.length,
    0,
  );
  assert.equal(
    compareSelections(data.transactions, baseline, baseline).removed.length,
    0,
  );
  assert.ok(
    movement.added.every(
      (row) =>
        !movement.removed.some(
          (other) => other.transaction_id === row.transaction_id,
        ),
    ),
  );
});
test("policy costs and loss fractions change values but preserve fixed-cost ranking", () => {
  const a = evaluate(data.transactions, DEFAULT_POLICY),
    b = evaluate(data.transactions, {
      ...DEFAULT_POLICY,
      reviewCost: 8,
      lossFraction: 0.5,
    });
  assert.deepEqual(
    a.queue.map((r) => r.transaction_id),
    b.queue.map((r) => r.transaction_id),
  );
  assert.equal(b.reviewSpend, a.reviewSpend * 2);
  assert.equal(b.missedLoss, a.missedLoss * 0.5);
  assert.equal(b.expectedExposure, a.expectedExposure * 0.5);
});
test("stable priority ties use amount and original input order", () => {
  const rows = data.transactions.slice(0, 3).map((r, i) => ({
    ...r,
    amount: i === 0 ? 100 : 200,
    fraud_probability: 0.5,
  }));
  assert.deepEqual(
    buildQueue(rows, { ...DEFAULT_POLICY, strategy: "probability" }).map(
      (r) => r.transaction_id,
    ),
    [rows[1].transaction_id, rows[2].transaction_id, rows[0].transaction_id],
  );
});
test("untrusted share parameters are bounded and finite", () => {
  const policy = policyFromParams(
    new URLSearchParams(
      "capacity=-50&threshold=NaN&cost=Infinity&loss=10&strategy=constructor",
    ),
    1749,
  );
  assert.deepEqual(policy, { ...DEFAULT_POLICY, capacity: 0 });
  assert.deepEqual(
    policyFromParams(policyParams(DEFAULT_POLICY), 1749),
    DEFAULT_POLICY,
  );
  assert.equal(
    normalizePolicy({ ...DEFAULT_POLICY, capacity: 1e20 }, 1749).capacity,
    1749,
  );
});
test("probability bands include precise boundary values", () => {
  assert.equal(riskBand(0.1), "guarded");
  assert.equal(riskBand(0.4), "high");
  assert.equal(riskBand(0.75), "critical");
});
test("development score matches known Python fixture", async () => {
  const result = await demoScore({
    transaction_id: "lab-transfer-001",
    step: 26,
    type: "TRANSFER",
    amount: 4200,
  });
  // SHA-256 ID term is checked independently against node crypto.
  const { createHash } = await import("node:crypto");
  const jitter =
    (parseInt(
      createHash("sha256").update("lab-transfer-001").digest("hex").slice(0, 4),
      16,
    ) /
      65535) *
    0.04;
  assert.equal(result.probability, 0.015 + 0.34 + 0.042 + 0.08 + jitter);
  assert.deepEqual(
    await demoScore({
      transaction_id: "lab-transfer-001",
      step: 26,
      type: "TRANSFER",
      amount: 4200,
    }),
    result,
  );
  await assert.rejects(
    demoScore({ transaction_id: "x", step: 1.5, type: "TRANSFER", amount: 20 }),
  );
  await assert.rejects(
    demoScore({
      transaction_id: "x",
      step: 2,
      type: "TRANSFER",
      amount: Infinity,
    }),
  );
});
test("CSV escapes quotes, newlines, and formula injection", () => {
  const row = buildQueue(data.transactions, DEFAULT_POLICY)[0];
  const csv = queueCSV([
    { ...row, transaction_id: '=HYPERLINK("evil")\nnext' },
  ]);
  assert.ok(csv.includes('"\'=HYPERLINK(""evil"")\nnext"'));
});
test("audit exports are reproducible and carry snapshot identity and selected IDs", () => {
  const a = auditSnapshot(data, DEFAULT_POLICY);
  assert.deepEqual(a, auditSnapshot(data, DEFAULT_POLICY));
  assert.equal(a.selectedTransactionIds.length, 250);
  assert.equal(a.provenance.sha256, data.provenance.sha256);
  assert.equal(a.assumptions.reviewPreventsFraudLoss, true);
});

test("capacity sweep prices every eligible prefix and counts excluded fraud as missed", () => {
  const rows = [
    { amount: 100, fraud_probability: 0.9, isFraud: 0 },
    { amount: 40, fraud_probability: 0.8, isFraud: 1 },
    { amount: 5, fraud_probability: 0.7, isFraud: 0 },
    { amount: 60, fraud_probability: 0.1, isFraud: 1 },
  ];
  const policy = {
    ...DEFAULT_POLICY,
    strategy: "probability",
    capacity: 1,
    threshold: 0.5,
    lossFraction: 0.5,
  };
  const { points, minimum } = capacityTradeoff(rows, policy);
  assert.deepEqual(
    points.map(({ reviews, valueCapture, modeledCost }) => [
      reviews,
      valueCapture,
      modeledCost,
    ]),
    [
      [0, 0, 50],
      [1, 0, 54],
      [2, 0.4, 38],
      [3, 0.4, 42],
    ],
  );
  assert.equal(minimum.reviews, 2);
  assert.equal(minimum.reviewSpend, 8);
  assert.equal(minimum.missedLoss, 30);
  assert.deepEqual(
    capacityTradeoff(rows, { ...policy, threshold: 1 }).points,
    points.slice(0, 1),
  );
  assert.equal(
    capacityTradeoff(rows, { ...policy, reviewCost: 0, lossFraction: 0 })
      .minimum.reviews,
    0,
  );
});

test("capacity sweep matches active-policy cost and capture on the published cohort", () => {
  const policy = {
    ...DEFAULT_POLICY,
    threshold: 0.23,
    reviewCost: 6.5,
    lossFraction: 0.35,
  };
  const { points, minimum } = capacityTradeoff(data.transactions, policy);
  for (const reviews of [0, 1, minimum.reviews, points.length - 1]) {
    const reference = evaluate(data.transactions, {
      ...policy,
      capacity: reviews,
    });
    assert.equal(points[reviews].modeledCost, reference.modeledCost);
    assert.equal(points[reviews].valueCapture, reference.valueCapture);
  }
});

test("capacity sweep keeps the smaller queue when fractional loss introduces rounding noise", () => {
  const rows = [
    { amount: 10, fraud_probability: 0.9, isFraud: 1 },
    { amount: 0.01, fraud_probability: 0.1, isFraud: 1 },
  ];
  const { minimum } = capacityTradeoff(rows, {
    ...DEFAULT_POLICY,
    strategy: "probability",
    threshold: 0.5,
    reviewCost: 0.5,
    lossFraction: 0.05,
  });
  assert.equal(minimum.reviews, 0);
});
