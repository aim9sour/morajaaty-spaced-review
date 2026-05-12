from __future__ import annotations

import asyncio
import json
import math
import re
from collections.abc import AsyncGenerator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .database import connect, ensure_category_exists, init_db, is_leaf_category, row_to_dict, rows_to_dicts


BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_COMPANION_CONTEXT = """اسمك رفيق الرفقاء.
أنت تعمل داخل منصة مراجعة متباعدة شخصية.
استخدم أدوات قراءة المنصة وقاعدة البيانات عندما يسأل المستخدم عن الإحصائيات أو الأداء أو المستحق اليوم أو الأيام القادمة أو سجل المراجعات.
حلل البيانات الفعلية قبل الإجابة على الأسئلة التي تحتاج أرقاما أو تواريخ.
اسم المستخدم من الإعدادات: {user_name}"""

app = FastAPI(title="Spaced Review Companion")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    parent_id: int | None = None


class CategoryUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=160)


class SettingsUpdate(BaseModel):
    user_name: str = ""
    main_prompt: str = ""
    companion_context: str = ""
    default_model: str = ""


class ApiKeyCreate(BaseModel):
    key_value: str = Field(min_length=1)
    label: str = ""


class ChatMessage(BaseModel):
    role: Literal["user", "model"]
    text: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    model: str | None = None


class ReviewAnswer(BaseModel):
    rating: Literal["easy", "hard", "wrong"]


REVIEW_INTERVALS_DAYS = [1, 3, 7, 15, 32, 90]


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/categories")
def list_categories(parent_id: int | None = None, root: bool = False) -> list[dict[str, Any]]:
    with connect() as conn:
        if root:
            rows = conn.execute(
                """
                SELECT c.*,
                    (SELECT COUNT(*) FROM categories child WHERE child.parent_id = c.id) AS children_count,
                    0 AS cards_count
                FROM categories c
                WHERE c.parent_id IS NULL
                ORDER BY c.updated_at DESC, c.id DESC
                """
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT c.*,
                    (SELECT COUNT(*) FROM categories child WHERE child.parent_id = c.id) AS children_count,
                    (SELECT COUNT(*) FROM cards card WHERE card.category_id = c.id) AS cards_count
                FROM categories c
                WHERE c.parent_id IS ?
                ORDER BY c.updated_at DESC, c.id DESC
                """,
                (parent_id,),
            ).fetchall()
        return rows_to_dicts(rows)


@app.post("/api/categories", status_code=201)
def create_category(payload: CategoryCreate) -> dict[str, Any]:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="اسم القسم مطلوب")

    with connect() as conn:
        if payload.parent_id is not None:
            parent = ensure_category_exists(conn, payload.parent_id)
            if parent["parent_id"] is not None:
                raise HTTPException(status_code=400, detail="لا يمكن إنشاء قسم داخل قسم فرعي")

        cursor = conn.execute(
            "INSERT INTO categories (name, parent_id) VALUES (?, ?)",
            (name, payload.parent_id),
        )
        conn.commit()
        created = conn.execute("SELECT * FROM categories WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return row_to_dict(created)


@app.get("/api/categories/{category_id}")
def get_category(category_id: int) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT c.*,
                (SELECT COUNT(*) FROM categories child WHERE child.parent_id = c.id) AS children_count,
                (SELECT COUNT(*) FROM cards card WHERE card.category_id = c.id) AS cards_count
            FROM categories c
            WHERE c.id = ?
            """,
            (category_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="القسم غير موجود")
        return row_to_dict(row)


@app.patch("/api/categories/{category_id}")
def update_category(category_id: int, payload: CategoryUpdate) -> dict[str, Any]:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="اسم القسم مطلوب")

    with connect() as conn:
        ensure_category_exists(conn, category_id)
        conn.execute(
            "UPDATE categories SET name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (name, category_id),
        )
        conn.commit()
        return row_to_dict(conn.execute("SELECT * FROM categories WHERE id = ?", (category_id,)).fetchone())


