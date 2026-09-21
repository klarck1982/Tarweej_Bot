"""جدول settings: إعدادات العمل القابلة للتعديل من البوت بدون إعادة نشر.

نحتفظ بنسخة في الذاكرة لمدة 60 ثانية حتى لا نوقظ Neon مع كل ضغطة زر.
"""

from __future__ import annotations

import json
import time
from typing import Any

from app.db import pool as db

_cache: dict[str, tuple[float, Any]] = {}
_TTL = 60.0


async def get(key: str, default: Any = None) -> Any:
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and hit[0] > now:
        return hit[1]
    raw = await db.fetchval("SELECT value FROM settings WHERE key = $1", key)
    value = default if raw is None else (json.loads(raw) if isinstance(raw, str) else raw)
    _cache[key] = (now + _TTL, value)
    return value


async def set_(key: str, value: Any) -> None:
    await db.execute(
        """
        INSERT INTO settings (key, value, updated_at) VALUES ($1, $2::jsonb, now())
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
        """,
        key, json.dumps(value, ensure_ascii=False),
    )
    _cache.pop(key, None)


async def get_many(keys: list[str]) -> dict[str, Any]:
    """قراءة عدة مفاتيح باستعلام واحد (تتجاوز الذاكرة المؤقتة وتحدّثها)."""
    rows = await db.fetch("SELECT key, value FROM settings WHERE key = ANY($1::text[])", list(keys))
    now = time.monotonic()
    out: dict[str, Any] = {}
    for r in rows:
        raw = r["value"]
        out[r["key"]] = json.loads(raw) if isinstance(raw, str) else raw
        _cache[r["key"]] = (now + _TTL, out[r["key"]])
    return out


def invalidate(key: str | None = None) -> None:
    if key is None:
        _cache.clear()
    else:
        _cache.pop(key, None)


async def services() -> dict[str, bool]:
    """أي الخدمات مفعّلة الآن — الخدمة المتوقفة تظهر بزر 🔒 لا تختفي."""
    default = {"meta": True, "tg_ads": True, "tg_post": True, "addons": True, "ai_reel": False, "scheduled": True}
    val = await get("services", default)
    return {**default, **(val or {})}
