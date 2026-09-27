"""Database helpers for helpdesk ticket storage."""

from typing import Any, Optional

import psycopg
from psycopg.rows import dict_row

from core.config import settings

_tables_created = False


# ---------------------------------------------------------------------------
# Table bootstrap
# ---------------------------------------------------------------------------

def ensure_tables() -> None:
    """Create all required tables if they do not already exist."""
    global _tables_created
    if _tables_created:
        return

    with psycopg.connect(settings.POSTGRES_URL, autocommit=True) as conn:
        with conn.cursor() as cur:

            cur.execute("CREATE SCHEMA IF NOT EXISTS helpdesk")

            # Backing sequence for human-readable, strictly unique ticket IDs
            # (TKT-001, TKT-002, ...). NEXTVAL is safe under concurrent callers.
            cur.execute("""
                CREATE SEQUENCE IF NOT EXISTS helpdesk.helpdesk_ticket_seq
                START WITH 1
                INCREMENT BY 1
            """)

            # ── helpdesk_tickets ───────────────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS helpdesk.helpdesk_tickets (
                    id                      SERIAL PRIMARY KEY,
                    ticket_id               VARCHAR(50) UNIQUE,
                    "userId"                VARCHAR(50)  NOT NULL DEFAULT 'helpdesk',
                    status                  VARCHAR(255),
                    message                 VARCHAR(255) NOT NULL,
                    main_category           TEXT,
                    sub_category            TEXT         NOT NULL,
                    duplicate_check         VARCHAR(30)  NOT NULL DEFAULT 'open',
                    need_more_informations  VARCHAR(20)  NOT NULL DEFAULT 'normal',
                    updated_main_category   VARCHAR(30)  NOT NULL DEFAULT 'chat',
                    updated_sub_category    VARCHAR(255),
                    "createdAt"             TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
                    "updatedAt"             TIMESTAMPTZ  NOT NULL DEFAULT NOW()
                )
            """)

            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_helpdesk_tickets_user_id
                ON helpdesk.helpdesk_tickets ("userId")
            """)

            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_helpdesk_tickets_status
                ON helpdesk.helpdesk_tickets (status)
            """)

            # ── solved_helpdesk_tickets ─────────────────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS helpdesk.solved_helpdesk_tickets (
                    id              SERIAL PRIMARY KEY,
                    requirement    TEXT         NOT NULL,
                    answer          TEXT         NOT NULL,
                    "createdAt"     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
                    "updatedAt"     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
                )
            """)

            # ── all_categories ─────────────────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS helpdesk.all_categories (
                    id              SERIAL PRIMARY KEY,
                    category_id     VARCHAR(50)  NOT NULL,
                    category_name   VARCHAR(255) NOT NULL,
                    subcategory     VARCHAR(255) NOT NULL,
                    description     TEXT,
                    keywords        TEXT,
                    example_tickets TEXT
                )
            """)

            # ALTER ... ADD COLUMN IF NOT EXISTS so tables created before these
            # columns existed get migrated in place instead of erroring.
            cur.execute("""
                ALTER TABLE helpdesk.all_categories
                ADD COLUMN IF NOT EXISTS keywords TEXT
            """)
            cur.execute("""
                ALTER TABLE helpdesk.all_categories
                ADD COLUMN IF NOT EXISTS example_tickets TEXT
            """)

            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_all_categories_category_id
                ON helpdesk.all_categories (category_id)
            """)

    _tables_created = True


# ---------------------------------------------------------------------------
# Helpdesk tickets
# ---------------------------------------------------------------------------

