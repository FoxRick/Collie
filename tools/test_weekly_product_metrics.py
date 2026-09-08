import unittest
from datetime import date, datetime

from weekly_product_metrics import format_report, latest_complete_week


class WeeklyMetricsTests(unittest.TestCase):
    def test_complete_weeks_at_utc_boundary(self):
        for stamp, expected in [
            ("2026-09-05T23:59:59+00:00", date(2026, 8, 23)),
            ("2026-09-06T00:00:00+00:00", date(2026, 8, 30)),
            ("2026-09-07T09:00:00+08:00", date(2026, 8, 30)),
            ("2026-01-04T00:00:00+00:00", date(2025, 12, 28)),
        ]:
            self.assertEqual(latest_complete_week(datetime.fromisoformat(stamp)), expected)

    def test_growth_zero_denominator_and_partial_rollout(self):
        rows = [{"week_start": day, "active_installs": installs, "runs": 0,
                 "interactive_runs": 0, "tool_calls": 0,
                 "collection_started_at": "2026-08-01T00:00:00Z"}
                for day, installs in [("2026-08-30", 160), ("2026-08-23", 100)]]
        report = format_report(rows, date(2026, 8, 30))
        self.assertIn("+60.0%", report)
        self.assertIn("| Agent runs started | 0 | 0 | N/A |", report)
        rows[0]["collection_started_at"] = "2026-08-24T00:00:00Z"
        self.assertNotIn("+60.0%", format_report(rows, date(2026, 8, 30)))


if __name__ == "__main__":
    unittest.main()
