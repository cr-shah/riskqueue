from datetime import UTC, datetime

from riskqueue.warehouse.snowflake import DisabledWarehouse, SnowflakeWarehouse


class Cursor:
    def __init__(self):
        self.executed = []
        self.executemany_rows = []
        self.closed = False

    def execute(self, sql):
        self.executed.append(sql)

    def executemany(self, sql, rows):
        self.executed.append(sql)
        self.executemany_rows.extend(rows)

    def close(self):
        self.closed = True


class Connection:
    def __init__(self):
        self.cursor_value = Cursor()
        self.committed = False
        self.closed = False

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


def test_snowflake_sink_uses_incremental_merge():
    connection = Connection()
    warehouse = SnowflakeWarehouse(
        lambda: connection, database="analytics", schema="riskqueue_analytics"
    )
    row = {
        "event_id": "event",
        "transaction_id": "tx",
        "ingested_at": datetime.now(UTC),
        "step": 1,
        "transaction_type": "TRANSFER",
        "amount": 100.0,
        "fraud_probability": 0.5,
        "expected_loss": 50.0,
        "risk_band": "high",
        "decision": "review",
        "model_version": "v1",
    }

    assert warehouse.merge_scoring_history([row]) == 1
    assert any("MERGE INTO" in sql for sql in connection.cursor_value.executed)
    assert connection.committed
    assert connection.closed


def test_disabled_warehouse_is_safe_local_default():
    assert DisabledWarehouse().merge_scoring_history([{"ignored": True}]) == 0