def next_ticket_id() -> str:
    """Reserve and return the next sequential ticket id, e.g. 'TKT-001'.

    Backed by a Postgres SEQUENCE, so it stays unique even under concurrent
    callers without needing a max()+1 read-then-write race.
    """
    ensure_tables()

    with psycopg.connect(settings.POSTGRES_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT NEXTVAL('helpdesk.helpdesk_ticket_seq')")
            n = cur.fetchone()[0]
            return f"TKT-{n:03d}"


def create_helpdesk_ticket(
    *,
    user_id: str,
    message: str,
    ticket_id: Optional[str] = None,
    status: Optional[str] = None,
    main_category: Optional[str] = None,
    sub_category: str,
    duplicate_check: str = "open",
    need_more_informations: str = "normal",
    updated_main_category: str = "chat",
    updated_sub_category: Optional[str] = None,
) -> dict[str, Any]:
    """Insert a helpdesk ticket and return the stored row.

    If ticket_id is not supplied, one is drawn from the same sequence used by
    next_ticket_id() (still unique, still sequential) rather than being left
    NULL. Reuses next_ticket_id() itself so there is exactly one place that
    formats ticket ids — no second implementation to drift out of sync.
    """
    ensure_tables()

    if not ticket_id:
        ticket_id = next_ticket_id()

    with psycopg.connect(settings.POSTGRES_URL, autocommit=True) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                INSERT INTO helpdesk.helpdesk_tickets (
                    ticket_id,
                    "userId",
                    status,
                    message,
                    main_category,
                    sub_category,
                    duplicate_check,
                    need_more_informations,
                    updated_main_category,
                    updated_sub_category
                )
                VALUES (
                    %(ticket_id)s,
                    %(user_id)s,
                    %(status)s,
                    %(message)s,
                    %(main_category)s,
                    %(sub_category)s,
                    %(duplicate_check)s,
                    %(need_more_informations)s,
                    %(updated_main_category)s,
                    %(updated_sub_category)s
                )
                RETURNING
                    id,
                    ticket_id,
                    "userId",
                    status,
                    message,
                    main_category,
                    sub_category,
                    duplicate_check,
                    need_more_informations,
                    updated_main_category,
                    updated_sub_category,
                    "createdAt",
                    "updatedAt"
                """,
                {
                    "ticket_id": ticket_id,
                    "user_id": user_id,
                    "status": status,
                    "message": message,
                    "main_category": main_category,
                    "sub_category": sub_category,
                    "duplicate_check": duplicate_check,
                    "need_more_informations": need_more_informations,
                    "updated_main_category": updated_main_category,
                    "updated_sub_category": updated_sub_category,
                },
            )
            return dict(cur.fetchone())


def list_helpdesk_tickets(
    *,
    user_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return recent helpdesk tickets, optionally filtered by userId / status."""
    ensure_tables()

    filters: list[str] = []
    params: dict[str, Any] = {"limit": max(1, min(limit, 500))}

    if user_id:
        filters.append('"userId" = %(user_id)s')
        params["user_id"] = user_id

    if status:
        filters.append("status = %(status)s")
        params["status"] = status

    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""

    with psycopg.connect(settings.POSTGRES_URL, autocommit=True) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                SELECT
                    id,
                    ticket_id,
                    "userId",
                    status,
                    message,
                    main_category,
                    sub_category,
                    duplicate_check,
                    need_more_informations,
                    updated_main_category,
                    updated_sub_category,
                    "createdAt",
                    "updatedAt"
                FROM helpdesk.helpdesk_tickets
                {where_clause}
                ORDER BY "createdAt" DESC
                LIMIT %(limit)s
                """,
                params,
            )
            return [dict(row) for row in cur.fetchall()]


def get_helpdesk_ticket(ticket_id: str) -> Optional[dict[str, Any]]:
    """Return one helpdesk ticket by ticket_id."""
    ensure_tables()

    with psycopg.connect(settings.POSTGRES_URL, autocommit=True) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT
                    id,
                    ticket_id,
                    "userId",
                    status,
                    message,
                    main_category,
                    sub_category,
                    duplicate_check,
                    need_more_informations,
                    updated_main_category,
                    updated_sub_category,
                    "createdAt",
                    "updatedAt"
                FROM helpdesk.helpdesk_tickets
                WHERE ticket_id = %(ticket_id)s
                """,
                {"ticket_id": ticket_id},
            )
            row = cur.fetchone()
            return dict(row) if row else None


def update_helpdesk_ticket(
    ticket_id: str,
    *,
    status: Optional[str] = None,
    main_category: Optional[str] = None,
    sub_category: Optional[str] = None,
    duplicate_check: Optional[str] = None,
    need_more_informations: Optional[str] = None,
    updated_main_category: Optional[str] = None,
    updated_sub_category: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Partially update a helpdesk ticket; only provided fields are changed."""
    ensure_tables()

    field_map = {
        "status": status,
        "main_category": main_category,
        "sub_category": sub_category,
        "duplicate_check": duplicate_check,
        "need_more_informations": need_more_informations,
        "updated_main_category": updated_main_category,
        "updated_sub_category": updated_sub_category,
    }
    updates = {k: v for k, v in field_map.items() if v is not None}
    if not updates:
        return get_helpdesk_ticket(ticket_id)

    set_clause = ", ".join(f'"{k}" = %({k})s' for k in updates)
    updates["ticket_id"] = ticket_id

    with psycopg.connect(settings.POSTGRES_URL, autocommit=True) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                UPDATE helpdesk.helpdesk_tickets
                SET {set_clause}, "updatedAt" = NOW()
                WHERE ticket_id = %(ticket_id)s
                RETURNING
                    id, ticket_id, "userId", status, message,
                    main_category, sub_category, duplicate_check,
                    need_more_informations, updated_main_category,
                    updated_sub_category, "createdAt", "updatedAt"
                """,
                updates,
            )
            row = cur.fetchone()
            return dict(row) if row else None


