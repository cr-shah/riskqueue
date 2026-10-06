from __future__ import annotations

import os
import re
import uuid
from collections.abc import Callable, Iterable
from typing import Protocol


class AnalyticalWarehouse(Protocol):
    def merge_scoring_history(self, rows: Iterable[dict]) -> int: ...

    def refresh_daily_model_monitoring(self) -> None: ...


class DisabledWarehouse:
    def merge_scoring_history(self, rows: Iterable[dict]) -> int:
        return 0

    def refresh_daily_model_monitoring(self) -> None:
        return None


def _identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", value):
        raise ValueError(f"Unsafe Snowflake identifier: {value}")
    return value.upper()


class SnowflakeWarehouse:
    """Incrementally merge scoring history without affecting request latency."""

    COLUMNS = (
        "EVENT_ID",
        "TRANSACTION_ID",
        "INGESTED_AT",
        "STEP",
        "TRANSACTION_TYPE",
        "AMOUNT",
        "FRAUD_PROBABILITY",
        "EXPECTED_LOSS",
        "RISK_BAND",
        "DECISION",
        "MODEL_VERSION",
    )

    def __init__(
        self,
        connection_factory: Callable | None = None,
        *,
        database: str | None = None,
        schema: str | None = None,
    ):
        self.database = _identifier(database or os.environ["SNOWFLAKE_DATABASE"])
        self.schema = _identifier(schema or os.getenv("SNOWFLAKE_SCHEMA", "RISKQUEUE_ANALYTICS"))
        self.connection_factory = connection_factory or self._connect

    @staticmethod
    def _connect():
        import snowflake.connector

        parameters = {
            "account": os.environ["SNOWFLAKE_ACCOUNT"],
            "user": os.environ["SNOWFLAKE_USER"],
            "password": os.environ["SNOWFLAKE_PASSWORD"],
            "warehouse": os.environ["SNOWFLAKE_WAREHOUSE"],
            "database": os.environ["SNOWFLAKE_DATABASE"],
            "schema": os.getenv("SNOWFLAKE_SCHEMA", "RISKQUEUE_ANALYTICS"),
        }
        if role := os.getenv("SNOWFLAKE_ROLE"):
            parameters["role"] = role
        return snowflake.connector.connect(**parameters)

    def merge_scoring_history(self, rows: Iterable[dict]) -> int:
        values = list(rows)
        if not values:
            return 0
        stage = _identifier(f"SCORING_STAGE_{uuid.uuid4().hex}")
        table = f"{self.database}.{self.schema}.SCORING_HISTORY"
        connection = self.connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                f"CREATE TEMPORARY TABLE {stage} LIKE {table}"  # noqa: S608
            )
            placeholders = ", ".join(["%s"] * len(self.COLUMNS))
            cursor.executemany(
                f"INSERT INTO {stage} ({', '.join(self.COLUMNS)}) VALUES ({placeholders})",  # noqa: S608
                [tuple(row[column.lower()] for column in self.COLUMNS) for row in values],
            )
            cursor.execute(
                f"""
                MERGE INTO {table} target
                USING {stage} source
                  ON target.EVENT_ID = source.EVENT_ID
                 AND target.TRANSACTION_ID = source.TRANSACTION_ID
                WHEN MATCHED THEN UPDATE SET
                    {", ".join(f"target.{column} = source.{column}" for column in self.COLUMNS if column not in {"EVENT_ID", "TRANSACTION_ID"})}
                WHEN NOT MATCHED THEN INSERT ({", ".join(self.COLUMNS)})
                VALUES ({", ".join(f"source.{column}" for column in self.COLUMNS)})
                """  # noqa: S608
            )
            connection.commit()
            return len(values)
        finally:
            cursor.close()
            connection.close()

    def refresh_daily_model_monitoring(self) -> None:
        connection = self.connection_factory()
        cursor = connection.cursor()
        target = f"{self.database}.{self.schema}.DAILY_MODEL_MONITORING"
        source = f"{self.database}.{self.schema}.DAILY_SCORING_SUMMARY"
        try:
            cursor.execute(
                f"""
                MERGE INTO {target} target
                USING {source} source
                  ON target.MONITOR_DATE = source.SCORE_DATE
                 AND target.MODEL_VERSION = source.MODEL_VERSION
                WHEN MATCHED THEN UPDATE SET
                    TRANSACTIONS = source.TRANSACTIONS,
                    AVERAGE_SCORE = source.AVERAGE_SCORE,
                    REVIEW_RATE = source.REVIEW_RATE,
                    AVERAGE_EXPECTED_LOSS = source.AVERAGE_EXPECTED_LOSS,
                    REFRESHED_AT = CURRENT_TIMESTAMP()
                WHEN NOT MATCHED THEN INSERT (
                    MONITOR_DATE, MODEL_VERSION, TRANSACTIONS, AVERAGE_SCORE,
                    REVIEW_RATE, AVERAGE_EXPECTED_LOSS
                ) VALUES (
                    source.SCORE_DATE, source.MODEL_VERSION, source.TRANSACTIONS,
                    source.AVERAGE_SCORE, source.REVIEW_RATE, source.AVERAGE_EXPECTED_LOSS
                )
                """  # noqa: S608
            )
            connection.commit()
        finally:
            cursor.close()
            connection.close()


def build_warehouse() -> AnalyticalWarehouse:
    if os.getenv("SNOWFLAKE_ENABLED", "false").lower() not in {"1", "true", "yes"}:
        return DisabledWarehouse()
    return SnowflakeWarehouse()
