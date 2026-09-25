"""The hourly tick as a state machine: each step replays cleanly after a
failure, and nothing is ever announced, scored or replied to twice."""

import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from helpers import BOT_DID, DbTestCase, bluesky, db, entry, main

# 3:00 PM PDT on a Tuesday — on the hour.
T0 = datetime(2026, 9, 22, 22, 0, tzinfo=timezone.utc)
HOUR = timedelta(hours=1)


class TickTestCase(DbTestCase):
    def setUp(self):
        super().setUp()
        self.entries = {}          # theme post uri -> [Entry]
        self.likers = {}           # entry uri -> set of DIDs
        self.posted = []           # (kind, text)
        self.replies = []          # (text, parent_uri, root_uri)
        real_post, real_reply = bluesky.post_text, bluesky.post_reply

        def post_text(text, kind="theme", extra_dids=None):
            self.posted.append((kind, text))
            return real_post(text, kind, extra_dids)

        def post_reply(text, parent_uri, parent_cid, root_uri="", root_cid="",
                       kind="winner", extra_dids=None):
            self.replies.append((text, parent_uri, root_uri))
            return real_reply(text, parent_uri, parent_cid, root_uri, root_cid,
                              kind, extra_dids)

        patches = [
            mock.patch.object(bluesky, "post_text", side_effect=post_text),
            mock.patch.object(bluesky, "post_reply", side_effect=post_reply),
            mock.patch.object(bluesky, "get_entries",
                              side_effect=lambda uri: list(self.entries.get(uri, []))),
            mock.patch.object(bluesky, "get_likers",
                              side_effect=lambda uri: set(self.likers.get(uri, ()))),
            mock.patch.object(main.time, "sleep"),
        ]
        self.mocks = [p.start() for p in patches]
        for p in patches:
            self.addCleanup(p.stop)

    def add_entry(self, round_row, did, likes, minutes_in=10):
        at = (db.from_iso(round_row["opened_at"]) + timedelta(minutes=minutes_in))
        stamp = at.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        e = entry(did, likes, at=stamp)
        self.entries.setdefault(round_row["post_uri"], []).append(e)
        self.likers[e.uri] = {f"did:plc:fan{i}" for i in range(likes)}
        return e


