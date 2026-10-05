"""Publish an allowlisted synthetic evaluation snapshot, never operational databases."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NUMERIC_FIELDS = (
    "step",
    "amount",
    "isFraud",
    "fraud_probability",
    "sender_count_24h",
    "sender_historical_median_amount",
    "amount_to_sender_median",
    "recipient_unique_senders_24h",
    "first_time_recipient",
    "prior_pair_count",
)
PUBLIC_FIELDS = ("transaction_id", "type", *NUMERIC_FIELDS)


def build_payload(source: Path) -> dict:
    summary = json.loads((source / "summary.json").read_text())
    raw = (source / "scored_test.csv").read_bytes()
    if not summary["dataset"].startswith("deterministic synthetic demo"):
        raise ValueError("Only the explicitly labeled synthetic demo may be published")
    rows = []
    for record in csv.DictReader(raw.decode().splitlines()):
        row = {key: record[key] for key in PUBLIC_FIELDS}
        if not row["transaction_id"].startswith("demo-"):
            raise ValueError("Public evaluation must contain only demo transaction IDs")
        if row["type"] not in {"PAYMENT", "TRANSFER", "CASH_OUT", "CASH_IN", "DEBIT"}:
            raise ValueError("Unsupported transaction type")
        for key in NUMERIC_FIELDS:
            row[key] = float(row[key])
            if not math.isfinite(row[key]) or row[key] < 0:
                raise ValueError(f"Invalid {key} in public snapshot")
        if row["fraud_probability"] > 1 or row["isFraud"] not in (0, 1):
            raise ValueError("Invalid probability or label")
        rows.append(row)
    if len(rows) != summary["transactions"] or len({r["transaction_id"] for r in rows}) != len(
        rows
    ):
        raise ValueError("Snapshot count or transaction IDs do not match")
    if sum(r["isFraud"] for r in rows) != summary["fraud_cases"]:
        raise ValueError("Snapshot labels do not match summary")
    return {
        "schemaVersion": 1,
        "provenance": {
            "dataset": "Deterministic synthetic demo",
            "source": "artifacts/figures/scored_test.csv",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "seed": 42,
            "split": "Chronological 70 / 15 / 15; equal-hour boundaries kept together",
            "model": summary["best_model"],
            "calibration": "Sigmoid calibration on chronological validation data",
            "notice": "Historical evaluation, not live operations or real-world effectiveness.",
        },
        "summary": summary,
        "transactions": rows,
    }


def build_site(
    source: Path = ROOT / "artifacts/figures", output: Path = ROOT / "dist/site"
) -> None:
    payload = build_payload(source)
    output.mkdir(parents=True, exist_ok=True)
    allowed = {
        "index.html",
        "styles.css",
        "app.js",
        "domain.js",
        "favicon.svg",
        "data.json",
        ".nojekyll",
    }
    if any(path.name not in allowed for path in output.iterdir()):
        raise ValueError("Public output contains unexpected files; use an empty output directory")
    for name in ("index.html", "styles.css", "app.js", "domain.js", "favicon.svg"):
        shutil.copyfile(ROOT / "website" / name, output / name)
    (output / "data.json").write_text(
        json.dumps(payload, separators=(",", ":"), allow_nan=False) + "\n"
    )
    (output / ".nojekyll").touch()
    print(f"Built RiskQueue: {len(payload['transactions']):,} synthetic evaluation rows")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist/site")
    build_site(output=parser.parse_args().output)
