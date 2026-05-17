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

            CREATE TABLE IF NOT EXISTS card_variants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
                question TEXT NOT NULL CHECK (length(trim(question)) > 0),
                answer TEXT NOT NULL CHECK (length(trim(answer)) > 0),
                notes TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_card_variants_card_id
                ON card_variants(card_id);

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

            CREATE TABLE IF NOT EXISTS ai_providers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL CHECK (length(trim(label)) > 0),
                provider_kind TEXT NOT NULL DEFAULT 'openai_compatible',
                base_url TEXT NOT NULL CHECK (length(trim(base_url)) > 0),
                api_key TEXT NOT NULL,
                organization TEXT,
                project TEXT,
                default_headers TEXT,
                default_query TEXT,
                timeout_seconds REAL,
                max_retries INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS provider_models (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider_id INTEGER NOT NULL REFERENCES ai_providers(id) ON DELETE CASCADE,
                model_id TEXT NOT NULL CHECK (length(trim(model_id)) > 0),
                display_name TEXT,
                owned_by TEXT,
                metadata_json TEXT,
                first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(provider_id, model_id)
            );

            CREATE INDEX IF NOT EXISTS idx_provider_models_provider_id
                ON provider_models(provider_id);

            CREATE TABLE IF NOT EXISTS review_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_id INTEGER REFERENCES cards(id) ON DELETE SET NULL,
                variant_id INTEGER REFERENCES card_variants(id) ON DELETE SET NULL,
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
        ensure_column(conn, "categories", "is_concept_root", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(conn, "cards", "concept_debt", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(conn, "review_events", "outcome", "TEXT")
        ensure_column(conn, "review_events", "variant_id", "INTEGER")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_review_events_card_variant
                ON review_events(card_id, variant_id, reviewed_at)
            """
        )
        conn.execute(
            """
            UPDATE cards
            SET due_at = date(due_at)
            WHERE due_at IS NOT NULL
              AND date(due_at) IS NOT NULL
              AND due_at != date(due_at)
            """
        )
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
