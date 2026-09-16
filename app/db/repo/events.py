"""سجل الأحداث (تدقيق) — يُنظَّف بعد 90 يوماً."""

from __future__ import annotations

import json
from typing import Any

from app.db import pool as db


async def log_event(type_: str, user_id: int | None = None, order_id: int | None = None, **payload: Any) -> None:
    try:
        await db.execute(
            "INSERT INTO events (user_id, order_id, type, payload) VALUES ($1, $2, $3, $4::jsonb)",
            user_id, order_id, type_, json.dumps(payload, ensure_ascii=False, default=str),
        )
    except Exception:  # noqa: BLE001 — السجل لا يجب أن يكسر التجربة أبداً
        pass


async def purge_old(days: int = 90) -> int:
    res = await db.execute("DELETE FROM events WHERE created_at < now() - ($1 || ' days')::interval", str(days))
    try:
        return int(res.split()[-1])
    except Exception:  # noqa: BLE001
        return 0
