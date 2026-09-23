"""The rules for who wins a round and how the points split."""

import unittest
from datetime import datetime, timezone

from helpers import BOT_DID, entry, judging

CLOSE = datetime(2026, 9, 22, 16, 0, tzinfo=timezone.utc)


def likers_from(table):
    """A fetch_likers stand-in: uri -> set of DIDs, counting calls."""
    calls = []

    def fetch(uri):
        calls.append(uri)
        return set(table.get(uri, ()))
    fetch.calls = calls
    return fetch


def run(entries, likers, points=60, min_likes=1):
    fetch = likers_from(likers)
    judging.resolve_likes(entries, fetch, BOT_DID, min_likes)
    return judging.score_round(entries, points, min_likes), fetch


def fans(n, prefix="fan"):
    return {f"did:plc:{prefix}{i}" for i in range(n)}


class TestWinners(unittest.TestCase):
    def test_single_winner_takes_all(self):
        a, b = entry("did:plc:a", 5), entry("did:plc:b", 3)
        result, _ = run([a, b], {a.uri: fans(5), b.uri: fans(3)})
        self.assertEqual([w.did for w in result.winners], ["did:plc:a"])
        self.assertEqual(result.winners[0].points, 60)
        self.assertEqual(result.winners[0].likes, 5)

    def test_ties_split_evenly_and_sum_to_the_round(self):
        for n in (2, 3, 7):
            with self.subTest(tied=n):
                es = [entry(f"did:plc:p{i}", 4) for i in range(n)]
                result, _ = run(es, {e.uri: fans(4) for e in es})
                self.assertEqual(len(result.winners), n)
                self.assertAlmostEqual(sum(w.points for w in result.winners), 60)
                for w in result.winners:
                    self.assertAlmostEqual(w.points, 60 / n)

    def test_self_like_and_bot_like_do_not_count(self):
        # a's raw count of 4 includes its author and the bot; only 2 are real.
        a = entry("did:plc:a", 4)
        b = entry("did:plc:b", 3)
        result, _ = run([a, b], {
            a.uri: fans(2) | {"did:plc:a", BOT_DID},
            b.uri: fans(3),
        })
        self.assertEqual([w.did for w in result.winners], ["did:plc:b"])
        self.assertEqual(a.likes, 2)

    def test_player_cannot_tie_with_themselves(self):
        first = entry("did:plc:a", 3, at="2026-09-22T15:05:00.000Z")
        second = entry("did:plc:a", 3, at="2026-09-22T15:20:00.000Z")
        other = entry("did:plc:b", 1)
        result, _ = run([first, second, other], {
            first.uri: fans(3), second.uri: fans(3), other.uri: fans(1)})
        self.assertEqual(len(result.winners), 1)
        self.assertEqual(result.winners[0].points, 60)
        # The earlier of their two equal puns is the one credited.
        self.assertEqual(result.winners[0].entry.uri, first.uri)
        self.assertEqual(result.player_count, 2)
        self.assertEqual(result.entry_count, 3)

    def test_best_entry_counts_for_a_multi_entry_player(self):
        weak = entry("did:plc:a", 1, at="2026-09-22T15:01:00.000Z")
        strong = entry("did:plc:a", 6, at="2026-09-22T15:02:00.000Z")
        rival = entry("did:plc:b", 5)
        result, _ = run([weak, strong, rival], {
            weak.uri: fans(1), strong.uri: fans(6), rival.uri: fans(5)})
        self.assertEqual(result.winners[0].entry.uri, strong.uri)

    def test_no_winner_below_min_likes(self):
        es = [entry("did:plc:a", 0), entry("did:plc:b", 0)]
        result, fetch = run(es, {})
        self.assertEqual(result.winners, [])
        self.assertEqual(fetch.calls, [])      # nothing worth checking

    def test_only_self_likes_is_no_winner(self):
        a = entry("did:plc:a", 1)
        result, _ = run([a], {a.uri: {"did:plc:a"}})
        self.assertEqual(result.winners, [])
        self.assertEqual(result.top_likes, 0)

    def test_higher_min_likes(self):
        a = entry("did:plc:a", 2)
        result, _ = run([a], {a.uri: fans(2)}, min_likes=3)
        self.assertEqual(result.winners, [])

    def test_fractional_points_display(self):
        self.assertEqual(judging.fmt_points(60), "60")
        self.assertEqual(judging.fmt_points(30.0), "30")
        self.assertEqual(judging.fmt_points(60 / 7), "8.57")
        self.assertEqual(judging.fmt_points(7.5), "7.5")


class TestLikeChecks(unittest.TestCase):
    def test_stops_once_nothing_below_can_win(self):
        es = [entry(f"did:plc:p{i}", n) for i, n in enumerate([9, 7, 5, 3, 1])]
        table = {e.uri: fans(e.like_count) for e in es}
        _, fetch = run(es, table)
        # 9 exact beats every lower upper bound: one call, not five.
        self.assertEqual(len(fetch.calls), 1)

    def test_keeps_checking_when_self_likes_inflate_the_leader(self):
        top = entry("did:plc:a", 5)
        runner = entry("did:plc:b", 4)
        _, fetch = run([top, runner], {
            top.uri: fans(3) | {"did:plc:a"}, runner.uri: fans(4)})
        self.assertEqual(len(fetch.calls), 2)
        self.assertEqual(runner.likes, 4)


class TestValidEntries(unittest.TestCase):
    def test_filters(self):
        ok = entry("did:plc:a", 1)
        bot = entry(BOT_DID, 9)
        late = entry("did:plc:b", 9, at="2026-09-22T16:00:01.000Z")
        empty = entry("did:plc:c", 9, text="   ")
        kept = judging.valid_entries([ok, bot, late, empty], BOT_DID, CLOSE)
        self.assertEqual(kept, [ok])

    def test_backdated_created_at_does_not_sneak_in(self):
        sneaky = entry("did:plc:a", 9, at="2026-09-22T15:00:00.000Z")
        sneaky.indexed_at = "2026-09-22T16:02:00.000Z"
        self.assertEqual(judging.valid_entries([sneaky], BOT_DID, CLOSE), [])

    def test_parse_time_formats(self):
        expected = datetime(2026, 9, 22, 15, 10, 0, 123000, tzinfo=timezone.utc)
        for raw in ("2026-09-22T15:10:00.123Z", "2026-09-22T15:10:00.123000Z",
                    "2026-09-22T15:10:00.123+00:00",
                    "2026-09-22T08:10:00.123-07:00"):
            with self.subTest(raw=raw):
                self.assertEqual(judging.parse_time(raw), expected)
        self.assertIsNone(judging.parse_time("yesterday"))


if __name__ == "__main__":
    unittest.main()