@app.delete("/api/categories/{category_id}", status_code=204, response_class=Response)
def delete_category(category_id: int) -> Response:
    with connect() as conn:
        ensure_category_exists(conn, category_id)
        conn.execute("DELETE FROM categories WHERE id = ?", (category_id,))
        conn.commit()
    return Response(status_code=204)


@app.get("/api/categories/{category_id}/cards")
def list_cards(category_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        ensure_category_exists(conn, category_id)
        rows = conn.execute(
            "SELECT * FROM cards WHERE category_id = ? ORDER BY id DESC",
            (category_id,),
        ).fetchall()
        return rows_to_dicts(rows)


@app.post("/api/categories/{category_id}/cards/import", status_code=201)
async def import_cards(category_id: int, file: UploadFile = File(...)) -> dict[str, Any]:
    raw = await file.read()
    try:
        parsed = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="ملف JSON غير صالح") from exc

    cards = parsed.get("cards") if isinstance(parsed, dict) else parsed
    if not isinstance(cards, list):
        raise HTTPException(status_code=400, detail="الملف يجب أن يحتوي على قائمة بطاقات")

    cleaned: list[tuple[str, str, str | None]] = []
    for index, card in enumerate(cards, start=1):
        if not isinstance(card, dict):
            raise HTTPException(status_code=400, detail=f"البطاقة رقم {index} غير صالحة")
        question = str(card.get("question", card.get("front", ""))).strip()
        answer = str(card.get("answer", card.get("back", ""))).strip()
        notes_value = card.get("notes")
        notes = str(notes_value).strip() if notes_value is not None else None
        if not question or not answer:
            raise HTTPException(status_code=400, detail=f"السؤال والإجابة مطلوبان في البطاقة رقم {index}")
        cleaned.append((question, answer, notes or None))

    with connect() as conn:
        ensure_category_exists(conn, category_id)
        if not is_leaf_category(conn, category_id):
            raise HTTPException(status_code=400, detail="رفع البطاقات متاح داخل الأقسام الفرعية فقط")
        conn.executemany(
            "INSERT INTO cards (category_id, question, answer, notes) VALUES (?, ?, ?, ?)",
            [(category_id, question, answer, notes) for question, answer, notes in cleaned],
        )
        conn.commit()
        return {"imported": len(cleaned)}


def iso_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat()


def parse_dt(value: str | None) -> datetime:
    if not value:
        return datetime.utcnow()
    try:
        return datetime.fromisoformat(value.replace("Z", ""))
    except ValueError:
        return datetime.utcnow()


def category_card_filter(category: sqlite3.Row) -> tuple[str, tuple[Any, ...]]:
    if category["parent_id"] is None:
        return "categories.parent_id = ?", (category["id"],)
    return "cards.category_id = ?", (category["id"],)