class TestTick(TickTestCase):
    def test_first_tick_opens_a_round_on_the_hour(self):
        main.run_tick(T0)
        current = db.current_round()
        self.assertEqual(current["status"], db.OPEN)
        self.assertEqual(db.from_iso(current["closes_at"]), T0 + HOUR)
        self.assertEqual(len(self.posted), 1)
        self.assertIn("Welcome to Pun Battle", self.posted[0][1])
        self.assertIn("4:00 PM PDT", self.posted[0][1])

    def test_mid_round_tick_does_nothing(self):
        main.run_tick(T0)
        main.run_tick(T0 + timedelta(minutes=40))     # a restart at 3:40
        self.assertEqual(len(self.posted), 1)
        self.assertEqual(db.round_count(), 1)

    def test_full_round(self):
        main.run_tick(T0)
        first = db.current_round()
        winner = self.add_entry(first, "did:plc:alice", 7)
        self.add_entry(first, "did:plc:bob", 3)

        main.run_tick(T0 + HOUR)

        rounds = [db.get_round(i) for i in (1, 2)]
        self.assertEqual(rounds[0]["status"], db.ANNOUNCED)
        self.assertEqual(rounds[1]["status"], db.OPEN)
        self.assertIn("@alice.bsky.social wins 60 pts with 7 likes",
                      self.posted[-1][1])
        self.assertEqual(db.player_total("did:plc:alice"), 60)

        self.assertEqual(len(self.replies), 1)
        text, parent, root = self.replies[0]
        self.assertEqual(parent, winner.uri)
        self.assertEqual(root, first["post_uri"])
        self.assertIn("+60 pts", text)
        self.assertIn("Your total: 60 pts (#1 of 2 players)", text)

    def test_a_failed_theme_post_replays_without_rejudging(self):
        main.run_tick(T0)
        first = db.current_round()
        self.add_entry(first, "did:plc:alice", 2)

        with mock.patch.object(bluesky, "post_text",
                               side_effect=RuntimeError("network down")):
            with self.assertRaises(RuntimeError):
                main.run_tick(T0 + HOUR)
        self.assertEqual(db.get_round(first["id"])["status"], db.JUDGED)
        self.assertEqual(db.round_count(), 1)
        self.assertEqual(self.replies, [])      # no reply before the announcement

        get_entries = self.mocks[2]
        calls_before = get_entries.call_count
        main.run_tick(T0 + 2 * HOUR)
        self.assertEqual(get_entries.call_count, calls_before)   # not re-judged
        self.assertEqual(db.get_round(first["id"])["status"], db.ANNOUNCED)
        self.assertEqual(db.round_count(), 2)
        self.assertEqual(db.player_total("did:plc:alice"), 60)   # scored once
        self.assertEqual(len(self.replies), 1)

    def test_failed_winner_reply_is_retried_then_abandoned(self):
        main.run_tick(T0)
        self.add_entry(db.current_round(), "did:plc:alice", 2)
        with mock.patch.object(bluesky, "post_reply",
                               side_effect=RuntimeError("pun deleted")):
            main.run_tick(T0 + HOUR)           # announced, reply fails (1)
            main.run_tick(T0 + HOUR + timedelta(minutes=20))   # retry (2)
            main.run_tick(T0 + HOUR + timedelta(minutes=40))   # retry (3)
        self.assertEqual(db.pending_replies(), [])
        # The round itself went through; only the courtesy reply is lost.
        self.assertEqual(db.player_total("did:plc:alice"), 60)

    def test_no_entries(self):
        main.run_tick(T0)
        main.run_tick(T0 + HOUR)
        self.assertIn("No puns for", self.posted[-1][1])
        self.assertEqual(self.replies, [])

    def test_late_entry_is_ignored(self):
        main.run_tick(T0)
        first = db.current_round()
        self.add_entry(first, "did:plc:early", 1, minutes_in=59)
        self.add_entry(first, "did:plc:late", 9, minutes_in=61)
        main.run_tick(T0 + HOUR)
        self.assertEqual(db.player_total("did:plc:early"), 60)
        self.assertEqual(db.player_total("did:plc:late"), 0)

    def test_bot_replies_are_not_entries(self):
        main.run_tick(T0)
        self.add_entry(db.current_round(), BOT_DID, 9)
        main.run_tick(T0 + HOUR)
        self.assertEqual(db.leaderboard(), [])

    def test_themes_do_not_repeat_across_ticks(self):
        now = T0
        for _ in range(30):
            main.run_tick(now)
            now += HOUR
        history = sorted(db.theme_history(), key=lambda h: h[2])
        self.assertEqual(len({h[0] for h in history}), len(history))
        topics = [key for h in history for key in h[3]]
        self.assertEqual(len(topics), len(set(topics)))
        categories = [set(h[1].split("|")) for h in history]
        for i in range(1, len(categories)):
            recent = set().union(*categories[max(0, i - 24):i])
            self.assertFalse(categories[i] & recent)

    def test_queue_goes_next(self):
        db.queue_theme("Lighthouses")
        main.run_tick(T0)
        self.assertEqual(db.current_round()["theme"], "Lighthouses")
        self.assertEqual(db.queued_themes(), [])

    def test_queued_mashup(self):
        db.queue_theme("Coffee + Lighthouses")
        main.run_tick(T0)
        current = db.current_round()
        self.assertEqual(current["theme"], "Coffee + Lighthouses")
        self.assertTrue(current["theme_key"].startswith("mashup:"))
        self.assertEqual(current["components"], "coffee|lighthouse")
        self.assertIn("⚔️ MASHUP ROUND: COFFEE + LIGHTHOUSES", self.posted[-1][1])
        self.assertIn("One pun, both topics", self.posted[-1][1])

    def test_skip_keeps_the_boundary_and_scores_nothing(self):
        main.run_tick(T0)
        first = db.current_round()
        self.add_entry(first, "did:plc:alice", 5)
        main.skip_current(T0 + timedelta(minutes=10))
        self.assertEqual(db.get_round(first["id"])["status"], db.SKIPPED)
        replacement = db.current_round()
        self.assertEqual(replacement["closes_at"], first["closes_at"])
        self.assertNotIn("🏆", self.posted[-1][1])
        main.run_tick(T0 + HOUR)
        self.assertEqual(db.player_total("did:plc:alice"), 0)

    def test_leaderboard_appears_at_noon_only(self):
        noon = datetime(2026, 9, 22, 19, 0, tzinfo=timezone.utc)   # 12 PM PDT
        main.run_tick(noon - 2 * HOUR)
        self.add_entry(db.current_round(), "did:plc:alice", 3)
        main.run_tick(noon - HOUR)
        self.assertNotIn("All-time", self.posted[-1][1])
        main.run_tick(noon)
        self.assertIn("📊 All-time: 1. @alice.bsky.social 60", self.posted[-1][1])


if __name__ == "__main__":
    unittest.main()
