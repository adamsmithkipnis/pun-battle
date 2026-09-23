"""Post copy: always under 300 characters, and facets on the right bytes."""

import random
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from helpers import bluesky, posts

CLOSES = datetime(2026, 9, 22, 16, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
LONG = "a" * 50 + ".bsky.social"


def award(handle, did, likes=12, points=60):
    return {"handle": handle, "did": did, "likes": likes, "points": points}


class TestThemePost(unittest.TestCase):
    def test_single_winner(self):
        text, dids = posts.build_theme_post(
            "Lighthouses", CLOSES,
            {"theme": "Cheese", "awards": [award("alice.bsky.social", "did:plc:a")],
             "entry_count": 9},
            tags=["#PunBattle"])
        self.assertIn("🏆 Last round (Cheese): @alice.bsky.social wins 60 pts "
                      "with 12 likes!", text)
        self.assertIn("New pun theme: LIGHTHOUSES", text)
        self.assertIn("Most likes at 4:00 PM PDT wins 60 pts", text)
        self.assertTrue(text.endswith("#PunBattle"))
        self.assertEqual(dids, {"alice.bsky.social": "did:plc:a"})

    def test_tie_names_everyone_and_the_share(self):
        awards = [award(f"p{i}.bsky.social", f"did:plc:{i}", 4, 20)
                  for i in range(3)]
        text, _ = posts.build_theme_post(
            "Owls", CLOSES, {"theme": "Cheese", "awards": awards,
                             "entry_count": 9})
        self.assertIn("@p0.bsky.social, @p1.bsky.social & @p2.bsky.social "
                      "tied at 4 likes, 20 pts each!", text)

    def test_long_tie_is_shortened_not_dropped(self):
        awards = [award(f"{i}{LONG}", f"did:plc:{i}", 4, 60 / 7)
                  for i in range(7)]
        text, _ = posts.build_theme_post(
            "Owls", CLOSES, {"theme": "Cheese", "awards": awards,
                             "entry_count": 30},
            tags=posts.pick_hashtags(random.Random(1)))
        self.assertLessEqual(len(text), 300)
        self.assertIn("more tied at 4 likes, 8.57 pts each", text)

    def test_no_entries_and_no_likes(self):
        text, _ = posts.build_theme_post(
            "Owls", CLOSES, {"theme": "Cheese", "awards": [], "entry_count": 0})
        self.assertIn("No puns for Cheese last round", text)
        text, _ = posts.build_theme_post(
            "Owls", CLOSES, {"theme": "Cheese", "awards": [], "entry_count": 4})
        self.assertIn("so no winner", text)

    def test_first_ever_post_welcomes(self):
        text, _ = posts.build_theme_post("Owls", CLOSES)
        self.assertIn("Welcome to Pun Battle", text)

    def test_leaderboard_when_it_fits(self):
        leaders = [("did:plc:a", "alice.bsky.social", 480, 8),
                   ("did:plc:b", "bob.bsky.social", 300, 5),
                   ("did:plc:c", "cy.bsky.social", 240, 4)]
        text, dids = posts.build_theme_post(
            "Owls", CLOSES, {"theme": "Cheese", "awards": [], "entry_count": 2},
            leaders)
        self.assertIn("📊 All-time: 1. @alice.bsky.social 480 · "
                      "2. @bob.bsky.social 300 · 3. @cy.bsky.social 240", text)
        self.assertEqual(dids["cy.bsky.social"], "did:plc:c")

    def test_hashtags_never_push_past_the_limit(self):
        many = [f"#tag{i}" for i in range(80)]
        text, _ = posts.build_theme_post(
            "Owls", CLOSES, {"theme": "Cheese",
                             "awards": [award(LONG, "did:plc:a")],
                             "entry_count": 2}, tags=many)
        self.assertLessEqual(len(text), 300)


class TestWinnerReply(unittest.TestCase):
    def test_solo(self):
        text = posts.build_winner_reply("Cheese", 14, 60, 1, 180, 2, 37)
        self.assertEqual(
            text, "🎉 Your pun won “Cheese” with 14 likes! +60 pts.\n"
                  "Your total: 180 pts (#2 of 37 players).\n\n#PunBattle")

    def test_tie_and_singulars(self):
        text = posts.build_winner_reply("Cheese", 1, 20, 3, 20, 1, 1)
        self.assertIn("tied for the win", text)
        self.assertIn("with 1 like!", text)
        self.assertIn("(3-way split)", text)
        self.assertIn("#1 of 1 player)", text)


class TestFacets(unittest.TestCase):
    def test_mentions_and_tags_land_on_the_right_bytes(self):
        text, dids = posts.build_theme_post(
            "Cheese", CLOSES,
            {"theme": "Brie", "awards": [award("alice.bsky.social", "did:plc:a")],
             "entry_count": 3}, tags=["#PunBattle", "#puns"])
        encoded = text.encode("utf-8")
        found = {(kind, encoded[s:e].decode()) for kind, _, s, e
                 in bluesky.facet_ranges(text, dids)}
        self.assertIn(("mention", "@alice.bsky.social"), found)
        self.assertIn(("tag", "#PunBattle"), found)
        self.assertIn(("tag", "#puns"), found)

    def test_rank_is_not_a_hashtag(self):
        text = posts.build_winner_reply("Cheese", 3, 60, 1, 60, 2, 9)
        tags = [v for kind, v, _, _ in bluesky.facet_ranges(text) if kind == "tag"]
        self.assertEqual(tags, ["PunBattle"])


if __name__ == "__main__":
    unittest.main()
