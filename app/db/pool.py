"""اتصال Postgres (Neon) + الترحيلات التلقائية.

قواعد مهمة للخطة المجانية:
- pool صغير (1–3 اتصالات) وعمر قصير للاتصال الخامل، حتى تنام Neon بعد 5 دقائق هدوء.
- إعادة محاولة واحدة تلقائياً عند انقطاع الاتصال (الإقلاع البارد لـ Neon ≈ ثانية).
- الترحيلات ملفات SQL مرقّمة في app/db/migrations تُطبَّق مرة واحدة عند الإقلاع.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import asyncpg

log = logging.getLogger("db")

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_pool: asyncpg.Pool | None = None


def _clean_dsn(dsn: str) -> str:
    """Neon يعطي رابطاً فيه channel_binding=require — asyncpg يمرّره خطأً كإعداد خادم، فنحذفه."""
    if "channel_binding=" not in dsn:
        return dsn
    base, _, query = dsn.partition("?")
    params = [p for p in query.split("&") if p and not p.startswith("channel_binding=")]
    return base + ("?" + "&".join(params) if params else "")


async def init_pool(dsn: str) -> asyncpg.Pool:
    global _pool
    if _pool is not None:
        return _pool
    last_err: Exception | None = None
    for attempt in range(1, 4):
        try:
            _pool = await asyncpg.create_pool(
                _clean_dsn(dsn),
                min_size=1,
                max_size=3,
                max_inactive_connection_lifetime=60,
                command_timeout=30,
            )
            log.info("Postgres pool ready (attempt %s)", attempt)
            return _pool
        except Exception as e:  # noqa: BLE001 — نريد أي خطأ شبكة/مصادقة
            last_err = e
            log.warning("DB connect failed (attempt %s/3): %s", attempt, e)
            await asyncio.sleep(2 * attempt)
    raise RuntimeError(f"تعذّر الاتصال بقاعدة البيانات: {last_err}")


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool غير مهيّأ — استدعِ init_pool أولاً")
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


# ───────────── تنفيذ مع إعادة محاولة واحدة ─────────────

_RETRYABLE = (
    asyncpg.exceptions.ConnectionDoesNotExistError,
    asyncpg.exceptions.InterfaceError,
    asyncpg.exceptions.CannotConnectNowError,
    asyncpg.exceptions.AdminShutdownError,
    ConnectionResetError,
    OSError,
)


async def _retry(op, *args: Any):
    try:
        return await op(*args)
    except _RETRYABLE as e:
        log.warning("DB op failed, retrying once: %s", e)
        await asyncio.sleep(1.0)
        return await op(*args)


async def fetch(query: str, *args: Any) -> list[asyncpg.Record]:
    return await _retry(pool().fetch, query, *args)


async def fetchrow(query: str, *args: Any) -> asyncpg.Record | None:
    return await _retry(pool().fetchrow, query, *args)


async def fetchval(query: str, *args: Any) -> Any:
    return await _retry(pool().fetchval, query, *args)


async def execute(query: str, *args: Any) -> str:
    return await _retry(pool().execute, query, *args)


# ───────────── الترحيلات ─────────────

async def run_migrations() -> list[str]:
    """يطبّق كل ملف SQL في migrations لم يُطبَّق بعد. يعيد أسماء الملفات المطبَّقة الآن."""
    p = pool()
    applied_now: list[str] = []
    async with p.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version    TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        # قفل استشاري: لو أقلعت نسختان معاً أثناء النشر، واحدة فقط ترحّل
        await conn.execute("SELECT pg_advisory_lock(727_001)")
        try:
            done = {r["version"] for r in await conn.fetch("SELECT version FROM schema_migrations")}
            for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                if path.name in done:
                    continue
                sql = path.read_text(encoding="utf-8")
                async with conn.transaction():
                    await conn.execute(sql)
                    await conn.execute(
                        "INSERT INTO schema_migrations(version) VALUES($1)", path.name
                    )
                applied_now.append(path.name)
                log.info("migration applied: %s", path.name)
        finally:
            await conn.execute("SELECT pg_advisory_unlock(727_001)")
    return applied_now
