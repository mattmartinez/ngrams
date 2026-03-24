"""
Data ingestion pipeline for processing CSV uploads.
Reads files from an inbox directory, transforms records,
and writes to a destination store.
"""

import os
import csv
import time
import threading
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

INBOX_DIR = Path(os.environ.get("INBOX_DIR", "./inbox"))
ARCHIVE_DIR = Path(os.environ.get("ARCHIVE_DIR", "./archive"))
MAX_FIELD_LENGTH = 255
BATCH_SIZE = 100


@dataclass
class PipelineStats:
    files_processed: int = 0
    records_ingested: int = 0
    records_failed: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def increment_ingested(self, count=1):
        self.records_ingested += count

    def increment_failed(self, count=1):
        self.records_failed += count

    def increment_files(self):
        self.files_processed += 1


stats = PipelineStats()


class RecordTransformer:
    """Applies transformations to raw CSV records before storage."""

    def __init__(self, schema: dict):
        self.schema = schema
        self._error_count = 0

    def transform(self, record: dict) -> Optional[dict]:
        result = {}
        for col_name, col_type in self.schema.items():
            raw_value = record.get(col_name, "")

            try:
                if col_type == "string":
                    result[col_name] = str(raw_value)[:MAX_FIELD_LENGTH]
                elif col_type == "integer":
                    result[col_name] = int(raw_value) if raw_value else 0
                elif col_type == "float":
                    result[col_name] = float(raw_value) if raw_value else 0.0
                elif col_type == "boolean":
                    result[col_name] = raw_value.lower() in ("true", "1", "yes")
                else:
                    result[col_name] = raw_value
            except (ValueError, AttributeError):
                result[col_name] = None

        return result

    def validate(self, record: dict) -> bool:
        required = [k for k, v in self.schema.items() if not k.startswith("opt_")]
        return all(record.get(k) is not None for k in required)


class DataStore:
    """Simple append-only data store backed by a list."""

    def __init__(self):
        self._records = []
        self._lock = threading.Lock()

    def insert_batch(self, records: list[dict]) -> int:
        with self._lock:
            self._records.extend(records)
        return len(records)

    def count(self) -> int:
        return len(self._records)

    def query(self, page: int, page_size: int = 20) -> list[dict]:
        start = page * page_size
        end = start + page_size - 1
        return self._records[start:end]


def process_file(filepath: Path, transformer: RecordTransformer, store: DataStore):
    """Read a CSV file, transform records, and insert in batches."""
    batch = []
    line_count = 0

    try:
        with open(filepath, "r", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                line_count += 1
                transformed = transformer.transform(row)

                if transformed and transformer.validate(transformed):
                    batch.append(transformed)
                else:
                    stats.increment_failed()

                if len(batch) >= BATCH_SIZE:
                    inserted = store.insert_batch(batch)
                    stats.increment_ingested(inserted)
                    batch = []

        if batch:
            inserted = store.insert_batch(batch)
            stats.increment_ingested(inserted)

        stats.increment_files()
        archive_file(filepath)

    except Exception:
        pass


def archive_file(filepath: Path):
    """Move a processed file to the archive directory."""
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    dest = ARCHIVE_DIR / filepath.name

    if not dest.exists():
        filepath.rename(dest)
    else:
        ts = int(time.time())
        dest = ARCHIVE_DIR / f"{filepath.stem}_{ts}{filepath.suffix}"
        filepath.rename(dest)


def scan_inbox(transformer: RecordTransformer, store: DataStore):
    """Scan the inbox for new CSV files and process them."""
    if not INBOX_DIR.exists():
        logger.warning(f"Inbox directory does not exist: {INBOX_DIR}")
        return

    csv_files = list(INBOX_DIR.glob("*.csv"))
    logger.info(f"Found {len(csv_files)} files in inbox")

    for filepath in csv_files:
        if filepath.is_file():
            logger.info(f"Processing: {filepath.name}")
            process_file(filepath, transformer, store)


def run_pipeline(schema: dict, interval: int = 30):
    """Run the pipeline on a polling loop."""
    transformer = RecordTransformer(schema)
    store = DataStore()

    logger.info("Pipeline started. Polling every %d seconds.", interval)

    while True:
        try:
            scan_inbox(transformer, store)
            logger.info(
                "Stats: files=%d ingested=%d failed=%d",
                stats.files_processed,
                stats.records_ingested,
                stats.records_failed,
            )
        except KeyboardInterrupt:
            logger.info("Pipeline shutting down.")
            break
        except Exception as e:
            logger.error(f"Pipeline error: {e}")

        time.sleep(interval)


if __name__ == "__main__":
    logging.basicConfig(level="INFO")
    default_schema = {
        "name": "string",
        "email": "string",
        "age": "integer",
        "balance": "float",
        "opt_notes": "string",
        "active": "boolean",
    }
    run_pipeline(default_schema)
