"""The theme catalog, and the variety rules the picker must never break.

The catalog tests and the long simulation run against the real files in
themes/, so an edit that would cause repeats, a too-thin topic or a too-long
post fails the deploy. The rule tests use small made-up catalogs.
"""

import random
import unittest
from collections import Counter
from datetime import datetime, timedelta, timezone

from helpers import config, posts, themes

START = datetime(2026, 1, 1, tzinfo=timezone.utc)
HOUR = timedelta(hours=1)
FAMILIES = {"food", "home", "nature", "science", "arts", "body", "work",
            "play", "travel", "history-fantasy"}
SCENARIO_STARTS = ("a ", "an ", "things ", "when ", "how ", "why ", "what ")


def simulate(catalog, rounds, seed=1, **kwargs):
    """Pick `rounds` hourly themes; return [(when, theme)]."""
    rng = random.Random(seed)
    history = themes.History()
    out, now = [], START
    for _ in range(rounds):
        theme, _ = themes.choose(catalog, history, now, rng, **kwargs)
        out.append((now, theme))
        history.add(theme, now)
        now += HOUR
    return out


def fake_catalog(families=("food", "nature", "arts", "play"), per_category=30,
                 categories_per_family=3):
    out = []
    for family in families:
        for c in range(categories_per_family):
            category = f"{family}-{c}"
            for i in range(per_category):
                out.append(themes.Theme(f"{family} topic {c} {i}", category,
                                        category.title(), family,
                                        tuple(f"angle{j}" for j in range(8))))
    return out


class TestCatalog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = themes.load_catalog()

    def test_size(self):
        self.assertGreaterEqual(len(themes.dedupe(self.catalog)), 1800)
        self.assertGreaterEqual(len({t.category for t in self.catalog}), 60)

    def test_every_category_has_a_known_family_and_some_depth(self):
        counts = Counter(t.category for t in self.catalog)
        for theme in self.catalog:
            self.assertIn(theme.family, FAMILIES, theme.category)
            self.assertTrue(theme.category_name, theme.category)
        thin = {c: n for c, n in counts.items() if n < 10}
        self.assertEqual(thin, {}, "categories with under 10 topics")

    def test_every_topic_has_at_least_eight_distinct_angles(self):
        thin = []
        for t in self.catalog:
            angles = {themes.normalize(a) for a in t.angles}
            angles.discard(themes.normalize(t.text))
            if len(angles) < 8:
                thin.append(f"{t.text} ({t.category}): {len(angles)}")
        self.assertEqual(thin, [], "\n" + "\n".join(thin))

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

    def test_topic_format(self):
        bad = [t.text for t in self.catalog
               if not t.text or len(t.text) > 32 or t.text != t.text.strip()
               or t.text.endswith((".", "!", "?", ","))
               or themes.MASHUP_JOIN in t.text or "|" in t.text
               or t.text.lower().startswith(SCENARIO_STARTS)]
        self.assertEqual(bad, [])

    def test_worst_case_posts_fit(self):
        """The longest mashup, with a many-way tie of long handles and the
        leaderboard, still fits — and still names the new theme."""
        by_length = sorted(self.catalog, key=lambda t: -len(t.text))
        a = by_length[0]
        b = next(t for t in by_length if t.family != a.family)
        mashup = themes.make_mashup(a, b)
        handle = "a" * 60 + ".bsky.social"
        awards = [{"handle": f"{i}{handle}", "did": f"did:plc:{i}",
                   "likes": 999, "points": 60 / 7} for i in range(7)]
        leaders = [(f"did:plc:{i}", f"{i}{handle}", 99999.99, 9)
                   for i in range(3)]
        closes = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
        for theme, is_mashup in ((a.text, False), (mashup.text, True)):
            text, _ = posts.build_theme_post(
                theme, closes, {"theme": mashup.text, "awards": awards,
                                "entry_count": 50},
                leaders, posts.pick_hashtags(random.Random(0)),
                mashup=is_mashup)
            self.assertLessEqual(len(text), 300)
            self.assertIn(theme.upper(), text)
            self.assertIn("🏆", text)


