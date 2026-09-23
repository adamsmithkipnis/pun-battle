"""Themes go up on the hour, and only on the hour."""

import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from helpers import config, main

LA = ZoneInfo("America/Los_Angeles")


def local(*args):
    return datetime(*args, tzinfo=LA)


class TestBoundaries(unittest.TestCase):
    def test_next_boundary_is_the_next_top_of_the_hour(self):
        cases = [
            (local(2026, 9, 22, 15, 40), local(2026, 9, 22, 16, 0)),
            (local(2026, 9, 22, 15, 0), local(2026, 9, 22, 16, 0)),
            (local(2026, 9, 22, 15, 0, 0, 300000), local(2026, 9, 22, 16, 0)),
            (local(2026, 9, 22, 23, 59, 59), local(2026, 9, 23, 0, 0)),
        ]
        for now, expected in cases:
            with self.subTest(now=now):
                got = main.next_boundary(now, 60, LA)
                self.assertEqual(got, expected)
                self.assertEqual((got.minute, got.second), (0, 0))

    def test_spring_forward(self):
        # 2027-03-14: 2:00 AM PST jumps to 3:00 AM PDT.
        got = main.next_boundary(local(2027, 3, 14, 1, 30), 60, LA)
        self.assertEqual(got.astimezone(timezone.utc),
                         datetime(2027, 3, 14, 10, 0, tzinfo=timezone.utc))
        self.assertEqual((got.hour, got.minute), (3, 0))

    def test_half_hour_rounds(self):
        self.assertEqual(main.next_boundary(local(2026, 9, 22, 15, 10), 30, LA),
                         local(2026, 9, 22, 15, 30))

    def test_config_rejects_uneven_round_lengths(self):
        self.assertEqual(60 % config.ROUND_MINUTES, 0)


class TestTrigger(unittest.TestCase):
    def test_a_boot_at_340_first_fires_at_400(self):
        trigger = main.make_trigger()
        booted = datetime(2026, 9, 22, 15, 40, 12,
                          tzinfo=ZoneInfo(config.TIMEZONE))
        fire = trigger.get_next_fire_time(None, booted)
        self.assertEqual((fire.hour, fire.minute, fire.second), (16, 0, 0))

    def test_fires_every_hour_on_the_hour(self):
        trigger = main.make_trigger()
        t = datetime(2026, 9, 22, 0, 0, 1, tzinfo=ZoneInfo(config.TIMEZONE))
        fires = []
        for _ in range(24):
            t = trigger.get_next_fire_time(None, t)
            fires.append(t)
            t = t.replace(second=1)
        self.assertTrue(all((f.minute, f.second) == (0, 0) for f in fires))
        self.assertEqual(len({f.hour for f in fires}), 24)


if __name__ == "__main__":
    unittest.main()
