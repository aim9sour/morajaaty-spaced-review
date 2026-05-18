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
from google import genai
from google.genai import types as genai_types
from openai import AsyncOpenAI
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
    is_concept_root: bool = False


class CategoryUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    is_concept_root: bool | None = None


class SettingsUpdate(BaseModel):
    user_name: str = ""
    main_prompt: str = ""
    companion_context: str = ""
    default_model: str = ""
    default_provider_id: int | None = None


class ApiKeyCreate(BaseModel):
    key_value: str = Field(min_length=1)
    label: str = ""


class ProviderCreate(BaseModel):
    label: str = Field(min_length=1, max_length=160)
    base_url: str = Field(min_length=1, max_length=500)
    api_key: str = Field(min_length=1)
    organization: str = ""
    project: str = ""
    default_headers: str = ""
    default_query: str = ""
    timeout_seconds: float | None = None
    max_retries: int | None = None


class ChatMessage(BaseModel):
    role: Literal["user", "model"]
    text: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    model: str | None = None
    provider_id: int | None = None


class ReviewAnswer(BaseModel):
    rating: Literal["easy", "hard", "wrong"]
    variant_id: int | None = None


REVIEW_INTERVALS_DAYS = [1, 3, 7, 15, 32, 90]
CONCEPT_INTERVALS_DAYS = [1, 3, 7, 15, 30, 90]
MAX_REQUIRED_EASY = 10
MAX_CONCEPT_DEBT_DAYS = 4


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


def category_dict(conn, row: sqlite3.Row | None) -> dict[str, Any] | None:
    data = row_to_dict(row)
    if data is None:
        return None
    if data.get("parent_id") is None:
        data["is_concept_mode"] = bool(data.get("is_concept_root"))
    else:
        parent = conn.execute("SELECT is_concept_root FROM categories WHERE id = ?", (data["parent_id"],)).fetchone()
        data["is_concept_mode"] = bool(parent and parent["is_concept_root"])
    data["is_concept_root"] = bool(data.get("is_concept_root"))
    return data


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
        return [category_dict(conn, row) for row in rows]


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
            "INSERT INTO categories (name, parent_id, is_concept_root) VALUES (?, ?, ?)",
            (name, payload.parent_id, 1 if payload.parent_id is None and payload.is_concept_root else 0),
        )
        conn.commit()
        created = conn.execute("SELECT * FROM categories WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return category_dict(conn, created)


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
        return category_dict(conn, row)


@app.patch("/api/categories/{category_id}")
def update_category(category_id: int, payload: CategoryUpdate) -> dict[str, Any]:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="اسم القسم مطلوب")

    with connect() as conn:
        category = ensure_category_exists(conn, category_id)
        if category["parent_id"] is None:
            concept_value = bool(category["is_concept_root"]) if payload.is_concept_root is None else payload.is_concept_root
            conn.execute(
                "UPDATE categories SET name = ?, is_concept_root = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (name, 1 if concept_value else 0, category_id),
            )
        else:
            conn.execute(
                "UPDATE categories SET name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (name, category_id),
            )
        conn.commit()
        return category_dict(conn, conn.execute("SELECT * FROM categories WHERE id = ?", (category_id,)).fetchone())


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
            """
            SELECT cards.*,
                categories.name AS category_name,
                COALESCE(parent.is_concept_root, categories.is_concept_root, 0) AS concept_mode,
                MAX(1, (SELECT COUNT(*) FROM card_variants WHERE card_variants.card_id = cards.id)) AS variant_count
            FROM cards
            JOIN categories ON categories.id = cards.category_id
            LEFT JOIN categories parent ON parent.id = categories.parent_id
            WHERE cards.category_id = ?
            ORDER BY cards.id DESC
            """,
            (category_id,),
        ).fetchall()
        return rows_to_dicts(rows)


def category_is_concept_mode(conn, category: sqlite3.Row) -> bool:
    if category["parent_id"] is None:
        return bool(category["is_concept_root"])
    parent = conn.execute("SELECT is_concept_root FROM categories WHERE id = ?", (category["parent_id"],)).fetchone()
    return bool(parent and parent["is_concept_root"])


def concept_due_date(days: int = 1) -> datetime:
    return day_start(datetime.now() + timedelta(days=days))


def decode_import_file(raw: bytes) -> str:
    try:
        return raw.decode("utf-8-sig").strip()
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="ملف البطاقات يجب أن يكون UTF-8 صالح") from exc


def parse_cards_text(text: str) -> Any:
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        embedded = re.search(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.IGNORECASE | re.DOTALL)
        if embedded:
            try:
                return json.loads(embedded.group(1).strip())
            except json.JSONDecodeError:
                pass
        raise HTTPException(
            status_code=400,
            detail=f"ملف JSON غير صالح عند السطر {exc.lineno} والعمود {exc.colno}",
        ) from exc


def parse_cards_file(raw: bytes) -> Any:
    return parse_cards_text(decode_import_file(raw))


def parse_bracketed_concepts_text(text: str) -> list[str]:
    concepts = [match.strip() for match in re.findall(r"\[([^\[\]\r\n]+)\]", text) if match.strip()]
    if concepts:
        return concepts
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines:
        return [line.removeprefix("[").removesuffix("]").strip() for line in lines if line.removeprefix("[").removesuffix("]").strip()]
    return []


def import_cards_list(parsed: Any) -> list[Any]:
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        for key in ("cards", "flashcards", "items", "data", "بطاقات"):
            value = parsed.get(key)
            if isinstance(value, list):
                return value
        normalized_keys = {str(key).strip().casefold() for key in parsed.keys()}
        question_keys = {"question", "front", "prompt", "q", "term", "title", "سؤال", "السؤال"}
        answer_keys = {"answer", "back", "completion", "a", "definition", "meaning", "إجابة", "الاجابة", "الإجابة", "جواب"}
        if normalized_keys & {key.casefold() for key in question_keys} and normalized_keys & {
            key.casefold() for key in answer_keys
        }:
            return [parsed]
        if normalized_keys & {"variants", "alternatives", "versions", "forms"}:
            return [parsed]
        if parsed and all(not isinstance(value, (list, dict)) for value in parsed.values()):
            return [{"question": key, "answer": value} for key, value in parsed.items()]
        keys = ", ".join(str(key) for key in parsed.keys())
        raise HTTPException(status_code=400, detail=f"الملف يجب أن يحتوي على قائمة بطاقات. المفاتيح الموجودة: {keys}")
    raise HTTPException(status_code=400, detail="الملف يجب أن يحتوي على قائمة بطاقات")