def card_stats(card: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    review_count = int(card["review_count"] or 0)
    easy_count = int(card["easy_count"] or 0)
    hard_count = int(card["hard_count"] or 0)
    wrong_count = int(card["wrong_count"] or 0)
    return {
        "stage": card["stage"],
        "due_at": card["due_at"],
        "easy_streak": card["easy_streak"],
        "required_easy": card["required_easy"],
        "remaining_easy": max(int(card["required_easy"]) - int(card["easy_streak"]), 0),
        "interval_index": card["interval_index"],
        "current_interval_days": REVIEW_INTERVALS_DAYS[card["interval_index"]]
        if int(card["interval_index"]) >= 0
        else None,
        "review_count": review_count,
        "easy_count": easy_count,
        "hard_count": hard_count,
        "wrong_count": wrong_count,
        "graduated_count": card["graduated_count"],
        "last_reviewed_at": card["last_reviewed_at"],
        "accuracy_percent": round((easy_count / review_count) * 100) if review_count else None,
    }


def best_due_date(conn, interval_days: int, from_dt: datetime | None = None) -> datetime:
    base = from_dt or datetime.utcnow()
    target = base + timedelta(days=interval_days)
    if interval_days <= 3:
        return target

    window_days = max(1, math.ceil(interval_days * 0.1))
    candidates = [target + timedelta(days=offset) for offset in range(-window_days, window_days + 1)]
    day_loads: dict[str, int] = {}
    for candidate in candidates:
        day = candidate.date().isoformat()
        row = conn.execute(
            "SELECT COUNT(*) AS count FROM cards WHERE date(due_at) = ?",
            (day,),
        ).fetchone()
        day_loads[day] = row["count"]

    target_day = target.date().isoformat()
    best_day = min(day_loads, key=lambda day: (day_loads[day], abs((datetime.fromisoformat(day) - datetime.fromisoformat(target_day)).days)))
    if day_loads[best_day] == day_loads[target_day]:
        best_day = target_day
    return datetime.fromisoformat(best_day) + timedelta(hours=9)


def public_card(row: sqlite3.Row) -> dict[str, Any]:
    card = row_to_dict(row)
    card["stats"] = card_stats(row)
    return card


@app.get("/api/review/{category_id}")
def review_cards(category_id: int) -> dict[str, Any]:
    with connect() as conn:
        category = ensure_category_exists(conn, category_id)
        where_sql, params = category_card_filter(category)
        rows = conn.execute(
            f"""
            SELECT cards.*, categories.name AS category_name
            FROM cards
            JOIN categories ON categories.id = cards.category_id
            WHERE {where_sql}
              AND datetime(cards.due_at) <= datetime('now')
            ORDER BY datetime(cards.due_at) ASC, cards.id ASC
            """,
            params,
        ).fetchall()
        total_row = conn.execute(
            f"""
            SELECT
                COUNT(*) AS total_cards,
                SUM(CASE WHEN datetime(cards.due_at) <= datetime('now') THEN 1 ELSE 0 END) AS due_cards,
                SUM(CASE WHEN cards.stage = 'learning' THEN 1 ELSE 0 END) AS learning_cards,
                SUM(CASE WHEN cards.stage = 'review' THEN 1 ELSE 0 END) AS review_cards
            FROM cards
            JOIN categories ON categories.id = cards.category_id
            WHERE {where_sql}
            """,
            params,
        ).fetchone()
        return {
            "category": row_to_dict(category),
            "cards": [public_card(row) for row in rows],
            "session": row_to_dict(total_row),
        }


@app.post("/api/review/cards/{card_id}/answer")
def answer_review_card(card_id: int, payload: ReviewAnswer) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT cards.*, categories.name AS category_name
            FROM cards
            JOIN categories ON categories.id = cards.category_id
            WHERE cards.id = ?
            """,
            (card_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="البطاقة غير موجودة")

        previous_stage = row["stage"]
        rating = payload.rating
        easy_count = int(row["easy_count"]) + (1 if rating == "easy" else 0)
        hard_count = int(row["hard_count"]) + (1 if rating == "hard" else 0)
        wrong_count = int(row["wrong_count"]) + (1 if rating == "wrong" else 0)
        required_easy = int(row["required_easy"])
        easy_streak = int(row["easy_streak"])
        interval_index = int(row["interval_index"])
        graduated_count = int(row["graduated_count"])
        stage = row["stage"]
        due_at = parse_dt(row["due_at"])
        graduated = False
        requeue_after_ratio: float | None = None

        if stage == "learning":
            if rating == "easy":
                easy_streak += 1
                if easy_streak >= required_easy:
                    stage = "review"
                    interval_index = 0
                    easy_streak = 0
                    graduated_count += 1
                    due_at = best_due_date(conn, REVIEW_INTERVALS_DAYS[interval_index])
                    graduated = True
                else:
                    due_at = datetime.utcnow()
                    requeue_after_ratio = 1.0
            elif rating == "hard":
                easy_streak = 0
                required_easy += 1
                due_at = datetime.utcnow()
                requeue_after_ratio = 0.3
            else:
                easy_streak = 0
                required_easy += 2
                due_at = datetime.utcnow()
                requeue_after_ratio = 0.1
        else:
            if rating == "easy":
                interval_index = min(interval_index + 1, len(REVIEW_INTERVALS_DAYS) - 1)
                due_at = best_due_date(conn, REVIEW_INTERVALS_DAYS[interval_index])
            else:
                stage = "learning"
                easy_streak = 0
                required_easy = max(required_easy, 2) + (1 if rating == "hard" else 2)
                interval_index = -1
                due_at = datetime.utcnow()
                requeue_after_ratio = 0.3 if rating == "hard" else 0.1

        conn.execute(
            """
            UPDATE cards
            SET stage = ?,
                due_at = ?,
                easy_streak = ?,
                required_easy = ?,
                interval_index = ?,
                review_count = review_count + 1,
                easy_count = ?,
                hard_count = ?,
                wrong_count = ?,
                graduated_count = ?,
                last_reviewed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                stage,
                due_at.replace(microsecond=0).isoformat(),
                easy_streak,
                required_easy,
                interval_index,
                easy_count,
                hard_count,
                wrong_count,
                graduated_count,
                card_id,
            ),
        )
        conn.execute(
            """
            INSERT INTO review_events (card_id, category_id, rating, previous_stage, next_stage)
            VALUES (?, ?, ?, ?, ?)
            """,
            (card_id, row["category_id"], rating, previous_stage, stage),
        )
        conn.commit()
        updated = conn.execute(
            """
            SELECT cards.*, categories.name AS category_name
            FROM cards
            JOIN categories ON categories.id = cards.category_id
            WHERE cards.id = ?
            """,
            (card_id,),
        ).fetchone()
        return {
            "card": public_card(updated),
            "graduated": graduated,
            "requeue_after_ratio": requeue_after_ratio,
            "next_due_at": updated["due_at"],
        }


