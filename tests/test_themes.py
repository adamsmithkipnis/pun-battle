"""The theme catalog, and the variety rules the picker must never break.

The simulations run against the real catalog in themes/, so a theme file
edit that would cause repeats or a too-long post fails the deploy.
"""

import random
import unittest
from collections import Counter, deque
from datetime import datetime, timedelta, timezone

from helpers import config, posts, themes

START = datetime(2026, 1, 1, tzinfo=timezone.utc)
HOUR = timedelta(hours=1)


def simulate(catalog, rounds, seed=1, cooldown=None, repeat_days=None):
    """Pick `rounds` hourly themes; return [(when, theme)].

    Keeps last-use and recent-category state incrementally (themes.choose),
    which is what makes a multi-year run fast enough for every deploy.
    """
    cooldown = config.CATEGORY_COOLDOWN if cooldown is None else cooldown
    rng = random.Random(seed)
    last, recent = {}, deque(maxlen=cooldown)
    out, now = [], START
    for _ in range(rounds):
        theme, _ = themes.choose(catalog, last, list(recent), now, rng,
                                 repeat_days)
        out.append((now, theme))
        last[themes.normalize(theme.text)] = now
        recent.appendleft(theme.category)
        now += HOUR
    return out


class TestCatalog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = themes.load_catalog()

    def test_size(self):
        categories = {t.category for t in self.catalog}
        self.assertGreaterEqual(len(themes.dedupe(self.catalog)), 4000)
        self.assertGreaterEqual(len(categories), 80)

    def test_every_category_is_substantial(self):
        counts = Counter(t.category for t in self.catalog)
        thin = {c: n for c, n in counts.items() if n < 40}
        self.assertEqual(thin, {}, "categories with under 40 themes")

    def test_no_duplicates_anywhere(self):
        seen, dupes = {}, []
        for theme in self.catalog:
            key = themes.normalize(theme.text)
            if key in seen:
                dupes.append(f"{theme.text!r} ({theme.category}) = "
                             f"{seen[key].text!r} ({seen[key].category})")
            else:
                seen[key] = theme
        self.assertEqual(dupes, [], "\n" + "\n".join(dupes))

    def test_theme_format(self):
        bad = [t for t in self.catalog
               if not t.text or len(t.text) > 40 or t.text != t.text.strip()
               or t.text.endswith((".", "!", "?", ","))]
        self.assertEqual(bad, [])

    def test_every_category_has_a_display_name(self):
        for theme in self.catalog:
            self.assertTrue(theme.category_name, theme.category)

    def test_worst_case_post_fits(self):
        """The longest theme, with a many-way tie of maximum-length handles
        and a leaderboard, still fits — and still names the new theme."""
        longest = max(self.catalog, key=lambda t: len(t.text)).text
        handle = "a" * 60 + ".bsky.social"
        awards = [{"handle": f"{i}{handle}", "did": f"did:plc:{i}",
                   "likes": 999, "points": 60 / 7} for i in range(7)]
        leaders = [(f"did:plc:{i}", f"{i}{handle}", 99999.99, 9)
                   for i in range(3)]
        closes = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
        text, _ = posts.build_theme_post(
            longest, closes, {"theme": longest, "awards": awards,
                              "entry_count": 50},
            leaders, posts.pick_hashtags(random.Random(0)))
        self.assertLessEqual(len(text), 300)
        self.assertIn(longest.upper(), text)
        self.assertIn("🏆", text)


class TestPicker(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = themes.dedupe(themes.load_catalog())

    def test_no_repeats_until_the_catalog_is_used_up(self):
        picks = simulate(self.catalog, 4000)
        keys = [themes.normalize(t.text) for _, t in picks]
        self.assertEqual(len(keys), len(set(keys)))

    def test_category_cooldown(self):
        picks = simulate(self.catalog, 4000)
        cooldown = config.CATEGORY_COOLDOWN
        for i, (_, theme) in enumerate(picks):
            window = [t.category for _, t in picks[max(0, i - cooldown):i]]
            self.assertNotIn(theme.category, window,
                             f"round {i}: {theme.category} within {cooldown}")

    def test_never_repeats_within_ninety_days_over_two_years(self):
        picks = simulate(self.catalog, 24 * 365 * 2, seed=7)
        last = {}
        floor = timedelta(days=config.MIN_THEME_REPEAT_DAYS)
        configured = timedelta(days=config.THEME_REPEAT_DAYS)
        for when, theme in picks:
            key = themes.normalize(theme.text)
            if key in last:
                self.assertGreaterEqual(when - last[key], floor, theme.text)
                # With a catalog this size the configured window holds too.
                self.assertGreaterEqual(when - last[key], configured, theme.text)
            last[key] = when

    def test_small_catalog_fails_loudly_instead_of_repeating(self):
        tiny = self.catalog[:48]
        with self.assertRaises(themes.NoThemeAvailable):
            simulate(tiny, 49)

    def test_queue_goes_first_but_respects_the_floor(self):
        rng = random.Random(0)
        queued = themes.Theme("Lighthouses", "custom", "Custom")
        theme, index = themes.pick_next(self.catalog, [], START, rng,
                                        queue=[queued])
        self.assertEqual((theme, index), (queued, 0))

        recent_use = [themes.Use(themes.normalize("Lighthouses"), "custom",
                                 START - timedelta(days=30))]
        theme, index = themes.pick_next(self.catalog, recent_use, START, rng,
                                        queue=[queued])
        self.assertIsNone(index)
        self.assertNotEqual(theme.text, "Lighthouses")

    def test_normalize_merges_near_duplicates(self):
        self.assertEqual(themes.normalize("Owls"), themes.normalize("owl!"))
        self.assertEqual(themes.normalize("The Beach"), themes.normalize("Beach"))
        self.assertEqual(themes.normalize("Salt & Pepper"),
                         themes.normalize("Salt and Pepper"))
        self.assertNotEqual(themes.normalize("Bass"), themes.normalize("Bas"))


class TestRepeatFloor(unittest.TestCase):
    def test_config_rejects_less_than_ninety_days(self):
        with self.assertRaises(ValueError):
            config.check_repeat_days(89)
        self.assertEqual(config.check_repeat_days(90), 90)


if __name__ == "__main__":
    unittest.main()