def first_card_value(card: dict[str, Any], keys: tuple[str, ...]) -> str:
    normalized = {str(key).strip().casefold(): value for key, value in card.items()}
    for key in keys:
        value = normalized.get(key.casefold())
        if value is not None:
            return str(value).strip()
    return ""


QUESTION_KEYS = ("question", "front", "prompt", "q", "term", "title", "سؤال", "السؤال")
ANSWER_KEYS = ("answer", "back", "completion", "a", "definition", "meaning", "إجابة", "الاجابة", "الإجابة", "جواب")
NOTES_KEYS = ("notes", "note", "explanation", "ملاحظات", "شرح")
VARIANTS_KEYS = ("variants", "alternatives", "versions", "forms")


def first_card_list(card: dict[str, Any], keys: tuple[str, ...]) -> list[Any] | None:
    normalized = {str(key).strip().casefold(): value for key, value in card.items()}
    for key in keys:
        value = normalized.get(key.casefold())
        if value is None:
            continue
        if not isinstance(value, list):
            raise HTTPException(status_code=400, detail=f"{key} must be a list")
        return value
    return None


def normal_card_variants(card: dict[str, Any], index: int) -> list[tuple[str, str, str | None]]:
    base_question = first_card_value(card, QUESTION_KEYS)
    base_answer = first_card_value(card, ANSWER_KEYS)
    base_notes = first_card_value(card, NOTES_KEYS) or None
    raw_variants = first_card_list(card, VARIANTS_KEYS)

    variants: list[tuple[str, str, str | None]] = []
    if base_question or base_answer:
        if not base_question or not base_answer:
            keys = ", ".join(str(key) for key in card.keys())
            raise HTTPException(
                status_code=400,
                detail=f"question/front and answer/back are both required in card {index}. Existing keys: {keys}",
            )
        variants.append((base_question, base_answer, base_notes))

    if raw_variants is not None:
        for variant_index, variant in enumerate(raw_variants, start=1):
            if not isinstance(variant, dict):
                raise HTTPException(status_code=400, detail=f"variant {variant_index} in card {index} must be an object")
            question = first_card_value(variant, QUESTION_KEYS)
            answer = first_card_value(variant, ANSWER_KEYS)
            notes = first_card_value(variant, NOTES_KEYS) or base_notes
            if not question or not answer:
                keys = ", ".join(str(key) for key in variant.keys())
                raise HTTPException(
                    status_code=400,
                    detail=f"question/front and answer/back are both required in variant {variant_index} of card {index}. Existing keys: {keys}",
                )
            variants.append((question, answer, notes or None))

    if not variants:
        keys = ", ".join(str(key) for key in card.keys())
        raise HTTPException(
            status_code=400,
            detail=f"card {index} must include front/back, question/answer, or a variants list. Existing keys: {keys}",
        )
    return variants


@app.post("/api/categories/{category_id}/cards/import", status_code=201)
async def import_cards(category_id: int, file: UploadFile = File(...)) -> dict[str, Any]:
    raw = await file.read()
    text = decode_import_file(raw)

    with connect() as conn:
        category = ensure_category_exists(conn, category_id)
        concept_mode = category_is_concept_mode(conn, category)
        if not is_leaf_category(conn, category_id):
            raise HTTPException(status_code=400, detail="رفع البطاقات متاح داخل الأقسام الفرعية فقط")

    if concept_mode:
        try:
            cards = import_cards_list(parse_cards_text(text))
        except HTTPException:
            cards = parse_bracketed_concepts_text(text)
        if not cards:
            raise HTTPException(status_code=400, detail="ملف المفاهيم يجب أن يحتوي JSON أو عناصر نصية مثل [مفهوم]")
    else:
        cards = import_cards_list(parse_cards_text(text))

    cleaned: list[list[tuple[str, str, str | None]]] = []
    for index, card in enumerate(cards, start=1):
        if concept_mode:
            if isinstance(card, dict):
                question = first_card_value(card, ("concept", "question", "front", "prompt", "q", "term", "title", "مفهوم", "سؤال", "السؤال"))
                notes = first_card_value(card, ("notes", "note", "explanation", "ملاحظات", "شرح")) or None
            else:
                question = str(card).strip()
                notes = None
            if not question:
                raise HTTPException(status_code=400, detail=f"المفهوم رقم {index} فارغ أو غير صالح")
            answer = question
            cleaned.append([(question, answer, notes or None)])
            continue
        else:
            if not isinstance(card, dict):
                raise HTTPException(status_code=400, detail=f"البطاقة رقم {index} غير صالحة")
            cleaned.append(normal_card_variants(card, index))

    with connect() as conn:
        for variants in cleaned:
            question, answer, notes = variants[0]
            cursor = conn.execute(
                "INSERT INTO cards (category_id, question, answer, notes, due_at) VALUES (?, ?, ?, ?, date('now', 'localtime'))",
                (category_id, question, answer, notes),
            )
            card_id = cursor.lastrowid
            if not concept_mode:
                conn.executemany(
                    "INSERT INTO card_variants (card_id, question, answer, notes) VALUES (?, ?, ?, ?)",
                    [(card_id, variant_question, variant_answer, variant_notes) for variant_question, variant_answer, variant_notes in variants],
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


def day_start(value: datetime) -> datetime:
    return datetime.fromisoformat(value.date().isoformat())


def today_due_date() -> datetime:
    return day_start(datetime.now())


def due_date_iso(value: datetime) -> str:
    return value.date().isoformat()


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
        "concept_debt": card["concept_debt"] if "concept_debt" in card.keys() else 0,
        "concept_mode": bool(card["concept_mode"]) if "concept_mode" in card.keys() else False,
        "last_reviewed_at": card["last_reviewed_at"],
        "accuracy_percent": round((easy_count / review_count) * 100) if review_count else None,
    }