@app.delete("/api/cards/{card_id}", status_code=204, response_class=Response)
def delete_card(card_id: int) -> Response:
    with connect() as conn:
        conn.execute("DELETE FROM cards WHERE id = ?", (card_id,))
        conn.commit()
    return Response(status_code=204)


def get_setting(conn, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return default if row is None or row["value"] is None else row["value"]


def set_setting(conn, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO settings (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )


def mask_key(key_value: str) -> str:
    if len(key_value) <= 8:
        return "••••"
    return f"{key_value[:4]}••••{key_value[-4:]}"


def list_api_keys(conn) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT id, label, key_value, created_at FROM api_keys ORDER BY id DESC").fetchall()
    return [
        {
            "id": row["id"],
            "label": row["label"] or f"مفتاح {row['id']}",
            "masked": mask_key(row["key_value"]),
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def raw_api_keys(conn) -> list[str]:
    rows = conn.execute("SELECT key_value FROM api_keys ORDER BY id ASC").fetchall()
    return [row["key_value"] for row in rows]


@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    with connect() as conn:
        user_name = get_setting(conn, "user_name")
        return {
            "user_name": user_name,
            "main_prompt": get_setting(conn, "main_prompt"),
            "companion_context": get_setting(conn, "companion_context", DEFAULT_COMPANION_CONTEXT),
            "default_model": get_setting(conn, "default_model"),
            "api_keys": list_api_keys(conn),
        }


@app.put("/api/settings")
def update_settings(payload: SettingsUpdate) -> dict[str, Any]:
    with connect() as conn:
        set_setting(conn, "user_name", payload.user_name.strip())
        set_setting(conn, "main_prompt", payload.main_prompt.strip())
        set_setting(conn, "companion_context", payload.companion_context.strip())
        set_setting(conn, "default_model", payload.default_model.strip())
        conn.commit()
    return get_settings()


@app.post("/api/settings/api-keys", status_code=201)
def add_api_key(payload: ApiKeyCreate) -> dict[str, Any]:
    key_value = payload.key_value.strip()
    if not key_value:
        raise HTTPException(status_code=422, detail="المفتاح مطلوب")
    with connect() as conn:
        conn.execute(
            "INSERT INTO api_keys (label, key_value) VALUES (?, ?)",
            (payload.label.strip(), key_value),
        )
        conn.commit()
    return get_settings()


@app.delete("/api/settings/api-keys/{key_id}", status_code=204, response_class=Response)
def delete_api_key(key_id: int) -> Response:
    with connect() as conn:
        conn.execute("DELETE FROM api_keys WHERE id = ?", (key_id,))
        conn.commit()
    return Response(status_code=204)


@app.post("/api/settings/models/search")
async def search_models() -> dict[str, Any]:
    with connect() as conn:
        keys = raw_api_keys(conn)
    if not keys:
        raise HTTPException(status_code=400, detail="أضف مفتاح Gemini API أولا")

    last_error = "تعذر جلب النماذج"
    async with httpx.AsyncClient(timeout=20) as client:
        for key in keys:
            try:
                response = await client.get(f"{GEMINI_API_BASE}/models", params={"key": key})
                if response.status_code >= 400:
                    last_error = response.text
                    continue
                data = response.json()
                models = []
                for model in data.get("models", []):
                    methods = model.get("supportedGenerationMethods", [])
                    if "generateContent" not in methods:
                        continue
                    name = model.get("name", "")
                    display = model.get("displayName") or name.replace("models/", "")
                    models.append({"name": name, "display_name": display})
                return {"models": models}
            except httpx.HTTPError as exc:
                last_error = str(exc)
    raise HTTPException(status_code=502, detail=last_error)


def sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


ROADMAP_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\bthe user is asking\b",
        r"\bto answer this\b",
        r"\bto give a detailed answer\b",
        r"\bi need to\b",
        r"\bi should\b",
        r"\bi will\b",
        r"\bi'll\b",
        r"\blet'?s start\b",
        r"\bquery\s+\d+\b",
        r"\bstep\s+\d+\b",
        r"\bperformance can be measured\b",
        r"\bi have access to\b",
        r"\bi have already called\b",
        r"`?(get_platform_statistics|get_database_schema|query_review_database|review_events|cards)`?",
    ]
]


def model_text_event(text: str) -> str:
    compact = " ".join(text.strip().split())
    if not compact:
        return "delta"
    if any(pattern.search(compact) for pattern in ROADMAP_PATTERNS):
        return "roadmap_delta"
    return "delta"


def build_platform_stats() -> dict[str, Any]:
    with connect() as conn:
        root_count = conn.execute("SELECT COUNT(*) AS count FROM categories WHERE parent_id IS NULL").fetchone()["count"]
        leaf_count = conn.execute("SELECT COUNT(*) AS count FROM categories WHERE parent_id IS NOT NULL").fetchone()["count"]
        card_count = conn.execute("SELECT COUNT(*) AS count FROM cards").fetchone()["count"]
        due_today = conn.execute("SELECT COUNT(*) AS count FROM cards WHERE date(due_at) <= date('now')").fetchone()["count"]
        learning = conn.execute("SELECT COUNT(*) AS count FROM cards WHERE stage = 'learning'").fetchone()["count"]
        review = conn.execute("SELECT COUNT(*) AS count FROM cards WHERE stage = 'review'").fetchone()["count"]
        events_today = conn.execute("SELECT COUNT(*) AS count FROM review_events WHERE date(reviewed_at) = date('now')").fetchone()["count"]
        latest = conn.execute(
            """
            SELECT categories.name, COUNT(cards.id) AS cards_count
            FROM categories
            LEFT JOIN cards ON cards.category_id = categories.id
            WHERE categories.parent_id IS NOT NULL
            GROUP BY categories.id
            ORDER BY categories.updated_at DESC, categories.id DESC
            LIMIT 8
            """
        ).fetchall()
    return {
        "root_categories": root_count,
        "subcategories": leaf_count,
        "cards": card_count,
        "due_today": due_today,
        "learning_cards": learning,
        "review_cards": review,
        "reviews_today": events_today,
        "recent_subcategories": rows_to_dicts(latest),
    }


def database_schema_for_agent() -> dict[str, Any]:
    return {
        "tables": {
            "categories": ["id", "name", "parent_id", "created_at", "updated_at"],
            "cards": [
                "id",
                "category_id",
                "question",
                "answer",
                "notes",
                "stage",
                "due_at",
                "easy_streak",
                "required_easy",
                "interval_index",
                "review_count",
                "easy_count",
                "hard_count",
                "wrong_count",
                "graduated_count",
                "last_reviewed_at",
                "created_at",
            ],
            "review_events": ["id", "card_id", "category_id", "rating", "previous_stage", "next_stage", "reviewed_at"],
        },
        "notes": [
            "Only read-only SELECT queries are allowed.",
            "Use date(due_at) and date(reviewed_at) for daily workload questions.",
            "Ratings are easy, hard, wrong.",
        ],
    }


def run_agent_database_query(query: str) -> dict[str, Any]:
    cleaned = query.strip().rstrip(";")
    lowered = cleaned.lower()
    if not (lowered.startswith("select") or lowered.startswith("with") or lowered.startswith("pragma")):
        return {"error": "Only read-only SELECT/WITH/PRAGMA queries are allowed."}
    forbidden = [" insert ", " update ", " delete ", " drop ", " alter ", " create ", " attach ", " detach ", " replace "]
    padded = f" {lowered} "
    if any(token in padded for token in forbidden) or ";" in cleaned:
        return {"error": "The query contains a forbidden operation."}
    limited = cleaned
    if lowered.startswith("select") and " limit " not in padded:
        limited = f"{cleaned} LIMIT 200"
    with connect() as conn:
        rows = conn.execute(limited).fetchall()
        return {"query": limited, "rows": rows_to_dicts(rows), "row_count": len(rows)}


def gemini_contents(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    return [
        {
            "role": "model" if message.role == "model" else "user",
            "parts": [{"text": message.text}],
        }
        for message in messages
        if message.text.strip()
    ]


def chat_tools() -> list[dict[str, Any]]:
    return [
        {
            "functionDeclarations": [
                {
                    "name": "get_platform_statistics",
                    "description": "Read local spaced-review platform statistics, including section and card counts.",
                    "parameters": {"type": "object", "properties": {}},
                },
                {
                    "name": "get_database_schema",
                    "description": "Read the available SQLite table and column names for deeper analysis.",
                    "parameters": {"type": "object", "properties": {}},
                },
                {
                    "name": "query_review_database",
                    "description": "Run a read-only SQLite query against the local spaced-review database for complex analytics. Use only SELECT, WITH, or PRAGMA.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "A read-only SQLite query. Add aggregates when possible and keep result sets focused.",
                            }
                        },
                        "required": ["query"],
                    },
                }
            ]
        }
    ]


