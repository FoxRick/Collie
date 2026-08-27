#!/usr/bin/env python3
"""
Collie — maker-side device liveness view.

Who is using Collie, and are they live right now? This is the FOUNDER's
ops tool (not a product feature). It reads the `user_sync_snapshots` table —
the same opt-in cloud-sync rows the app owns — and reports presence from the
`last_seen` / `version` / `platform` columns the client heartbeats.

Privacy note: only devices whose user signed in AND turned on sync appear here.
The heartbeat carries a device id + version + platform only (no content, no
conversations). This whole view is data the users already volunteered to send
by enabling sync — there is no covert telemetry.

Usage:
    python3 tools/device_liveness.py                # reads ~/.collie-supabase.env
    SUPABASE_URL=... SUPABASE_SERVICE_KEY=... python3 tools/device_liveness.py

Output is plain text, sorted so it's easy to skim in a terminal or paste into
a message. All timestamps are UTC.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

LIVE_WINDOW_MIN = 10          # "live now" = last_seen within this many minutes
ACTIVE_WINDOW_HRS = 24        # "active today" = last_seen within this many hours


def load_config():
    """URL + service key from env, falling back to ~/.collie-supabase.env."""
    env = dict(os.environ)
    if "SUPABASE_URL" not in env or "SUPABASE_SERVICE_KEY" not in env:
        path = os.path.expanduser("~/.collie-supabase.env")
        if os.path.exists(path):
            with open(path) as fh:
                for line in fh:
                    line = line.strip()
                    if "=" in line and not line.startswith("#"):
                        k, v = line.split("=", 1)
                        env.setdefault(k.strip(), v.strip())
    url = env.get("SUPABASE_URL", "").rstrip("/")
    key = env.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        sys.exit(
            "Missing SUPABASE_URL / SUPABASE_SERVICE_KEY (env or "
            "~/.collie-supabase.env)."
        )
    return url, key


def fetch_devices(url, key):
    """Pull the presence columns for every device row (service key = admin)."""
    params = (
        "device_name,version,platform,last_seen"
        "&order=last_seen.desc.nullsfirst&limit=10000"
    )
    req = urllib.request.Request(
        f"{url}/rest/v1/user_sync_snapshots?select={params}",
        headers={"apikey": key, "Authorization": f"Bearer {key}",
                 "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as err:
        sys.exit(f"Supabase error {err.code}: {err.read().decode()[:300]}")


def parse(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def main():
    url, key = load_config()
    rows = fetch_devices(url, key)
    now = datetime.now(timezone.utc)

    live, active, total = 0, 0, 0
    live_rows, active_rows = [], []
    spread = {}

    for row in rows:
        seen = parse(row.get("last_seen"))
        total += 1
        if seen and (now - seen).total_seconds() <= LIVE_WINDOW_MIN * 60:
            live += 1
            live_rows.append((row.get("device_name") or "Unknown", row.get("version"),
                              row.get("platform"), seen))
        if seen and (now - seen).total_seconds() <= ACTIVE_WINDOW_HRS * 3600:
            active += 1
            active_rows.append((row.get("device_name") or "Unknown", row.get("version"),
                                row.get("platform"), seen))
            key = (row.get("version") or "unknown", row.get("platform") or "unknown")
            spread[key] = spread.get(key, 0) + 1

    has_rows = total > 0
    print("COLLIE DEVICE LIVENESS")
    print("=" * 52)
    print(f"devices on record (sync-opted-in): {total}")
    print(f"live right now (last_seen <= {LIVE_WINDOW_MIN}m): {live}")
    print(f"active last {ACTIVE_WINDOW_HRS}h:               {active}")
    if not has_rows:
        print("\n(nothing yet — devices appear once a signed-in + syncing user")
        print(" runs a build with the heartbeat wired in and it pings.)")
        return

    print("\nLIVE NOW (device | version | platform | last_seen UTC):")
    if not live_rows:
        print("  none")
    for name, ver, plat, seen in sorted(live_rows, key=lambda r: r[3], reverse=True):
        print(f"  {name:24s} {str(ver):12s} {str(plat):10s} {seen:%H:%M:%S}")

    print(f"\nACTIVE {ACTIVE_WINDOW_HRS}h VERSION SPREAD:")
    if not spread:
        print("  none")
    for (ver, plat), count in sorted(spread.items(), key=lambda kv: kv[1], reverse=True):
        print(f"  {str(ver):12s} {str(plat):14s} {count} device(s)")


if __name__ == "__main__":
    main()
