"""Running totals, ranks and the leaderboard, across rounds."""

import unittest
from datetime import datetime, timedelta, timezone

from helpers import DbTestCase, db, entry, judging

T0 = datetime(2026, 9, 22, 15, 0, tzinfo=timezone.utc)


def play_round(n, entries, winners_likes):
    """Record a judged round where each (did, likes) in winners_likes won."""
    rid = db.open_round(f"Theme {n}", f"theme {n}", "cat", "Cat",
                        f"at://bot/{n}", "cid", T0 + timedelta(hours=n),
                        T0 + timedelta(hours=n + 1))
    for e in entries:
        e.likes = e.like_count
    result = judging.score_round(entries, 60)
    db.save_judgement(rid, entries, result)
    return rid, result


class TestScores(DbTestCase):
    def test_totals_and_competition_ranking(self):
        a, b, c = "did:plc:a", "did:plc:b", "did:plc:c"
        play_round(1, [entry(a, 5), entry(b, 3), entry(c, 1)], None)   # a +60
        play_round(2, [entry(a, 4), entry(b, 4)], None)                # a,b +30
        play_round(3, [entry(b, 9), entry(c, 2)], None)                # b +60

        self.assertEqual(db.player_total(a), 90)
        self.assertEqual(db.player_total(b), 90)
        self.assertEqual(db.player_total(c), 0)
        self.assertEqual(db.player_rank(a), (1, 3))
        self.assertEqual(db.player_rank(b), (1, 3))
        # Never won, but played — ranked last of everyone who entered.
        self.assertEqual(db.player_rank(c), (3, 3))

        board = db.leaderboard(5)
        self.assertEqual([row[2] for row in board], [90, 90])
        self.assertEqual({row[0] for row in board}, {a, b})

    def test_fractional_shares_add_up(self):
        dids = [f"did:plc:{i}" for i in range(7)]
        play_round(1, [entry(d, 2) for d in dids], None)
        total = sum(db.player_total(d) for d in dids)
        self.assertAlmostEqual(total, 60)
        self.assertEqual(judging.fmt_points(db.player_total(dids[0])), "8.57")

    def test_leaderboard_uses_latest_handle(self):
        a = "did:plc:a"
        play_round(1, [entry(a, 3, handle="old.bsky.social")], None)
        play_round(2, [entry(a, 3, handle="new.bsky.social")], None)
        self.assertEqual(db.leaderboard(1)[0][1], "new.bsky.social")


if __name__ == "__main__":
    unittest.main()
