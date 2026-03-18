"""
Trend analysis and risk detection module.
Analyzes historical rating data to detect quality issues.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional
from . import database, config

logger = logging.getLogger(__name__)


def calculate_trend(snapshots: list[dict]) -> dict:
    """
    Calculate trend metrics from a list of snapshots (ordered by date ASC).

    Returns:
        - first_date / last_date
        - first_rating / last_rating
        - rating_change (absolute)
        - rating_change_pct (percentage)
        - first_count / last_count
        - count_increase
        - trend_direction: "up" | "stable" | "down"
        - daily_changes: list of day-over-day rating changes
    """
    if len(snapshots) < 2:
        return {
            "first_date": snapshots[0]["snapshot_date"] if snapshots else None,
            "last_date": snapshots[-1]["snapshot_date"] if snapshots else None,
            "first_rating": snapshots[0]["rating_score"] if snapshots else None,
            "last_rating": snapshots[-1]["rating_score"] if snapshots else None,
            "rating_change": 0,
            "rating_change_pct": 0,
            "first_count": snapshots[0].get("rating_count") if snapshots else None,
            "last_count": snapshots[-1].get("rating_count") if snapshots else None,
            "count_increase": 0,
            "trend_direction": "stable",
            "daily_changes": [],
            "insufficient_data": True,
        }

    first = snapshots[0]
    last = snapshots[-1]

    first_rating = first["rating_score"]
    last_rating = last["rating_score"]

    if first_rating is not None and last_rating is not None:
        rating_change = last_rating - first_rating
        rating_change_pct = (
            (rating_change / first_rating * 100) if first_rating != 0 else 0
        )
    else:
        rating_change = 0
        rating_change_pct = 0

    first_count = first.get("rating_count")
    last_count = last.get("rating_count")
    count_increase = (
        (last_count - first_count)
        if first_count is not None and last_count is not None
        else 0
    )

    # Calculate daily changes
    daily_changes = []
    for i in range(1, len(snapshots)):
        prev = snapshots[i - 1]["rating_score"]
        curr = snapshots[i]["rating_score"]
        if prev is not None and curr is not None:
            daily_changes.append({
                "date": snapshots[i]["snapshot_date"],
                "change": curr - prev,
            })

    # Determine trend direction
    if rating_change_pct < -2:
        direction = "down"
    elif rating_change_pct > 2:
        direction = "up"
    else:
        direction = "stable"

    return {
        "first_date": first["snapshot_date"],
        "last_date": last["snapshot_date"],
        "first_rating": first_rating,
        "last_rating": last_rating,
        "rating_change": round(rating_change, 2),
        "rating_change_pct": round(rating_change_pct, 2),
        "first_count": first_count,
        "last_count": last_count,
        "count_increase": count_increase,
        "trend_direction": direction,
        "daily_changes": daily_changes,
        "insufficient_data": False,
    }


def detect_risk(app: dict, trend: dict) -> dict:
    """
    Determine if an app is "at risk" based on rating trends.

    Risk conditions:
      1. Rating drops by 20%+ in the last 7 days
      2. Rating drops consistently for 3+ consecutive days

    Returns:
        - is_at_risk: bool
        - risk_reasons: list of strings
        - risk_level: "high" | "medium" | "low" | "none"
    """
    reasons = []
    risk_level = "none"

    if trend.get("insufficient_data"):
        return {"is_at_risk": False, "risk_reasons": ["Insufficient data"], "risk_level": "none"}

    # Condition 1: Rating drops by 20%+
    pct_change = trend["rating_change_pct"]
    if pct_change <= -config.RISK_RATING_DROP_PERCENT:
        reasons.append(
            f"Rating dropped {abs(pct_change):.1f}% over 7 days "
            f"(from {trend['first_rating']} to {trend['last_rating']})"
        )
        risk_level = "high"

    # Condition 2: Consecutive daily drops
    daily = trend.get("daily_changes", [])
    if len(daily) >= config.RISK_CONSECUTIVE_DROP_DAYS:
        consecutive_drops = 0
        max_consecutive = 0
        for dc in daily:
            if dc["change"] < 0:
                consecutive_drops += 1
                max_consecutive = max(max_consecutive, consecutive_drops)
            else:
                consecutive_drops = 0

        if max_consecutive >= config.RISK_CONSECUTIVE_DROP_DAYS:
            reasons.append(
                f"Rating declined for {max_consecutive} consecutive days"
            )
            if risk_level == "none":
                risk_level = "medium"

    # Additional signal: large increase in rating count with rating drop
    # (suggests influx of negative reviews)
    if trend["count_increase"] and trend["count_increase"] > 100 and trend["rating_change"] < -2:
        reasons.append(
            f"Surge of {trend['count_increase']} new ratings "
            f"coinciding with rating decline"
        )

    return {
        "is_at_risk": len(reasons) > 0,
        "risk_reasons": reasons,
        "risk_level": risk_level,
    }


def generate_ai_summary(app: dict, trend: dict, risk: dict) -> str:
    """
    Generate an AI root cause analysis summary for at-risk apps.
    Uses heuristic reasoning based on the data patterns.
    """
    if not risk["is_at_risk"]:
        return "No issues detected."

    app_name = app["app_name"]
    sections = []

    sections.append(f"**{app_name}** has been flagged as at risk ({risk['risk_level']} severity).")

    # Describe what happened
    if trend["first_rating"] is not None and trend["last_rating"] is not None:
        sections.append(
            f"The rating moved from {trend['first_rating']}% to {trend['last_rating']}% "
            f"over the period {trend['first_date']} to {trend['last_date']} "
            f"(change: {trend['rating_change']:+.1f} points, {trend['rating_change_pct']:+.1f}%)."
        )

    # Analyze patterns
    daily = trend.get("daily_changes", [])
    if daily:
        drops = [d for d in daily if d["change"] < 0]
        if len(drops) > len(daily) * 0.7:
            sections.append(
                "The decline has been persistent, with the majority of tracked days "
                "showing negative movement. This suggests an ongoing issue rather than "
                "a one-time event."
            )
        elif drops and abs(min(d["change"] for d in drops)) > 5:
            sections.append(
                "A sharp single-day drop was observed, which may indicate a specific "
                "triggering event such as a buggy update or service outage."
            )

    # Hypothesize root causes
    causes = []

    if trend["count_increase"] and trend["count_increase"] > 50:
        causes.append(
            f"A significant increase in rating volume (+{trend['count_increase']} ratings) "
            "suggests heightened user engagement — possibly driven by a recent update "
            "that introduced issues, or increased visibility bringing in new users "
            "with different expectations."
        )

    if trend["rating_change"] and trend["rating_change"] < -10:
        causes.append(
            "A large absolute rating drop (>10 points) often correlates with: "
            "a breaking software update, loss of key features, "
            "performance degradation, or compatibility issues with a new OS version."
        )

    if trend["rating_change"] and -10 <= trend["rating_change"] < -3:
        causes.append(
            "A moderate rating decline (3-10 points) may indicate: "
            "minor bugs introduced in a recent update, gradual UX deterioration, "
            "rising competition shifting user expectations, or pricing concerns."
        )

    if not causes:
        causes.append(
            "Possible causes include: recent software updates with unresolved bugs, "
            "changes in pricing or subscription model, compatibility issues, "
            "or shifts in the competitive landscape."
        )

    sections.append("\n**Possible root causes:**\n" + "\n".join(f"- {c}" for c in causes))

    # Recommendations
    sections.append(
        "\n**Recommended actions:**\n"
        f"- Review recent release notes and changelogs for {app_name}\n"
        "- Check user reviews for recurring complaint themes\n"
        "- Monitor the next 3-5 days for trend continuation or recovery\n"
        "- Consider reaching out to the developer if the decline persists"
    )

    return "\n\n".join(sections)


def run_full_analysis() -> list[dict]:
    """
    Run trend analysis and risk detection for all tracked apps.
    Returns a list of analysis results for each app.
    """
    apps = database.get_all_apps()
    results = []

    for app in apps:
        snapshots = database.get_snapshots_for_period(
            app["id"], days=config.TREND_WINDOW_DAYS
        )

        trend = calculate_trend(snapshots)
        risk = detect_risk(app, trend)

        summary = ""
        if risk["is_at_risk"]:
            summary = generate_ai_summary(app, trend, risk)

        results.append({
            "app": app,
            "trend": trend,
            "risk": risk,
            "ai_summary": summary,
        })

    # Sort: at-risk apps first, then by severity
    severity_order = {"high": 0, "medium": 1, "low": 2, "none": 3}
    results.sort(key=lambda r: (
        severity_order.get(r["risk"]["risk_level"], 3),
        r["trend"]["rating_change_pct"],
    ))

    return results
