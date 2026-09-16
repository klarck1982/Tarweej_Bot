"""تخزين حالة المعالج (FSM) في Postgres بدل الذاكرة.

لماذا؟ عملية Render قد تُعاد في أي لحظة. لو كان العميل في الخطوة 6 من طلب إعلان،
بهذا التخزين يكمل من حيث توقف بعد إعادة التشغيل كأن شيئاً لم يحدث.
المسودات القديمة تُنظَّف بعد 24 ساعة (مهمة دورية في الخطوة 5).
"""

from __future__ import annotations

import json
from typing import Any

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey

from app.db import pool as db


def _k(key: StorageKey) -> str:
    return f"{key.bot_id}:{key.chat_id}:{key.user_id}"


class PostgresStorage(BaseStorage):
    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        value = state.state if isinstance(state, State) else state
        k = _k(key)
        if value is None:
            # نصفّر الحالة، وإن صار الصف فارغاً تماماً نحذفه (استعلامان بسيطان على اتصال واحد)
            async with db.pool().acquire() as conn:
                await conn.execute("UPDATE fsm_state SET state = NULL, updated_at = now() WHERE key = $1", k)
                await conn.execute("DELETE FROM fsm_state WHERE key = $1 AND data = '{}'::jsonb", k)
            return
        await db.execute(
            """
            INSERT INTO fsm_state (key, state, data) VALUES ($1, $2, '{}'::jsonb)
            ON CONFLICT (key) DO UPDATE SET state = EXCLUDED.state, updated_at = now()
            """,
            k, value,
        )

    async def get_state(self, key: StorageKey) -> str | None:
        return await db.fetchval("SELECT state FROM fsm_state WHERE key = $1", _k(key))

    async def set_data(self, key: StorageKey, data: dict[str, Any]) -> None:
        k = _k(key)
        if not data:
            async with db.pool().acquire() as conn:
                await conn.execute("UPDATE fsm_state SET data = '{}'::jsonb, updated_at = now() WHERE key = $1", k)
                await conn.execute("DELETE FROM fsm_state WHERE key = $1 AND state IS NULL", k)
            return
        await db.execute(
            """
            INSERT INTO fsm_state (key, state, data) VALUES ($1, NULL, $2::jsonb)
            ON CONFLICT (key) DO UPDATE SET data = EXCLUDED.data, updated_at = now()
            """,
            k, json.dumps(data, ensure_ascii=False, default=str),
        )

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        raw = await db.fetchval("SELECT data FROM fsm_state WHERE key = $1", _k(key))
        if raw is None:
            return {}
        return json.loads(raw) if isinstance(raw, str) else dict(raw)

    async def update_data(self, key: StorageKey, data: dict[str, Any]) -> dict[str, Any]:
        # دمج على مستوى قاعدة البيانات — ضغطة واحدة = استعلام واحد
        row = await db.fetchrow(
            """
            INSERT INTO fsm_state (key, state, data) VALUES ($1, NULL, $2::jsonb)
            ON CONFLICT (key) DO UPDATE SET data = fsm_state.data || EXCLUDED.data, updated_at = now()
            RETURNING data
            """,
            _k(key), json.dumps(data, ensure_ascii=False, default=str),
        )
        raw = row["data"]
        return json.loads(raw) if isinstance(raw, str) else dict(raw)

    async def close(self) -> None:  # pool يُغلق من main
        return None


async def purge_stale(hours: int = 24) -> int:
    res = await db.execute(
        "DELETE FROM fsm_state WHERE updated_at < now() - ($1 || ' hours')::interval", str(hours)
    )
    try:
        return int(res.split()[-1])
    except Exception:  # noqa: BLE001
        return 0