class TestTwoYears(unittest.TestCase):
    """Two years of hourly rounds against the real catalog, mashups on."""

    @classmethod
    def setUpClass(cls):
        cls.catalog = themes.dedupe(themes.load_catalog())
        cls.picks = simulate(cls.catalog, 24 * 365 * 2, seed=7)

    def test_no_theme_within_ninety_days(self):
        last = {}
        floor = timedelta(days=config.MIN_THEME_REPEAT_DAYS)
        for when, theme in self.picks:
            key = themes.theme_key(theme)
            if key in last:
                self.assertGreaterEqual(when - last[key], floor, theme.text)
            last[key] = when

    def test_topics_are_spaced_in_every_form(self):
        last = {}
        spacing = timedelta(days=config.COMPONENT_SPACING_DAYS)
        for when, theme in self.picks:
            for key in themes.component_keys(theme):
                if key in last:
                    self.assertGreaterEqual(when - last[key], spacing,
                                            f"{key} in {theme.text}")
                last[key] = when

    def test_category_cooldown(self):
        cooldown = config.CATEGORY_COOLDOWN
        cats = [set(themes.categories_of(t)) for _, t in self.picks]
        for i, mine in enumerate(cats):
            window = set().union(*cats[max(0, i - cooldown):i])
            self.assertFalse(mine & window,
                             f"round {i}: {mine & window} within {cooldown}")

    def test_mashups_about_one_in_four_never_back_to_back(self):
        flags = [themes.is_mashup(t) for _, t in self.picks]
        share = sum(flags) / len(flags)
        self.assertGreater(share, 0.20)
        self.assertLess(share, 0.30)
        for i in range(1, len(flags)):
            self.assertFalse(flags[i] and flags[i - 1], f"round {i}")

    def test_mashups_join_different_families(self):
        for _, theme in self.picks:
            if themes.is_mashup(theme):
                a, b = theme.components
                self.assertNotEqual(a.family, b.family, theme.text)


class TestRules(unittest.TestCase):
    def test_mashup_key_is_order_independent(self):
        a = themes.Theme("Dentistry", "d", "D", "body")
        b = themes.Theme("Geology", "g", "G", "science")
        self.assertEqual(themes.theme_key(themes.make_mashup(a, b)),
                         themes.theme_key(themes.make_mashup(b, a)))
        self.assertEqual(themes.component_keys(themes.make_mashup(a, b)),
                         ("dentistry", "geology"))

    def test_small_catalog_fails_loudly_instead_of_repeating(self):
        tiny = fake_catalog(per_category=4, categories_per_family=1)
        with self.assertRaises(themes.NoThemeAvailable):
            simulate(tiny, 17, mashup_rate=0)

    def test_mashup_falls_back_to_solo_with_one_family(self):
        one_family = fake_catalog(families=("food",))
        picks = simulate(one_family, 60, mashup_rate=0.9)
        self.assertFalse(any(themes.is_mashup(t) for _, t in picks))

    def test_blocked_pairs_never_appear(self):
        catalog = fake_catalog(families=("food", "nature"), per_category=2,
                               categories_per_family=1)
        a, b = catalog[0], catalog[2]
        blocked = themes.theme_key(themes.make_mashup(a, b))
        seen = set()
        for seed in range(200):
            theme, _ = themes.choose(catalog, themes.History(), START,
                                     random.Random(seed), mashup_rate=1.0,
                                     blocked_pairs=frozenset({blocked}))
            self.assertTrue(themes.is_mashup(theme))
            seen.add(themes.theme_key(theme))
        self.assertNotIn(blocked, seen)
        self.assertEqual(len(seen), 3)      # every other pair still turns up

    def test_queue_goes_first_but_respects_the_floor(self):
        catalog = fake_catalog()
        rng = random.Random(0)
        queued = themes.Theme("Lighthouses", "custom", "Custom")
        history = themes.History()
        self.assertEqual(themes.choose(catalog, history, START, rng,
                                       queue=[queued]), (queued, 0))
        history.add(queued, START - timedelta(days=30))
        theme, index = themes.choose(catalog, history, START, rng,
                                     queue=[queued])
        self.assertIsNone(index)
        self.assertNotEqual(theme.text, "Lighthouses")

    def test_mashup_chance_hits_the_target_rate(self):
        self.assertAlmostEqual(themes.mashup_chance(0.25, 1), 1 / 3)
        self.assertEqual(themes.mashup_chance(0.25, 3), 1.0)
        self.assertEqual(themes.mashup_chance(0, 1), 0.0)

    def test_history_round_trips_through_uses(self):
        catalog = fake_catalog()
        picks = simulate(catalog, 50)
        uses = [themes.Use(themes.theme_key(t), t.category, when,
                           themes.component_keys(t)) for when, t in picks]
        rebuilt = themes.History.from_uses(uses)
        live = themes.History()
        for when, t in picks:
            live.add(t, when)
        self.assertEqual(rebuilt.last, live.last)
        self.assertEqual(rebuilt.last_topic, live.last_topic)
        self.assertEqual(rebuilt.recent, live.recent)
        self.assertEqual(rebuilt.since_mashup, live.since_mashup)

    def test_normalize_merges_near_duplicates(self):
        self.assertEqual(themes.normalize("Owls"), themes.normalize("owl!"))
        self.assertEqual(themes.normalize("The Beach"), themes.normalize("Beach"))
        self.assertEqual(themes.normalize("Salt & Pepper"),
                         themes.normalize("Salt and Pepper"))
        self.assertNotEqual(themes.normalize("Lunges"), themes.normalize("Lungs"))


class TestRepeatFloor(unittest.TestCase):
    def test_config_rejects_less_than_ninety_days(self):
        with self.assertRaises(ValueError):
            config.check_repeat_days(89)
        self.assertEqual(config.check_repeat_days(90), 90)


if __name__ == "__main__":
    unittest.main()