def capped_required_easy(value: int) -> int:
    return min(max(value, 2), MAX_REQUIRED_EASY)


def capped_concept_debt(value: int) -> int:
    return min(max(value, 0), MAX_CONCEPT_DEBT_DAYS)


def best_due_date(conn, interval_days: int, from_dt: datetime | None = None) -> datetime:
    base = from_dt or datetime.now()
    target = day_start(base + timedelta(days=interval_days))
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
    return datetime.fromisoformat(best_day)


def choose_card_variant(conn, card_id: int, exclude_variant_id: int | None = None) -> tuple[dict[str, Any] | None, int]:
    rows = rows_to_dicts(
        conn.execute(
            """
            SELECT id, question, answer, notes
            FROM card_variants
            WHERE card_id = ?
            ORDER BY RANDOM()
            """,
            (card_id,),
        ).fetchall()
    )
    if not rows:
        return None, 1
    recent_limit = max(len(rows) - 1, 0)
    recent_variant_ids = {
        row["variant_id"]
        for row in conn.execute(
            """
            SELECT variant_id
            FROM review_events
            WHERE card_id = ?
              AND variant_id IS NOT NULL
            ORDER BY id DESC
            LIMIT ?
            """,
            (card_id, recent_limit),
        ).fetchall()
    }
    blocked_ids = {variant_id for variant_id in recent_variant_ids if variant_id is not None}
    if exclude_variant_id is not None:
        blocked_ids.add(exclude_variant_id)
    candidates = [row for row in rows if row["id"] not in blocked_ids]
    if not candidates and exclude_variant_id is not None and len(rows) > 1:
        candidates = [row for row in rows if row["id"] != exclude_variant_id]
    if not candidates:
        candidates = rows
    return candidates[0], len(rows)


def public_card(row: sqlite3.Row, conn=None, exclude_variant_id: int | None = None) -> dict[str, Any]:
    card = row_to_dict(row)
    if conn is not None and not bool(card.get("concept_mode")):
        variant, variant_count = choose_card_variant(conn, int(card["id"]), exclude_variant_id)
        card["variant_count"] = variant_count
        card["variant_id"] = None
        if variant is not None:
            card["variant_id"] = variant["id"]
            card["question"] = variant["question"]
            card["answer"] = variant["answer"]
            card["notes"] = variant["notes"]
    else:
        card["variant_count"] = max(int(card.get("variant_count") or 1), 1)
        card["variant_id"] = None
    card["stats"] = card_stats(row)
    return card


@app.get("/api/review/{category_id}")
def review_cards(category_id: int) -> dict[str, Any]:
    with connect() as conn:
        category = ensure_category_exists(conn, category_id)
        where_sql, params = category_card_filter(category)
        rows = conn.execute(
            f"""
            SELECT cards.*, categories.name AS category_name,
                COALESCE(parent.is_concept_root, categories.is_concept_root, 0) AS concept_mode
            FROM cards
            JOIN categories ON categories.id = cards.category_id
            LEFT JOIN categories parent ON parent.id = categories.parent_id
            WHERE {where_sql}
              AND date(cards.due_at) <= date('now', 'localtime')
            ORDER BY date(cards.due_at) ASC, RANDOM()
            """,
            params,
        ).fetchall()
        total_row = conn.execute(
            f"""
            SELECT
                COUNT(*) AS total_cards,
                SUM(CASE WHEN date(cards.due_at) <= date('now', 'localtime') THEN 1 ELSE 0 END) AS due_cards,
                SUM(CASE WHEN cards.stage = 'learning' THEN 1 ELSE 0 END) AS learning_cards,
                SUM(CASE WHEN cards.stage = 'review' THEN 1 ELSE 0 END) AS review_cards
            FROM cards
            JOIN categories ON categories.id = cards.category_id
            WHERE {where_sql}
            """,
            params,
        ).fetchone()
        return {
            "category": category_dict(conn, category),
            "concept_mode": category_is_concept_mode(conn, category),
            "cards": [public_card(row, conn) for row in rows],
            "session": row_to_dict(total_row),
        }


