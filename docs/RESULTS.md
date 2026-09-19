# Results

The checked-in report is a **deterministic synthetic demo**, included so the repository and dashboard can be inspected before downloading PaySim. Run the PaySim pipeline before making portfolio claims.

## What the report covers

1. Rare-event prevalence and chronological 70/15/15 partitioning.
2. Logistic regression versus nonlinear gradient boosting.
3. Average precision, ROC-AUC, Brier, log loss, precision, recall, and F1.
4. 0.50, validation F1-optimal, and validation cost-optimized thresholds.
5. Probability versus expected-loss review queues across seven capacity levels.
6. A shifted-batch PSI monitoring demonstration.
7. A local in-process API benchmark: batch 1,000 median 7.50 ms and p95 10.90 ms.

Generated values live in `artifacts/figures/summary.json`; tables live beside it as CSV files. This separation keeps prose from becoming a second, stale source of truth.

## Final recommendation

Use the model selected by validation average precision, calibrate before interpreting scores financially, and choose the queue policy against the operation’s explicit objective. Re-evaluate those choices on PaySim and do not deploy this educational system in a real financial workflow.
