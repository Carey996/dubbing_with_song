from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


BASE_DIR = Path(__file__).resolve().parents[3]
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "app.sqlite3"
SQL_DIR = Path(__file__).resolve().parent / "sql"
SCHEMA_VERSION = "1"


def read_sql(filename: str) -> str:
    return (SQL_DIR / filename).read_text(encoding="utf-8")


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(read_sql("enable_foreign_keys.sql"))
    return conn


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize_database() -> None:
    with transaction() as conn:
        conn.executescript(read_sql("schema.sql").replace(":schema_version", repr(SCHEMA_VERSION)))