async def stream_gemini_once(
    client: httpx.AsyncClient,
    key: str,
    model: str,
    body: dict[str, Any],
) -> AsyncGenerator[dict[str, Any], None]:
    model_name = model.replace("models/", "")
    url = f"{GEMINI_API_BASE}/models/{model_name}:streamGenerateContent"
    async with client.stream("POST", url, params={"key": key, "alt": "sse"}, json=body) as response:
        if response.status_code >= 400:
            text = await response.aread()
            raise httpx.HTTPStatusError(text.decode("utf-8", "ignore"), request=response.request, response=response)
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue
            data = line.removeprefix("data:").strip()
            if not data:
                continue
            yield json.loads(data)


async def stream_answer_with_key(
    client: httpx.AsyncClient,
    key: str,
    model: str,
    settings: dict[str, str],
    messages: list[ChatMessage],
) -> AsyncGenerator[str, None]:
    main_prompt = settings.get("main_prompt", "")
    companion_context = settings.get("companion_context", "")
    system_text = "\n\n".join(part.strip() for part in [main_prompt, companion_context] if part and part.strip())
    contents = gemini_contents(messages)
    yield sse("thinking", {"text": "جار تجهيز الرد"})

    for _ in range(5):
        body = {
            "contents": contents,
            "tools": chat_tools(),
        }
        if system_text:
            body["systemInstruction"] = {"parts": [{"text": system_text}]}

        function_calls: list[dict[str, Any]] = []
        model_parts: list[dict[str, Any]] = []
        text_event_mode: str | None = None
        saw_final_text = False
        saw_roadmap = False

        async for chunk in stream_gemini_once(client, key, model, body):
            parts = chunk.get("candidates", [{}])[0].get("content", {}).get("parts", [])
            for part in parts:
                if part.get("thought"):
                    yield sse("thinking_delta", {"text": part.get("text", "")})
                if "text" in part and not part.get("thought"):
                    if text_event_mode is None:
                        text_event_mode = model_text_event(part["text"])
                    if text_event_mode == "delta" and part["text"].strip():
                        saw_final_text = True
                    if text_event_mode == "roadmap_delta" and part["text"].strip():
                        saw_roadmap = True
                    model_parts.append({"text": part["text"]})
                    yield sse(text_event_mode, {"text": part["text"]})
                if "functionCall" in part:
                    function_call = part["functionCall"]
                    function_calls.append(function_call)
                    model_parts.append({"functionCall": function_call})

        if function_calls:
            contents.append({"role": "model", "parts": model_parts})
            for call in function_calls:
                name = call.get("name", "")
                args = call.get("args") or {}
                if name == "get_platform_statistics":
                    result = build_platform_stats()
                elif name == "get_database_schema":
                    result = database_schema_for_agent()
                elif name == "query_review_database":
                    result = run_agent_database_query(str(args.get("query", "")))
                else:
                    result = {"error": "Unknown tool"}
                yield sse("tool_call", {"name": name, "args": args, "result": result})
                contents.append({"role": "function", "parts": [{"functionResponse": {"name": name, "response": result}}]})
            continue

        if saw_final_text:
            yield sse("done", {})
            return

        if saw_roadmap:
            contents.append({"role": "model", "parts": model_parts})
            contents.append(
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": "نفذ خارطة العمل الآن باستخدام الأدوات المتاحة عند الحاجة، ثم قدم الإجابة النهائية فقط للمستخدم دون شرح خطواتك الداخلية."
                        }
                    ],
                }
            )
            continue

        yield sse("done", {})
        return
    yield sse("done", {})