# ---------------------------------------------------------------------------
# Solved tickets
# ---------------------------------------------------------------------------

def search_solved_tickets(limit: int = 50) -> list[dict[str, Any]]:
    """Fetch recent solved tickets for matching against user queries."""
    ensure_tables()

    with psycopg.connect(settings.POSTGRES_URL, autocommit=True) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT id, requirement, answer, "createdAt", "updatedAt"
                FROM helpdesk.solved_helpdesk_tickets
                ORDER BY "createdAt" DESC
                LIMIT %(limit)s
                """,
                {"limit": max(1, min(limit, 200))},
            )
            return [dict(row) for row in cur.fetchall()]


def create_solved_ticket(
    *,
    requirements: str,
    answer: str,
) -> dict[str, Any]:
    """Insert a solved ticket and return the stored row."""
    ensure_tables()

    with psycopg.connect(settings.POSTGRES_URL, autocommit=True) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                INSERT INTO helpdesk.solved_helpdesk_tickets (requirement, answer)
                VALUES (%(requirement)s, %(answer)s)
                RETURNING id, requirement, answer, "createdAt", "updatedAt"
                """,
                {"requirement": requirements, "answer": answer},
            )
            return dict(cur.fetchone())


def list_solved_tickets(*, limit: int = 100) -> list[dict[str, Any]]:
    """Return recent solved tickets."""
    ensure_tables()

    with psycopg.connect(settings.POSTGRES_URL, autocommit=True) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT id, requirement, answer, "createdAt", "updatedAt"
                FROM helpdesk.solved_helpdesk_tickets
                ORDER BY "createdAt" DESC
                LIMIT %(limit)s
                """,
                {"limit": max(1, min(limit, 500))},
            )
            return [dict(row) for row in cur.fetchall()]


def get_solved_ticket(solved_id: int) -> Optional[dict[str, Any]]:
    """Return one solved ticket by id."""
    ensure_tables()

    with psycopg.connect(settings.POSTGRES_URL, autocommit=True) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT id, requirement, answer, "createdAt", "updatedAt"
                FROM helpdesk.solved_helpdesk_tickets
                WHERE id = %(id)s
                """,
                {"id": solved_id},
            )
            row = cur.fetchone()
            return dict(row) if row else None


# ---------------------------------------------------------------------------
# All categories
# ---------------------------------------------------------------------------

def create_category(
    *,
    category_id: str,
    category_name: str,
    subcategory: str,
    description: Optional[str] = None,
    keywords: Optional[str] = None,
    example_tickets: Optional[str] = None,
) -> dict[str, Any]:
    """Insert a category row and return the stored row."""
    ensure_tables()

    with psycopg.connect(settings.POSTGRES_URL, autocommit=True) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                INSERT INTO helpdesk.all_categories
                    (category_id, category_name, subcategory, description, keywords, example_tickets)
                VALUES
                    (%(category_id)s, %(category_name)s, %(subcategory)s, %(description)s, %(keywords)s, %(example_tickets)s)
                RETURNING id, category_id, category_name, subcategory, description, keywords, example_tickets
                """,
                {
                    "category_id": category_id,
                    "category_name": category_name,
                    "subcategory": subcategory,
                    "description": description,
                    "keywords": keywords,
                    "example_tickets": example_tickets,
                },
            )
            return dict(cur.fetchone())


def list_categories(
    *,
    category_name: Optional[str] = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return categories, optionally filtered by category_name."""
    ensure_tables()

    filters: list[str] = []
    params: dict[str, Any] = {"limit": max(1, min(limit, 500))}

    if category_name:
        filters.append("category_name = %(category_name)s")
        params["category_name"] = category_name

    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""

    with psycopg.connect(settings.POSTGRES_URL, autocommit=True) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                SELECT id, category_id, category_name, subcategory, description, keywords, example_tickets
                FROM helpdesk.all_categories
                {where_clause}
                ORDER BY category_name, subcategory
                LIMIT %(limit)s
                """,
                params,
            )
            return [dict(row) for row in cur.fetchall()]


def get_category(category_id: str) -> Optional[dict[str, Any]]:
    """Return one category row by category_id."""
    ensure_tables()

    with psycopg.connect(settings.POSTGRES_URL, autocommit=True) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT id, category_id, category_name, subcategory, description, keywords, example_tickets
                FROM helpdesk.all_categories
                WHERE category_id = %(category_id)s
                """,
                {"category_id": category_id},
            )
            row = cur.fetchone()
            return dict(row) if row else None