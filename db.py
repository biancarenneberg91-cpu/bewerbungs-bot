"""
Datenbank-Layer (PostgreSQL via asyncpg) für den Bewerbungs-Bot.
Nutzt die DATABASE_URL, die Railway automatisch bereitstellt, sobald
ein PostgreSQL-Plugin im selben Projekt verbunden ist.
"""

import os
import asyncpg
from datetime import datetime, timezone

DATABASE_URL = os.getenv("DATABASE_URL")

_pool: asyncpg.Pool | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id SERIAL PRIMARY KEY,
    applicant_id BIGINT,
    applicant_tag TEXT,
    name TEXT,
    age TEXT,
    discord_name TEXT,
    ingame_name TEXT,
    rang TEXT,
    motivation TEXT,
    erfahrung TEXT,
    stunden TEXT,
    staerken TEXT,
    schwaechen TEXT,
    warum_du TEXT,
    regeln_gelesen TEXT,
    status TEXT DEFAULT 'Ausstehend',
    ai_score INTEGER,
    ai_summary TEXT,
    ai_staerken TEXT,
    ai_risiken TEXT,
    ai_empfehlung TEXT,
    reviewed_by TEXT,
    reviewed_at TIMESTAMPTZ,
    submitted_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status);
CREATE INDEX IF NOT EXISTS idx_applications_submitted_at ON applications(submitted_at DESC);
"""


async def init_db() -> None:
    """Pool erstellen und Schema sicherstellen. Beim Bot-Start (on_ready) aufrufen."""
    global _pool
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL fehlt - PostgreSQL-Plugin in Railway verbinden!")
    _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    async with _pool.acquire() as conn:
        await conn.execute(SCHEMA)


async def save_application(answers: dict, ai_result: dict) -> int:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO applications (
                applicant_id, applicant_tag, name, age, discord_name, ingame_name, rang,
                motivation, erfahrung, stunden, staerken, schwaechen, warum_du, regeln_gelesen,
                status, ai_score, ai_summary, ai_staerken, ai_risiken, ai_empfehlung, submitted_at
            ) VALUES (
                $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21
            ) RETURNING id
            """,
            answers.get("applicant_id"),
            answers.get("applicant_tag"),
            answers.get("name"),
            answers.get("alter"),
            answers.get("discord_name"),
            answers.get("ingame_name"),
            answers.get("rang"),
            answers.get("motivation"),
            answers.get("erfahrung"),
            answers.get("stunden"),
            answers.get("staerken"),
            answers.get("schwaechen"),
            answers.get("warum_du"),
            answers.get("regeln_gelesen"),
            "Ausstehend",
            ai_result.get("score"),
            ai_result.get("summary"),
            str(ai_result.get("staerken_erkannt", "")),
            str(ai_result.get("risiken", "")),
            ai_result.get("empfehlung"),
            datetime.now(timezone.utc),
        )
        return row["id"]


async def update_application_status(app_id: int, status: str, reviewer: str) -> None:
    async with _pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE applications
            SET status = $1, reviewed_by = $2, reviewed_at = $3
            WHERE id = $4
            """,
            status, reviewer, datetime.now(timezone.utc), app_id,
        )


async def get_recent_applications(limit: int = 10) -> list:
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM applications ORDER BY id DESC LIMIT $1", limit
        )
        return [dict(r) for r in rows]