@app.post("/api/chat/stream")
async def chat_stream(payload: ChatRequest) -> StreamingResponse:
    with connect() as conn:
        keys = raw_api_keys(conn)
        settings = {
            "user_name": get_setting(conn, "user_name"),
            "main_prompt": get_setting(conn, "main_prompt"),
            "companion_context": get_setting(conn, "companion_context", DEFAULT_COMPANION_CONTEXT).replace(
                "{user_name}", get_setting(conn, "user_name")
            ),
            "default_model": get_setting(conn, "default_model"),
        }
    model = (payload.model or settings["default_model"] or "models/gemini-1.5-flash").strip()

    async def generator() -> AsyncGenerator[str, None]:
        if not keys:
            yield sse("error", {"message": "أضف مفتاح Gemini API من الإعدادات أولا"})
            return
        last_error = "تعذر الاتصال بالنموذج"
        async with httpx.AsyncClient(timeout=None) as client:
            for key in keys:
                try:
                    async for event in stream_answer_with_key(client, key, model, settings, payload.messages):
                        yield event
                    return
                except Exception as exc:  # Fallback to the next key without interrupting the UI.
                    last_error = str(exc)
                    await asyncio.sleep(0.2)
            yield sse("error", {"message": last_error})

    return StreamingResponse(generator(), media_type="text/event-stream")


@app.get("/{path:path}")
def spa_fallback(path: str) -> FileResponse:
    if path.startswith("api/"):
        raise HTTPException(status_code=404)
    return FileResponse(STATIC_DIR / "index.html")
