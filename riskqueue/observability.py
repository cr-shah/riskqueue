from __future__ import annotations

import json
import logging
from datetime import UTC, datetime


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), format="%(message)s")


def log_event(logger: logging.Logger, message: str, **fields) -> None:
    safe = {key: value for key, value in fields.items() if value is not None}
    logger.info(
        json.dumps(
            {"timestamp": datetime.now(UTC).isoformat(), "message": message, **safe},
            default=str,
            sort_keys=True,
        )
    )
