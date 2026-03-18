"""
Dashboard data generator for the Setapp Rating Monitor.
Produces JSON data that powers the HTML dashboard.
"""
import json
import os
from datetime import datetime, timedelta
from . import database, config


def get_dashboard_data() -> dict:
    """Build the full data payload for the dashboard."""
    conn = database.get_connection()

    snapshot_dates = database.get_distinct_dates()
    latest_date = snapshot_dates[-1] if snapshot_dates else None
    previous_date = snapshot_dates[-2] if len(snapshot_dates) >= 2 else None

    # Bottom 10 apps (exclude 0% which are unrated)
    bottom10 = conn.execute("""
        SELECT a.app_name, a.app_slug, a.app_url,
               s.rating_score, s.rating_count, s.snapshot_date
        FROM apps a
        JOIN rating_snapshots s ON a.id = s.app_id
        WHERE s.snapshot_date = (SELECT MAX(snapshot_date) FROM rating_snapshots)
          AND s.rating_score IS NOT NULL AND s.rating_score > 0
        ORDER BY s.rating_score ASC
        LIMIT 10
    """).fetchall()

    # Top 10 best apps for comparison
    top10 = conn.execute("""
        SELECT a.app_name, a.app_slug, a.app_url,
               s.rating_score, s.rating_count, s.snapshot_date
        FROM apps a
        JOIN rating_snapshots s ON a.id = s.app_id
        WHERE s.snapshot_date = (SELECT MAX(snapshot_date) FROM rating_snapshots)
          AND s.rating_score IS NOT NULL
        ORDER BY s.rating_score DESC
        LIMIT 10
    """).fetchall()

    # Overall stats (rated apps only)
    stats_row = conn.execute("""
        SELECT COUNT(DISTINCT app_id) as total,
               AVG(rating_score) as avg_rating,
               MIN(rating_score) as min_rating,
               MAX(rating_score) as max_rating
        FROM rating_snapshots
        WHERE rating_score > 0
          AND snapshot_date = (SELECT MAX(snapshot_date) FROM rating_snapshots)
    """).fetchone()

    # Total discovered apps (including unrated)
    total_discovered = conn.execute("""
        SELECT COUNT(DISTINCT app_id) as total
        FROM rating_snapshots
        WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM rating_snapshots)
    """).fetchone()["total"]

    # Rating distribution buckets
    buckets = conn.execute("""
        SELECT
            CASE
                WHEN rating_score >= 95 THEN '95-100'
                WHEN rating_score >= 90 THEN '90-94'
                WHEN rating_score >= 80 THEN '80-89'
                WHEN rating_score >= 70 THEN '70-79'
                WHEN rating_score >= 60 THEN '60-69'
                WHEN rating_score < 60 THEN 'Below 60'
            END as bucket,
            COUNT(*) as count
        FROM rating_snapshots
        WHERE rating_score > 0
          AND snapshot_date = (SELECT MAX(snapshot_date) FROM rating_snapshots)
        GROUP BY bucket
        ORDER BY MIN(rating_score) DESC
    """).fetchall()

    # All apps grouped by bucket (for interactive distribution drill-down)
    all_apps = conn.execute("""
        SELECT a.app_name, a.app_slug, a.app_url,
               s.rating_score, s.rating_count,
               CASE
                   WHEN s.rating_score >= 95 THEN '95-100'
                   WHEN s.rating_score >= 90 THEN '90-94'
                   WHEN s.rating_score >= 80 THEN '80-89'
                   WHEN s.rating_score >= 70 THEN '70-79'
                   WHEN s.rating_score >= 60 THEN '60-69'
                   WHEN s.rating_score < 60 THEN 'Below 60'
               END as bucket
        FROM apps a
        JOIN rating_snapshots s ON a.id = s.app_id
        WHERE s.snapshot_date = (SELECT MAX(snapshot_date) FROM rating_snapshots)
          AND s.rating_score IS NOT NULL AND s.rating_score > 0
        ORDER BY s.rating_score DESC
    """).fetchall()

    apps_by_bucket = {}
    for row in all_apps:
        b = row["bucket"]
        if b not in apps_by_bucket:
            apps_by_bucket[b] = []
        apps_by_bucket[b].append({
            "app_name": row["app_name"],
            "app_slug": row["app_slug"],
            "app_url": row["app_url"],
            "rating_score": row["rating_score"],
            "rating_count": row["rating_count"],
        })

    # ── 24-Hour Changes ──
    # Compare latest snapshot to the previous day's snapshot for every app
    changes_24h = []
    if latest_date and previous_date:
        rows = conn.execute("""
            SELECT a.app_name, a.app_slug, a.app_url,
                   curr.rating_score AS current_rating,
                   curr.rating_count AS current_count,
                   prev.rating_score AS previous_rating,
                   prev.rating_count AS previous_count
            FROM apps a
            JOIN rating_snapshots curr ON a.id = curr.app_id AND curr.snapshot_date = ?
            JOIN rating_snapshots prev ON a.id = prev.app_id AND prev.snapshot_date = ?
            WHERE curr.rating_score IS NOT NULL AND prev.rating_score IS NOT NULL
              AND curr.rating_score > 0 AND prev.rating_score > 0
              AND curr.rating_score != prev.rating_score
            ORDER BY (curr.rating_score - prev.rating_score) ASC
        """, (latest_date, previous_date)).fetchall()
        for r in rows:
            change = r["current_rating"] - r["previous_rating"]
            changes_24h.append({
                "app_name": r["app_name"],
                "app_slug": r["app_slug"],
                "app_url": r["app_url"],
                "current_rating": r["current_rating"],
                "previous_rating": r["previous_rating"],
                "change": round(change, 1),
                "direction": "up" if change > 0 else "down",
                "current_count": r["current_count"],
                "previous_count": r["previous_count"],
            })

    # ── All Apps Table (with 7-day comparison) ──
    seven_days_ago = (datetime.strptime(latest_date, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d") if latest_date else None
    all_apps_table = []

    all_current = conn.execute("""
        SELECT a.id, a.app_name, a.app_slug, a.app_url,
               s.rating_score, s.rating_count
        FROM apps a
        JOIN rating_snapshots s ON a.id = s.app_id
        WHERE s.snapshot_date = ?
          AND s.rating_score IS NOT NULL AND s.rating_score > 0
        ORDER BY a.app_name
    """, (latest_date,)).fetchall() if latest_date else []

    for app in all_current:
        # Get 7-day-ago snapshot (closest on or before)
        old_snap = None
        if seven_days_ago:
            old_snap = conn.execute("""
                SELECT rating_score, rating_count FROM rating_snapshots
                WHERE app_id = ? AND snapshot_date <= ?
                ORDER BY snapshot_date DESC LIMIT 1
            """, (app["id"], seven_days_ago)).fetchone()

        old_rating = old_snap["rating_score"] if old_snap else None
        change_7d = round(app["rating_score"] - old_rating, 1) if old_rating else None
        direction_7d = None
        if change_7d is not None:
            if change_7d > 0:
                direction_7d = "up"
            elif change_7d < 0:
                direction_7d = "down"
            else:
                direction_7d = "stable"

        all_apps_table.append({
            "app_name": app["app_name"],
            "app_slug": app["app_slug"],
            "app_url": app["app_url"],
            "rating_score": app["rating_score"],
            "rating_count": app["rating_count"],
            "rating_7d_ago": old_rating,
            "change_7d": change_7d,
            "direction_7d": direction_7d,
        })

    # ── Apps At Risk ──
    # Apps with: score < 70, OR 7-day drop > 5 points, OR consecutive drops
    at_risk_apps = []
    stable_apps = []
    for app_row in all_apps_table:
        is_at_risk = False
        risk_reasons = []

        if app_row["rating_score"] < 70:
            is_at_risk = True
            risk_reasons.append("Rating below 70%")

        if app_row["change_7d"] is not None and app_row["change_7d"] <= -5:
            is_at_risk = True
            risk_reasons.append(f"Dropped {abs(app_row['change_7d'])} pts in 7 days")

        if is_at_risk:
            at_risk_apps.append({**app_row, "risk_reasons": risk_reasons})
        elif app_row["change_7d"] is not None and abs(app_row["change_7d"]) <= 1:
            stable_apps.append(app_row)

    # Sort at-risk by rating ascending
    at_risk_apps.sort(key=lambda x: x["rating_score"])
    # Sort stable by rating descending
    stable_apps.sort(key=lambda x: -x["rating_score"])

    # Historical data for bottom 10 (for sparklines)
    history = {}
    for app in bottom10:
        slug = app["app_slug"]
        rows = conn.execute("""
            SELECT s.snapshot_date, s.rating_score
            FROM rating_snapshots s
            JOIN apps a ON a.id = s.app_id
            WHERE a.app_slug = ? AND s.rating_score IS NOT NULL
            ORDER BY s.snapshot_date ASC
        """, (slug,)).fetchall()
        history[slug] = [{"date": r["snapshot_date"], "rating": r["rating_score"]} for r in rows]

    conn.close()

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "snapshot_date": latest_date or "N/A",
        "previous_date": previous_date,
        "snapshot_count": len(snapshot_dates),
        "total_apps": stats_row["total"] if stats_row else 0,
        "total_discovered": total_discovered,
        "avg_rating": round(stats_row["avg_rating"], 1) if stats_row and stats_row["avg_rating"] else 0,
        "min_rating": stats_row["min_rating"] if stats_row else 0,
        "max_rating": stats_row["max_rating"] if stats_row else 0,
        "bottom10": [dict(r) for r in bottom10],
        "top10": [dict(r) for r in top10],
        "distribution": [dict(r) for r in buckets],
        "apps_by_bucket": apps_by_bucket,
        "changes_24h": changes_24h,
        "all_apps": all_apps_table,
        "at_risk": at_risk_apps,
        "stable": stable_apps,
        "history": history,
    }


def save_dashboard_data(data: dict) -> str:
    """Save dashboard JSON data file."""
    filepath = os.path.join(config.REPORTS_DIR, "dashboard_data.json")
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)
    return filepath
