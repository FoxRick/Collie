#!/usr/bin/env python3
"""Owner-only complete-week metrics. Uses device_liveness.py's owner credentials."""
import argparse
import json
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

from device_liveness import load_config


def latest_complete_week(now=None):
    today = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date()
    return today - timedelta(days=(today.weekday() + 1) % 7 + 7)


def fetch_report(url, key, week_start):
    request = urllib.request.Request(
        f"{url}/rest/v1/rpc/weekly_product_metrics",
        data=json.dumps({"p_latest_week_start": week_start.isoformat()}).encode(),
        headers={"apikey": key, "Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise SystemExit(
            f"Metrics report failed (HTTP {error.code}). Check owner credentials "
            "and apply the weekly_product_metrics migration before reporting."
        ) from None
    except urllib.error.URLError:
        raise SystemExit("Could not reach the metrics database. Try again when connected.") from None


def format_report(rows, week_start):
    weeks = {row["week_start"]: row for row in rows}
    latest = weeks[week_start.isoformat()]
    previous = weeks[(week_start - timedelta(days=7)).isoformat()]
    started = datetime.fromisoformat(latest["collection_started_at"].replace("Z", "+00:00"))
    comparable = started <= datetime.combine(
        week_start - timedelta(days=7), datetime.min.time(), tzinfo=timezone.utc,
    )
    lines = [f"Latest complete week: {week_start}–{week_start + timedelta(days=6)}, UTC.", "",
             "| Product metric | Latest week | Previous week | Change |",
             "|---|---:|---:|---:|"]
    for label, key in [
        ("Distinct observed active installations", "active_installs"),
        ("Agent runs started", "runs"),
        ("Interactive agent runs started", "interactive_runs"),
        ("Recorded tool calls", "tool_calls"),
    ]:
        current, prior = int(latest[key]), int(previous[key])
        change = f"{(current - prior) / prior:+.1%}" if comparable and prior else "N/A"
        lines.append(f"| {label} | {current:,} | {prior:,} | {change} |")
    lines += ["", (f"Collection began: {started.isoformat()}. "
                   "Weeks overlapping rollout are partial; unavailable history is not backfilled."),
              ("Observed installations, not people. Run/tool counts require the metrics build; "
               "offline counts can arrive up to 34 days later, so reports can change."),
              ("Interactive = chat/plan runs, including messenger chats. "
               "Routine/cron/automation runs are background; subagent runs are excluded. "
               "Tool calls include recorded blocked and failed attempts, including subagents.")]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week-start", type=date.fromisoformat,
                        help="Sunday starting a complete UTC week (default: latest complete week)")
    args = parser.parse_args()
    week_start = args.week_start or latest_complete_week()
    if week_start.weekday() != 6 or week_start > latest_complete_week():
        parser.error("--week-start must be a Sunday in a complete UTC week")
    url, key = load_config()
    print(format_report(fetch_report(url, key, week_start), week_start))


if __name__ == "__main__":
    main()
