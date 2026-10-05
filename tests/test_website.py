import csv
import json
from pathlib import Path

import pytest

from scripts.build_website import PUBLIC_FIELDS, build_payload, build_site

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "artifacts/figures"


def test_snapshot_is_deterministic_and_excludes_account_identifiers():
    payload = build_payload(SOURCE)
    assert payload == build_payload(SOURCE)
    assert len(payload["transactions"]) == payload["summary"]["transactions"]
    assert len(payload["provenance"]["sha256"]) == 64
    assert set(payload["transactions"][0]) == set(PUBLIC_FIELDS)
    assert "nameOrig" not in json.dumps(payload)
    assert "nameDest" not in json.dumps(payload)


def test_site_packages_only_public_files(tmp_path):
    build_site(output=tmp_path)
    assert {p.name for p in tmp_path.iterdir()} == {
        "index.html",
        "styles.css",
        "app.js",
        "domain.js",
        "favicon.svg",
        "data.json",
        ".nojekyll",
    }


def test_build_refuses_to_publish_unexpected_files(tmp_path):
    (tmp_path / "private.txt").write_text("private placeholder")
    with pytest.raises(ValueError, match="unexpected files"):
        build_site(output=tmp_path)


@pytest.mark.parametrize("mutation", ["dataset", "id", "probability", "count", "label", "nan"])
def test_public_snapshot_refuses_invalid_or_non_demo_data(tmp_path, mutation):
    summary = json.loads((SOURCE / "summary.json").read_text())
    rows = list(csv.DictReader((SOURCE / "scored_test.csv").read_text().splitlines()))
    if mutation == "dataset":
        summary["dataset"] = "private operational transactions"
    elif mutation == "id":
        rows[0]["transaction_id"] = "actual-account-transaction"
    elif mutation == "probability":
        rows[0]["fraud_probability"] = "1.2"
    elif mutation == "count":
        summary["transactions"] -= 1
    elif mutation == "label":
        rows[0]["isFraud"] = "2"
    elif mutation == "nan":
        rows[0]["amount"] = "nan"
    (tmp_path / "summary.json").write_text(json.dumps(summary))
    with (tmp_path / "scored_test.csv").open("w") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(ValueError):
        build_payload(tmp_path)
