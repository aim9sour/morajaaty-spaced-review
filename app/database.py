from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data.sqlite3"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [row_to_dict(row) for row in rows if row is not None]


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL CHECK (length(trim(name)) > 0),
                parent_id INTEGER REFERENCES categories(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_categories_parent_id
                ON categories(parent_id);

            CREATE TABLE IF NOT EXISTS cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
                question TEXT NOT NULL CHECK (length(trim(question)) > 0),
                answer TEXT NOT NULL CHECK (length(trim(answer)) > 0),
                notes TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_cards_category_id
                ON cards(category_id);

            CREATE TABLE IF NOT EXISTS api_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT,
                key_value TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS review_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_id INTEGER REFERENCES cards(id) ON DELETE SET NULL,
                category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
                rating TEXT NOT NULL,
                previous_stage TEXT,
                next_stage TEXT,
                reviewed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_review_events_reviewed_at
                ON review_events(reviewed_at);
            """
        )
        ensure_column(conn, "cards", "stage", "TEXT NOT NULL DEFAULT 'learning'")
        ensure_column(conn, "cards", "due_at", "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP")
        ensure_column(conn, "cards", "easy_streak", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(conn, "cards", "required_easy", "INTEGER NOT NULL DEFAULT 2")
        ensure_column(conn, "cards", "interval_index", "INTEGER NOT NULL DEFAULT -1")
        ensure_column(conn, "cards", "review_count", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(conn, "cards", "easy_count", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(conn, "cards", "hard_count", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(conn, "cards", "wrong_count", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(conn, "cards", "graduated_count", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(conn, "cards", "last_reviewed_at", "TEXT")
        conn.commit()


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def ensure_category_exists(conn: sqlite3.Connection, category_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM categories WHERE id = ?", (category_id,)).fetchone()
    if row is None:
        raise ValueError("القسم غير موجود")
    return row


def is_leaf_category(conn: sqlite3.Connection, category_id: int) -> bool:
    row = ensure_category_exists(conn, category_id)
    return row["parent_id"] is not None
