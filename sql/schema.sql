CREATE TABLE model_versions (
    model_version VARCHAR(80) PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL,
    algorithm VARCHAR(120) NOT NULL,
    training_end_step INTEGER NOT NULL,
    average_precision DOUBLE PRECISION NOT NULL,
    roc_auc DOUBLE PRECISION NOT NULL,
    brier DOUBLE PRECISION NOT NULL,
    decision_threshold DOUBLE PRECISION NOT NULL,
    git_commit CHAR(40)
);

CREATE TABLE prediction_events (
    prediction_id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    transaction_id VARCHAR(120) NOT NULL,
    model_version VARCHAR(80) NOT NULL REFERENCES model_versions(model_version),
    step INTEGER NOT NULL,
    transaction_type VARCHAR(20) NOT NULL,
    amount NUMERIC(18,2) NOT NULL CHECK (amount >= 0),
    fraud_probability DOUBLE PRECISION NOT NULL CHECK (fraud_probability BETWEEN 0 AND 1),
    risk_band VARCHAR(20) NOT NULL,
    review_priority_score DOUBLE PRECISION NOT NULL,
    decision VARCHAR(20) NOT NULL
);

CREATE TABLE processed_events (
    event_id VARCHAR(128) PRIMARY KEY,
    idempotency_key CHAR(64) NOT NULL UNIQUE,
    batch_id VARCHAR(128) NOT NULL,
    source VARCHAR(120) NOT NULL,
    object_bucket VARCHAR(255) NOT NULL,
    object_key VARCHAR(1024) NOT NULL,
    status VARCHAR(20) NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    transaction_count INTEGER NOT NULL DEFAULT 0 CHECK (transaction_count >= 0)
);

CREATE TABLE operational_transactions (
    transaction_id VARCHAR(120) PRIMARY KEY,
    event_id VARCHAR(128) NOT NULL REFERENCES processed_events(event_id),
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    step INTEGER NOT NULL CHECK (step >= 0),
    transaction_type VARCHAR(20) NOT NULL,
    amount NUMERIC(18,2) NOT NULL CHECK (amount >= 0),
    sender_id VARCHAR(120) NOT NULL,
    recipient_id VARCHAR(120) NOT NULL,
    fraud_probability DOUBLE PRECISION NOT NULL CHECK (fraud_probability BETWEEN 0 AND 1),
    expected_loss NUMERIC(18,2) NOT NULL CHECK (expected_loss >= 0),
    risk_band VARCHAR(20) NOT NULL,
    decision VARCHAR(20) NOT NULL,
    model_version VARCHAR(80) NOT NULL
);

CREATE TABLE review_queue (
    queue_id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    transaction_id VARCHAR(120) NOT NULL,
    event_id VARCHAR(128) REFERENCES processed_events(event_id),
    queue_date DATE NOT NULL,
    rank INTEGER NOT NULL,
    expected_loss NUMERIC(18,2) NOT NULL,
    priority_score DOUBLE PRECISION NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    UNIQUE (transaction_id)
);

CREATE INDEX ix_prediction_events_created_at ON prediction_events(created_at);
CREATE INDEX ix_prediction_events_transaction_id ON prediction_events(transaction_id);
CREATE INDEX ix_review_queue_date_status ON review_queue(queue_date, status);
CREATE INDEX ix_processed_events_batch_id ON processed_events(batch_id);
CREATE INDEX ix_processed_events_status ON processed_events(status);
CREATE INDEX ix_operational_transactions_event_id ON operational_transactions(event_id);
