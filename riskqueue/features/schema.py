from riskqueue.config import LEAKAGE_COLUMNS

FEATURE_VERSION = "behavioral-v1"

STATIC_FEATURES = ["amount", "hour", "day_index"]
BEHAVIORAL_FEATURES = [
    "sender_count_1h",
    "sender_count_6h",
    "sender_count_24h",
    "sender_total_amount_24h",
    "sender_average_amount_24h",
    "sender_historical_median_amount",
    "sender_unique_recipients_24h",
    "sender_hours_since_prior",
    "amount_to_sender_median",
    "recipient_incoming_count_24h",
    "recipient_unique_senders_24h",
    "recipient_total_received_24h",
    "recipient_hours_since_prior",
    "prior_pair_count",
    "first_time_recipient",
]
CATEGORICAL_FEATURES = ["type"]
MODEL_FEATURES = STATIC_FEATURES + BEHAVIORAL_FEATURES + CATEGORICAL_FEATURES


def assert_leakage_safe(columns: list[str]) -> None:
    overlap = LEAKAGE_COLUMNS.intersection(columns)
    if overlap:
        raise ValueError(f"Leakage-prone model features: {sorted(overlap)}")
