"""
Database layer for storing and querying app rating snapshots.
Uses SQLite for simplicity and portability.
"""
import sqlite3
from datetime import datetime, timedelta
from typing import Optional
from . import config


def get_connection() -> sqlite3.Connection:
    """Get a database connection with row factory enabled."""
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=DELETE")
    return conn


def init_db():
    """Create tables if they don't exist."""
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS apps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            app_name TEXT NOT NULL,
            app_slug TEXT UNIQUE NOT NULL,
            app_url TEXT NOT NULL,
            developer TEXT,
            first_seen DATE NOT NULL,
            last_seen DATE NOT NULL
        );

        CREATE TABLE IF NOT EXISTS rating_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            app_id INTEGER NOT NULL,
            snapshot_date DATE NOT NULL,
            rating_score REAL,
            rating_count INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (app_id) REFERENCES apps(id),
            UNIQUE(app_id, snapshot_date)
        );

        CREATE INDEX IF NOT EXISTS idx_snapshots_app_date
            ON rating_snapshots(app_id, snapshot_date);

        CREATE INDEX IF NOT EXISTS idx_snapshots_date
            ON rating_snapshots(snapshot_date);
    """)
    conn.commit()
    conn.close()


def upsert_app(app_name: str, app_slug: str, app_url: str,
               developer: Optional[str] = None) -> int:
    """Insert or update an app record. Returns the app ID."""
    conn = get_connection()
    today = datetime.now().strftime("%Y-%m-%d")

    cursor = conn.execute(
        "SELECT id FROM apps WHERE app_slug = ?", (app_slug,)
    )
    row = cursor.fetchone()

    if row:
        app_id = row["id"]
        conn.execute(
            "UPDATE apps SET app_name=?, app_url=?, developer=COALESCE(?, developer), last_seen=? WHERE id=?",
            (app_name, app_url, developer, today, app_id)
        )
    else:
        cursor = conn.execute(
            "INSERT INTO apps (app_name, app_slug, app_url, developer, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?)",
            (app_name, app_slug, app_url, developer, today, today)
        )
        app_id = cursor.lastrowid

    conn.commit()
    conn.close()
    return app_id


def insert_snapshot(app_id: int, rating_score: Optional[float],
                    rating_count: Optional[int],
                    snapshot_date: Optional[str] = None):
    """Insert a rating snapshot for an app."""
    if snapshot_date is None:
        snapshot_date = datetime.now().strftime("%Y-%m-%d")

    conn = get_connection()
    conn.execute("""
        INSERT OR REPLACE INTO rating_snapshots
            (app_id, snapshot_date, rating_score, rating_count)
        VALUES (?, ?, ?, ?)
    """, (app_id, snapshot_date, rating_score, rating_count))
    conn.commit()
    conn.close()


def get_snapshots_for_period(app_id: int, days: int = 7) -> list[dict]:
    """Get rating snapshots for an app over the last N days."""
    conn = get_connection()
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    rows = conn.execute("""
        SELECT snapshot_date, rating_score, rating_count
        FROM rating_snapshots
        WHERE app_id = ? AND snapshot_date >= ?
        ORDER BY snapshot_date ASC
    """, (app_id, start_date)).fetchall()

    conn.close()
    return [dict(r) for r in rows]


def get_all_apps() -> list[dict]:
    """Get all tracked apps."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM apps ORDER BY app_name"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_latest_snapshot(app_id: int) -> Optional[dict]:
    """Get the most recent snapshot for an app."""
    conn = get_connection()
    row = conn.execute("""
        SELECT * FROM rating_snapshots
        WHERE app_id = ?
        ORDER BY snapshot_date DESC
        LIMIT 1
    """, (app_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_snapshot_on_date(app_id: int, date_str: str) -> Optional[dict]:
    """Get snapshot closest to a specific date (on or before)."""
    conn = get_connection()
    row = conn.execute("""
        SELECT * FROM rating_snapshots
        WHERE app_id = ? AND snapshot_date <= ?
        ORDER BY snapshot_date DESC
        LIMIT 1
    """, (app_id, date_str)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_snapshot_count() -> int:
    """Get total number of snapshots in the database."""
    conn = get_connection()
    row = conn.execute("SELECT COUNT(*) as cnt FROM rating_snapshots").fetchone()
    conn.close()
    return row["cnt"]


def get_distinct_dates() -> list[str]:
    """Get all distinct snapshot dates."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT DISTINCT snapshot_date FROM rating_snapshots ORDER BY snapshot_date"
    ).fetchall()
    conn.close()
    return [r["snapshot_date"] for r in rows]