@app.post("/api/review/cards/{card_id}/answer")
def answer_review_card(card_id: int, payload: ReviewAnswer) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT cards.*, categories.name AS category_name,
                COALESCE(parent.is_concept_root, categories.is_concept_root, 0) AS concept_mode
            FROM cards
            JOIN categories ON categories.id = cards.category_id
            LEFT JOIN categories parent ON parent.id = categories.parent_id
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
        required_easy = capped_required_easy(int(row["required_easy"]))
        easy_streak = int(row["easy_streak"])
        interval_index = int(row["interval_index"])
        graduated_count = int(row["graduated_count"])
        concept_mode = bool(row["concept_mode"])
        variant_id = payload.variant_id if not concept_mode else None
        if variant_id is not None:
            variant_exists = conn.execute(
                "SELECT 1 FROM card_variants WHERE id = ? AND card_id = ?",
                (variant_id, card_id),
            ).fetchone()
            if variant_exists is None:
                raise HTTPException(status_code=400, detail="نسخة البطاقة غير صالحة")
        concept_debt = capped_concept_debt(int(row["concept_debt"] or 0))
        stage = row["stage"]
        due_at = today_due_date()
        graduated = False
        first_graduation = False
        regraduated = False
        outcome: str | None = None
        requeue_after_ratio: float | None = None

        if concept_mode:
            if rating == "easy":
                if concept_debt > 0:
                    concept_debt -= 1
                    due_at = concept_due_date(1)
                elif stage == "learning":
                    first_graduation = graduated_count == 0
                    regraduated = graduated_count > 0
                    stage = "review"
                    interval_index = 0
                    graduated_count += 1
                    graduated = True
                    outcome = "first_graduation" if first_graduation else "regraduated"
                    due_at = concept_due_date(CONCEPT_INTERVALS_DAYS[interval_index])
                else:
                    interval_index = min(max(interval_index, 0) + 1, len(CONCEPT_INTERVALS_DAYS) - 1)
                    due_at = concept_due_date(CONCEPT_INTERVALS_DAYS[interval_index])
            elif rating == "hard":
                concept_debt = capped_concept_debt(concept_debt + 2)
                if stage == "review":
                    interval_index = max(int(interval_index) - 2, 0)
                    due_at = concept_due_date(CONCEPT_INTERVALS_DAYS[interval_index])
                else:
                    stage = "learning"
                    interval_index = -1
                    due_at = concept_due_date(1)
            else:
                if stage == "review":
                    concept_debt = 4
                else:
                    concept_debt = capped_concept_debt(concept_debt + 4)
                stage = "learning"
                interval_index = -1
                easy_streak = 0
                due_at = concept_due_date(1)
        elif stage == "learning":
            if rating == "easy":
                easy_streak += 1
                if easy_streak >= required_easy:
                    first_graduation = graduated_count == 0
                    regraduated = graduated_count > 0
                    stage = "review"
                    interval_index = 0
                    easy_streak = 0
                    graduated_count += 1
                    due_at = best_due_date(conn, REVIEW_INTERVALS_DAYS[interval_index])
                    graduated = True
                    outcome = "first_graduation" if first_graduation else "regraduated"
                else:
                    due_at = today_due_date()
                    requeue_after_ratio = 1.0
            elif rating == "hard":
                easy_streak = 0
                required_easy = capped_required_easy(required_easy + 1)
                due_at = today_due_date()
                requeue_after_ratio = 0.3
            else:
                easy_streak = 0
                required_easy = capped_required_easy(required_easy + 2)
                due_at = today_due_date()
                requeue_after_ratio = 0.1
        else:
            if rating == "easy":
                interval_index = min(interval_index + 1, len(REVIEW_INTERVALS_DAYS) - 1)
                due_at = best_due_date(conn, REVIEW_INTERVALS_DAYS[interval_index])
            else:
                stage = "learning"
                easy_streak = 0
                required_easy = capped_required_easy(max(required_easy, 2) + (1 if rating == "hard" else 2))
                interval_index = -1
                due_at = today_due_date()
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
                concept_debt = ?,
                last_reviewed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                stage,
                due_date_iso(due_at),
                easy_streak,
                required_easy,
                interval_index,
                easy_count,
                hard_count,
                wrong_count,
                graduated_count,
                concept_debt,
                card_id,
            ),
        )
        conn.execute(
            """
            INSERT INTO review_events (card_id, variant_id, category_id, rating, previous_stage, next_stage, outcome)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (card_id, variant_id, row["category_id"], rating, previous_stage, stage, outcome),
        )
        conn.commit()
        updated = conn.execute(
            """
            SELECT cards.*, categories.name AS category_name,
                COALESCE(parent.is_concept_root, categories.is_concept_root, 0) AS concept_mode
            FROM cards
            JOIN categories ON categories.id = cards.category_id
            LEFT JOIN categories parent ON parent.id = categories.parent_id
            WHERE cards.id = ?
            """,
            (card_id,),
        ).fetchone()
        return {
            "card": public_card(updated, conn, payload.variant_id),
            "graduated": graduated,
            "first_graduation": first_graduation,
            "regraduated": regraduated,
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


def parse_json_object(value: str, field_name: str) -> dict[str, Any] | None:
    cleaned = value.strip()
    if not cleaned:
        return None
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"{field_name} يجب أن يكون JSON صالح") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=422, detail=f"{field_name} يجب أن يكون كائن JSON")
    return parsed


def provider_public(row: sqlite3.Row) -> dict[str, Any]:
    data = row_to_dict(row)
    data["masked"] = mask_key(data.pop("api_key"))
    data["models"] = []
    return data


def list_providers(conn) -> list[dict[str, Any]]:
    providers = [
        provider_public(row)
        for row in conn.execute("SELECT * FROM ai_providers ORDER BY updated_at DESC, id DESC").fetchall()
    ]
    models = rows_to_dicts(
        conn.execute(
            """
            SELECT *
            FROM provider_models
            ORDER BY provider_id ASC, lower(display_name || model_id) ASC, id ASC
            """
        ).fetchall()
    )
    by_provider: dict[int, list[dict[str, Any]]] = {}
    for model in models:
        by_provider.setdefault(model["provider_id"], []).append(model)
    for provider in providers:
        provider["models"] = by_provider.get(provider["id"], [])
    return providers


def get_provider(conn, provider_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM ai_providers WHERE id = ?", (provider_id,)).fetchone()


def openai_client_for_provider(provider: sqlite3.Row) -> AsyncOpenAI:
    headers = parse_json_object(provider["default_headers"] or "", "الرؤوس الإضافية") or None
    query = parse_json_object(provider["default_query"] or "", "استعلامات URL الإضافية") or None
    kwargs: dict[str, Any] = {
        "api_key": provider["api_key"],
        "base_url": provider["base_url"],
        "organization": provider["organization"] or None,
        "project": provider["project"] or None,
        "default_headers": headers,
        "default_query": query,
    }
    if provider["timeout_seconds"] is not None:
        kwargs["timeout"] = float(provider["timeout_seconds"])
    if provider["max_retries"] is not None:
        kwargs["max_retries"] = int(provider["max_retries"])
    return AsyncOpenAI(**kwargs)


@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    with connect() as conn:
        user_name = get_setting(conn, "user_name")
        return {
            "user_name": user_name,
            "main_prompt": get_setting(conn, "main_prompt"),
            "companion_context": get_setting(conn, "companion_context", DEFAULT_COMPANION_CONTEXT),
            "default_model": get_setting(conn, "default_model"),
            "default_provider_id": get_setting(conn, "default_provider_id"),
            "api_keys": list_api_keys(conn),
            "providers": list_providers(conn),
        }


@app.put("/api/settings")
def update_settings(payload: SettingsUpdate) -> dict[str, Any]:
    with connect() as conn:
        set_setting(conn, "user_name", payload.user_name.strip())
        set_setting(conn, "main_prompt", payload.main_prompt.strip())
        set_setting(conn, "companion_context", payload.companion_context.strip())
        set_setting(conn, "default_model", payload.default_model.strip())
        set_setting(conn, "default_provider_id", str(payload.default_provider_id or ""))
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


@app.post("/api/settings/providers", status_code=201)
def add_provider(payload: ProviderCreate) -> dict[str, Any]:
    label = payload.label.strip()
    base_url = payload.base_url.strip().rstrip("/")
    api_key = payload.api_key.strip()
    if not label or not base_url or not api_key:
        raise HTTPException(status_code=422, detail="اسم المزود ورابط النهاية والمفتاح مطلوبة")
    parse_json_object(payload.default_headers, "الرؤوس الإضافية")
    parse_json_object(payload.default_query, "استعلامات URL الإضافية")
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO ai_providers (
                label, base_url, api_key, organization, project,
                default_headers, default_query, timeout_seconds, max_retries
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                label,
                base_url,
                api_key,
                payload.organization.strip() or None,
                payload.project.strip() or None,
                payload.default_headers.strip() or None,
                payload.default_query.strip() or None,
                payload.timeout_seconds,
                payload.max_retries,
            ),
        )
        conn.commit()
    return get_settings()


@app.delete("/api/settings/providers/{provider_id}", status_code=204, response_class=Response)
def delete_provider(provider_id: int) -> Response:
    with connect() as conn:
        conn.execute("DELETE FROM ai_providers WHERE id = ?", (provider_id,))
        current_default = get_setting(conn, "default_provider_id")
        if current_default == str(provider_id):
            set_setting(conn, "default_provider_id", "")
            set_setting(conn, "default_model", "")
        conn.commit()
    return Response(status_code=204)


@app.post("/api/settings/providers/{provider_id}/models/fetch")
async def fetch_provider_models(provider_id: int) -> dict[str, Any]:
    with connect() as conn:
        provider = get_provider(conn, provider_id)
        if provider is None:
            raise HTTPException(status_code=404, detail="المزود غير موجود")
        provider_data = row_to_dict(provider)

    client = openai_client_for_provider(provider)
    try:
        response = await client.models.list()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    added = 0
    updated = 0
    seen = 0
    with connect() as conn:
        for model in response.data:
            model_id = getattr(model, "id", "") or ""
            if not model_id:
                continue
            seen += 1
            owned_by = getattr(model, "owned_by", None)
            try:
                metadata = model.model_dump(mode="json")
            except Exception:
                metadata = {"id": model_id, "owned_by": owned_by}
            metadata_json = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
            existing = conn.execute(
                "SELECT metadata_json FROM provider_models WHERE provider_id = ? AND model_id = ?",
                (provider_id, model_id),
            ).fetchone()
            if existing is None:
                added += 1
                conn.execute(
                    """
                    INSERT INTO provider_models (provider_id, model_id, display_name, owned_by, metadata_json)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (provider_id, model_id, model_id, owned_by, metadata_json),
                )
            elif existing["metadata_json"] != metadata_json:
                updated += 1
                conn.execute(
                    """
                    UPDATE provider_models
                    SET display_name = ?, owned_by = ?, metadata_json = ?, last_seen_at = CURRENT_TIMESTAMP
                    WHERE provider_id = ? AND model_id = ?
                    """,
                    (model_id, owned_by, metadata_json, provider_id, model_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE provider_models
                    SET last_seen_at = CURRENT_TIMESTAMP
                    WHERE provider_id = ? AND model_id = ?
                    """,
                    (provider_id, model_id),
                )
        conn.execute("UPDATE ai_providers SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (provider_id,))
        conn.commit()

    settings = get_settings()
    settings["fetch_result"] = {
        "provider": provider_data["label"],
        "seen": seen,
        "added": added,
        "updated": updated,
    }
    return settings


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
        variant_count = conn.execute("SELECT COUNT(*) AS count FROM card_variants").fetchone()["count"]
        due_today = conn.execute("SELECT COUNT(*) AS count FROM cards WHERE date(due_at) <= date('now', 'localtime')").fetchone()["count"]
        learning = conn.execute("SELECT COUNT(*) AS count FROM cards WHERE stage = 'learning'").fetchone()["count"]
        review = conn.execute("SELECT COUNT(*) AS count FROM cards WHERE stage = 'review'").fetchone()["count"]
        events_today = conn.execute(
            "SELECT COUNT(*) AS count FROM review_events WHERE date(reviewed_at, 'localtime') = date('now', 'localtime')"
        ).fetchone()["count"]
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
        "card_variants": variant_count,
        "due_today": due_today,
        "learning_cards": learning,
        "review_cards": review,
        "reviews_today": events_today,
        "recent_subcategories": rows_to_dicts(latest),
    }


def database_schema_for_agent() -> dict[str, Any]:
    return {
        "tables": {
            "categories": ["id", "name", "parent_id", "is_concept_root", "created_at", "updated_at"],
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
                "concept_debt",
                "last_reviewed_at",
                "created_at",
            ],
            "card_variants": ["id", "card_id", "question", "answer", "notes", "created_at"],
            "review_events": ["id", "card_id", "variant_id", "category_id", "rating", "previous_stage", "next_stage", "outcome", "reviewed_at"],
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


def get_platform_statistics() -> dict[str, Any]:
    """Read local spaced-review platform statistics, including section and card counts."""
    return build_platform_stats()


def get_database_schema() -> dict[str, Any]:
    """Read the available SQLite table and column names for deeper analysis."""
    return database_schema_for_agent()


def query_review_database(query: str) -> dict[str, Any]:
    """Run a read-only SQLite query against the local spaced-review database.

    Args:
        query: A read-only SQLite query. Use only SELECT, WITH, or PRAGMA. Add aggregates when possible and keep result sets focused.
    """
    return run_agent_database_query(query)


get_platform_statistics.__annotations__ = {"return": dict}
get_database_schema.__annotations__ = {"return": dict}
query_review_database.__annotations__ = {"query": str, "return": dict}


def gemini_contents(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    contents: list[dict[str, Any]] = []
    for message in messages:
        text = message.text.strip()
        if not text:
            continue
        failed_model_message = message.role == "model" and (
            "Internal error encountered" in text
            or '"status": "INTERNAL"' in text
            or text.lstrip().startswith('{"error"')
        )
        if failed_model_message:
            if contents and contents[-1]["role"] == "user":
                contents.pop()
            continue
        contents.append(
            {
                "role": "model" if message.role == "model" else "user",
                "parts": [{"text": text}],
            }
        )
    return contents


def genai_contents_from_messages(messages: list[ChatMessage]) -> list[genai_types.Content]:
    contents = []
    for item in gemini_contents(messages):
        text = item["parts"][0]["text"]
        contents.append(
            genai_types.Content(
                role=item["role"],
                parts=[genai_types.Part(text=text)],
            )
        )
    return contents


def normalize_model_name(model: str) -> str:
    return model.strip().replace("models/", "")


def function_calling_model_name(model: str) -> str:
    return normalize_model_name(model)


class EmptyModelResponseError(RuntimeError):
    pass


def response_text_from_chunk(chunk: genai_types.GenerateContentResponse) -> str:
    texts: list[str] = []
    for part in chunk.parts or []:
        if part.thought:
            continue
        if part.text:
            texts.append(part.text)
    if texts:
        return "".join(texts)
    try:
        return chunk.text or ""
    except ValueError:
        return ""


def tool_events_from_afc_history(history: list[genai_types.Content] | None) -> list[dict[str, Any]]:
    if not history:
        return []

    pending_calls: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    for content in history:
        for part in content.parts or []:
            function_call = part.function_call
            if function_call:
                pending_calls.append(
                    {
                        "name": function_call.name or "",
                        "args": dict(function_call.args or {}),
                    }
                )
                continue

            function_response = part.function_response
            if not function_response:
                continue

            call_index = next(
                (
                    index
                    for index, call in enumerate(pending_calls)
                    if call["name"] == function_response.name
                ),
                0 if pending_calls else None,
            )
            if call_index is None:
                call = {"name": function_response.name or "", "args": {}}
            else:
                call = pending_calls.pop(call_index)

            result = function_response.response or {}
            if isinstance(result, dict) and set(result.keys()) == {"result"}:
                result = result["result"]
            events.append({"name": call["name"], "args": call["args"], "result": result})
    return events


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


def openai_tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "get_platform_statistics",
                "description": "Read local spaced-review platform statistics, including section and card counts.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_database_schema",
                "description": "Read the available SQLite table and column names for deeper analysis.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
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
            },
        },
    ]


def openai_messages_from_chat(messages: list[ChatMessage], system_text: str) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    if system_text:
        converted.append({"role": "system", "content": system_text})
    for message in messages:
        text = message.text.strip()
        if not text:
            continue
        converted.append({"role": "assistant" if message.role == "model" else "user", "content": text})
    return converted


async def stream_openai_compatible_answer(
    provider: sqlite3.Row,
    model: str,
    settings: dict[str, str],
    messages: list[ChatMessage],
) -> AsyncGenerator[str, None]:
    main_prompt = settings.get("main_prompt", "")
    companion_context = settings.get("companion_context", "")
    system_text = "\n\n".join(part.strip() for part in [main_prompt, companion_context] if part and part.strip())
    conversation = openai_messages_from_chat(messages, system_text)
    client = openai_client_for_provider(provider)
    yield sse("thinking", {"text": "جار تجهيز الرد"})

    for round_index in range(5):
        yield sse("status", {"text": f"يتصل بالمزود {provider['label']} باستخدام النموذج {model}"})
        stream = await client.chat.completions.create(
            model=model,
            messages=conversation,
            tools=openai_tools(),
            stream=True,
        )
        content_parts: list[str] = []
        tool_calls: dict[int, dict[str, Any]] = {}
        finish_reason = None

        async for chunk in stream:
            choice = chunk.choices[0] if chunk.choices else None
            if choice is None:
                continue
            finish_reason = choice.finish_reason or finish_reason
            delta = choice.delta
            if delta.content:
                content_parts.append(delta.content)
                yield sse("delta", {"text": delta.content})
            if delta.tool_calls:
                for tool_delta in delta.tool_calls:
                    index = tool_delta.index
                    current = tool_calls.setdefault(
                        index,
                        {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                    )
                    if tool_delta.id:
                        current["id"] = tool_delta.id
                    if tool_delta.function:
                        if tool_delta.function.name:
                            current["function"]["name"] += tool_delta.function.name
                        if tool_delta.function.arguments:
                            current["function"]["arguments"] += tool_delta.function.arguments

        if not tool_calls:
            yield sse("done", {})
            return

        assistant_message: dict[str, Any] = {
            "role": "assistant",
            "content": "".join(content_parts) or None,
            "tool_calls": [tool_calls[index] for index in sorted(tool_calls)],
        }
        conversation.append(assistant_message)

        for tool_call in assistant_message["tool_calls"]:
            function = tool_call["function"]
            name = function["name"]
            yield sse("status", {"text": f"يستخدم أداة: {name}"})
            try:
                args = json.loads(function["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            result = execute_chat_tool(name, args)
            yield sse("tool_call", {"name": name, "args": args, "result": result})
            yield sse("status", {"text": f"انتهت أداة: {name}"})
            conversation.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )
        if finish_reason != "tool_calls":
            yield sse("done", {})
            return
        yield sse("status", {"text": f"ينفذ جولة متابعة بعد الأدوات رقم {round_index + 1}"})

    yield sse("error", {"message": "توقفت بعد عدة استدعاءات أدوات دون رد نهائي"})


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


def execute_chat_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name == "get_platform_statistics":
        return build_platform_stats()
    if name == "get_database_schema":
        return database_schema_for_agent()
    if name == "query_review_database":
        return run_agent_database_query(str(args.get("query", "")))
    return {"error": "Unknown tool"}


def latest_user_text(messages: list[ChatMessage]) -> str:
    for message in reversed(messages):
        if message.role == "user" and message.text.strip():
            return message.text.strip()
    return ""


def asks_for_due_today(text: str) -> bool:
    normalized = text.lower()
    has_today = "اليوم" in normalized or "today" in normalized
    has_due = any(token in normalized for token in ["مراجعات", "مراجعة", "مستحق", "مستحقة", "reviews", "review", "due"])
    return has_today and has_due


def due_today_tool_result(limit: int = 20) -> dict[str, Any]:
    query = """
        SELECT
            categories.name AS category,
            cards.question,
            cards.due_at,
            cards.stage
        FROM cards
        JOIN categories ON categories.id = cards.category_id
        WHERE date(cards.due_at) <= date('now', 'localtime')
        ORDER BY date(cards.due_at) ASC, cards.id ASC
    """
    with connect() as conn:
        total = conn.execute(
            "SELECT COUNT(*) AS count FROM cards WHERE date(due_at) <= date('now', 'localtime')"
        ).fetchone()["count"]
        category_rows = conn.execute(
            """
            SELECT categories.name AS category, COUNT(*) AS count
            FROM cards
            JOIN categories ON categories.id = cards.category_id
            WHERE date(cards.due_at) <= date('now', 'localtime')
            GROUP BY categories.id
            ORDER BY count DESC, categories.name ASC
            """
        ).fetchall()
        rows = conn.execute(f"{query} LIMIT ?", (limit,)).fetchall()
    return {
        "query": f"{query.strip()} LIMIT {limit}",
        "rows": rows_to_dicts(rows),
        "row_count": total,
        "category_counts": rows_to_dicts(category_rows),
    }


def direct_platform_answer(messages: list[ChatMessage]) -> tuple[dict[str, Any], str] | None:
    text = latest_user_text(messages)
    if not asks_for_due_today(text):
        return None

    result = due_today_tool_result()
    rows = result["rows"]
    total = int(result["row_count"])
    if total == 0:
        answer = "راجعت قاعدة البيانات: لا توجد بطاقات مستحقة للمراجعة اليوم."
    else:
        lines = [f"عندك {total} بطاقة مستحقة للمراجعة اليوم."]
        category_counts = result.get("category_counts") or []
        if category_counts:
            lines.append("")
            lines.append("التوزيع حسب القسم:")
            for item in category_counts[:8]:
                lines.append(f"- {item['category']}: {item['count']}")

        lines.append("")
        lines.append("أول البطاقات:")
        for index, row in enumerate(rows[:10], start=1):
            lines.append(f"{index}. [{row['category']}] {row['question']}")
        if total > 10:
            lines.append(f"\nعرضت أول 10 فقط. افتح زر بدء المراجعة لإنهاء المستحق كله.")
        answer = "\n".join(lines)

    return {"name": "query_review_database", "args": {"query": result["query"]}, "result": result}, answer


def fallback_answer_from_tools(tool_results: list[dict[str, Any]]) -> str | None:
    if not tool_results:
        return None

    latest_query = next((item for item in reversed(tool_results) if item["name"] == "query_review_database"), None)
    if latest_query:
        result = latest_query["result"]
        if result.get("error"):
            return f"قرأت طلبك لكن استعلام قاعدة البيانات تعثر: {result['error']}"

        rows = result.get("rows") or []
        if not rows:
            return "راجعت قاعدة البيانات: لا توجد نتائج مطابقة للسؤال حاليا."

        total = int(result.get("row_count") or len(rows))
        category_counts: dict[str, int] = {}
        for row in rows:
            category = str(row.get("category") or row.get("category_name") or row.get("name") or "بدون قسم")
            category_counts[category] = category_counts.get(category, 0) + 1

        lines = [f"راجعت قاعدة البيانات مباشرة. الموجود الآن: {total} نتيجة."]
        if category_counts:
            lines.append("")
            lines.append("التوزيع حسب القسم:")
            for category, count in sorted(category_counts.items(), key=lambda item: (-item[1], item[0]))[:8]:
                lines.append(f"- {category}: {count}")

        preview_rows = rows[:10]
        if preview_rows:
            lines.append("")
            lines.append("أول العناصر:")
            for index, row in enumerate(preview_rows, start=1):
                question = row.get("question")
                category = row.get("category") or row.get("category_name") or row.get("name")
                if question and category:
                    lines.append(f"{index}. [{category}] {question}")
                elif question:
                    lines.append(f"{index}. {question}")
                else:
                    values = [str(value) for value in row.values() if value is not None]
                    lines.append(f"{index}. {' | '.join(values[:4])}")

        if total > len(preview_rows):
            lines.append(f"\nعرضت أول {len(preview_rows)} فقط حتى لا تطول الرسالة.")
        return "\n".join(lines)

    latest_stats = next((item for item in reversed(tool_results) if item["name"] == "get_platform_statistics"), None)
    if latest_stats:
        result = latest_stats["result"]
        return "\n".join(
            [
                "راجعت إحصائيات المنصة مباشرة:",
                f"- المستحق اليوم: {result.get('due_today', 0)} بطاقة",
                f"- كل البطاقات: {result.get('cards', 0)}",
                f"- بطاقات التعلم: {result.get('learning_cards', 0)}",
                f"- بطاقات المراجعة: {result.get('review_cards', 0)}",
                f"- مراجعات تمت اليوم: {result.get('reviews_today', 0)}",
            ]
        )

    if any(item["name"] == "get_database_schema" for item in tool_results):
        return "قرأت بنية قاعدة البيانات، لكن النموذج لم يكمل الإجابة. الجداول المتاحة هي: categories و cards و review_events."

    return None


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
    tool_results: list[dict[str, Any]] = []
    yield sse("thinking", {"text": "جار تجهيز الرد"})

    direct_answer = direct_platform_answer(messages)
    if direct_answer:
        tool_event, answer = direct_answer
        yield sse("tool_call", tool_event)
        yield sse("delta", {"text": answer})
        yield sse("done", {})
        return

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

        try:
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
        except Exception:
            fallback = fallback_answer_from_tools(tool_results)
            if fallback:
                yield sse("delta", {"text": fallback})
                yield sse("done", {})
                return
            raise

        if function_calls:
            contents.append({"role": "model", "parts": model_parts})
            for call in function_calls:
                name = call.get("name", "")
                args = call.get("args") or {}
                result = execute_chat_tool(name, args)
                tool_results.append({"name": name, "args": args, "result": result})
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

        fallback = fallback_answer_from_tools(tool_results)
        if fallback:
            yield sse("delta", {"text": fallback})
            yield sse("done", {})
            return

        yield sse("done", {})
        return
    fallback = fallback_answer_from_tools(tool_results)
    if fallback:
        yield sse("delta", {"text": fallback})
    yield sse("done", {})


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
    contents = genai_contents_from_messages(messages)
    yield sse("thinking", {"text": "جار تجهيز الرد"})
    yield sse("status", {"text": f"يتصل بـ Gemini باستخدام النموذج {function_calling_model_name(model)}"})

    genai_client = genai.Client(api_key=key)
    config = genai_types.GenerateContentConfig(
        system_instruction=system_text or None,
        tools=[get_platform_statistics, get_database_schema, query_review_database],
        automatic_function_calling=genai_types.AutomaticFunctionCallingConfig(
            maximum_remote_calls=5,
            ignore_call_history=False,
        ),
        thinking_config=genai_types.ThinkingConfig(include_thoughts=True),
    )

    seen_tool_events: set[str] = set()
    tool_results: list[dict[str, Any]] = []
    emitted_text = False
    saw_model_activity = False
    thought_chars = 0
    finish_reasons: list[str] = []
    stream = await genai_client.aio.models.generate_content_stream(
        model=function_calling_model_name(model),
        contents=contents,
        config=config,
    )
    async for chunk in stream:
        saw_model_activity = True
        for candidate in chunk.candidates or []:
            if candidate.finish_reason:
                finish_reasons.append(str(candidate.finish_reason))
        for event in tool_events_from_afc_history(chunk.automatic_function_calling_history):
            event_key = json.dumps(event, ensure_ascii=False, sort_keys=True, default=str)
            if event_key in seen_tool_events:
                continue
            seen_tool_events.add(event_key)
            tool_results.append(event)
            yield sse("status", {"text": f"يستخدم أداة: {event.get('name', 'أداة')}"})
            yield sse("tool_call", event)

        for part in chunk.parts or []:
            if part.thought and part.text:
                thought_chars += len(part.text)
                yield sse("thinking_delta", {"text": part.text})

        text = response_text_from_chunk(chunk)
        if text:
            emitted_text = True
            yield sse("delta", {"text": text})

    if not emitted_text:
        fallback = fallback_answer_from_tools(tool_results)
        if fallback:
            yield sse("status", {"text": "لم يصل نص نهائي من النموذج؛ يعرض نتيجة الأدوات المتاحة"})
            yield sse("delta", {"text": fallback})
        elif saw_model_activity:
            reason_text = ", ".join(dict.fromkeys(finish_reasons)) or "غير معروف"
            raise EmptyModelResponseError(
                f"انتهى بث النموذج بدون نص إجابة نهائي؛ finish_reason={reason_text}; thoughts={thought_chars} حرف. سيتم تجربة مفتاح آخر إن وجد"
            )

    yield sse("done", {})


@app.post("/api/chat/stream")
async def chat_stream(payload: ChatRequest) -> StreamingResponse:
    with connect() as conn:
        keys = raw_api_keys(conn)
        default_provider_value = get_setting(conn, "default_provider_id")
        provider_id = payload.provider_id if payload.provider_id is not None else int(default_provider_value or 0) or None
        provider = get_provider(conn, provider_id) if provider_id else None
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
        if provider is not None:
            try:
                yield sse("status", {"text": f"تم اختيار مزود OpenAI-compatible: {provider['label']}"})
                async for event in stream_openai_compatible_answer(provider, model, settings, payload.messages):
                    yield event
            except Exception as exc:
                yield sse("status", {"text": "حدث خطأ في مزود OpenAI-compatible"})
                yield sse("error", {"message": str(exc)})
            return

        if not keys:
            yield sse("error", {"message": "أضف مفتاح Gemini API من الإعدادات أولا"})
            return
        last_error = "تعذر الاتصال بالنموذج"
        async with httpx.AsyncClient(timeout=None) as client:
            for index, key in enumerate(keys, start=1):
                try:
                    yield sse("status", {"text": f"يحاول مفتاح Gemini رقم {index} من {len(keys)}"})
                    async for event in stream_answer_with_key(client, key, model, settings, payload.messages):
                        yield event
                    return
                except Exception as exc:  # Fallback to the next key without interrupting the UI.
                    last_error = str(exc)
                    if index < len(keys):
                        yield sse("status", {"text": f"فشل مفتاح Gemini رقم {index}. يجرب المفتاح التالي"})
                    else:
                        yield sse("status", {"text": f"فشل آخر مفتاح Gemini"})
                    await asyncio.sleep(0.2)
            yield sse("error", {"message": last_error})

    return StreamingResponse(generator(), media_type="text/event-stream")


@app.get("/{path:path}")
def spa_fallback(path: str) -> FileResponse:
    if path.startswith("api/"):
        raise HTTPException(status_code=404)
    return FileResponse(STATIC_DIR / "index.html")
