"""JSON file logging for Datadog Agent log collection.

Writes structured JSON logs to logs/serenia.log, which the Datadog Agent
tails and forwards to LaunchDarkly via logs_dd_url.
"""

import logging
import os

from pythonjsonlogger import json as jsonlogger


def init_logging():
    """Set up JSON file logging for the Datadog Agent to collect."""
    log_dir = os.path.join(os.path.dirname(__file__), "..", "..", "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "serenia.log")

    logger = logging.getLogger("serenia")
    logger.setLevel(logging.INFO)

    # JSON file handler — tailed by the Datadog Agent
    file_handler = logging.FileHandler(log_path)
    formatter = jsonlogger.JsonFormatter(
        "%(asctime)s %(name)s %(levelname)s %(message)s",
        rename_fields={"asctime": "timestamp", "levelname": "level"},
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Also log to console for development
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter("[%(name)s] %(message)s"))
    logger.addHandler(console_handler)

    logger.info("Logging initialized", extra={"log_path": log_path})

    return logger
