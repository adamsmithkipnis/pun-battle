"""Who won a round, and how the points split.

No network and no atproto: bluesky.py turns replies into Entry records and
hands over a function for fetching a post's likers, so every rule here is
testable with plain data.

The rules, as announced:

* An entry is a direct reply to the theme post, made before the round closed.
  Replies from the bot itself are not entries.
* Likes are counted without the author's own like and without the bot's.
* A player is ranked by their best entry. Posting five puns does not give you
  five chances to tie with yourself.
* Most likes wins ROUND_POINTS. A tie splits them evenly between the tied
  players.
* Below MIN_LIKES there is no winner — otherwise a round nobody liked would
  pay every entrant as an n-way tie at zero.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone


@dataclass
class Entry:
    did: str
    handle: str
    text: str
    uri: str
    cid: str
    created_at: str          # the author's claimed timestamp
    indexed_at: str = ""     # when Bluesky's AppView saw it — can't be faked
    like_count: int = 0      # Bluesky's count: includes self-likes, may lag
    likes: int | None = None  # exact count after exclusions, once verified


@dataclass
class Award:
    did: str
    handle: str
    entry: Entry
    likes: int
    points: float


@dataclass
class Result:
    winners: list = field(default_factory=list)   # [Award]
    top_likes: int = 0
    entry_count: int = 0
    player_count: int = 0


_TS = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?"
    r"(Z|[+-]\d{2}:?\d{2})?$")


def parse_time(value: str) -> datetime | None:
    """ISO 8601 as Bluesky writes it, into an aware UTC datetime.

    Python 3.9's fromisoformat rejects both a trailing 'Z' and fractions that
    are not exactly 3 or 6 digits, and Bluesky clients emit both. None for
    anything unparseable.
    """
    match = _TS.match((value or "").strip())
    if not match:
        return None
    y, mo, d, h, mi, s, frac, zone = match.groups()
    micro = int((frac or "0")[:6].ljust(6, "0"))
    dt = datetime(int(y), int(mo), int(d), int(h), int(mi), int(s), micro,
                  tzinfo=timezone.utc)
    if zone and zone != "Z":
        sign = 1 if zone[0] == "+" else -1
        digits = zone[1:].replace(":", "")
        offset = sign * (int(digits[:2]) * 60 + int(digits[2:]))
        dt -= timedelta(minutes=offset)
    return dt


def entry_time(entry: Entry) -> datetime | None:
    """When the entry really arrived.

    indexed_at comes from Bluesky's servers; created_at is whatever the
    client wrote and can be backdated. Prefer the one that can't be faked.
    """
    return parse_time(entry.indexed_at) or parse_time(entry.created_at)


def valid_entries(entries: list, bot_did: str, closes_at: datetime) -> list:
    out = []
    for entry in entries:
        if not entry.did or entry.did == bot_did:
            continue
        if not entry.text.strip():
            continue
        when = entry_time(entry)
        if when is not None and when >= closes_at:
            continue
        out.append(entry)
    return out


def resolve_likes(entries: list, fetch_likers, bot_did: str,
                  min_likes: int = 1) -> int:
    """Fill in exact `likes` for every entry that could still win.

    Bluesky's like_count is an upper bound on the exact count (it includes
    self-likes and the bot's), so entries are checked from the top down and
    the walk stops as soon as the next upper bound is below the best exact
    count found so far — nothing below that line can win or tie. That keeps a
    busy round to a handful of getLikes calls instead of one per reply.

    Returns the number of likers fetches made.
    """
    best, calls = -1, 0
    for entry in sorted(entries, key=lambda e: e.like_count, reverse=True):
        # Below the best found so far, or below the minimum to win at all:
        # neither this entry nor anything after it can matter.
        if entry.like_count < max(best, min_likes):
            break
        likers = set(fetch_likers(entry.uri))
        calls += 1
        likers.discard(entry.did)
        likers.discard(bot_did)
        entry.likes = len(likers)
        best = max(best, entry.likes)
    return calls


def _earlier(a: Entry, b: Entry) -> bool:
    ta, tb = entry_time(a), entry_time(b)
    if ta is None or tb is None:
        return False
    return ta < tb


def score_round(entries: list, points: float, min_likes: int = 1) -> Result:
    """Winners and their shares. Entries must already have `likes` resolved;
    unresolved entries (likes None) are ones that provably cannot win."""
    result = Result(entry_count=len(entries),
                    player_count=len({e.did for e in entries}))

    best_by_player = {}
    for entry in entries:
        if entry.likes is None:
            continue
        current = best_by_player.get(entry.did)
        if (current is None or entry.likes > current.likes
                or (entry.likes == current.likes and _earlier(entry, current))):
            best_by_player[entry.did] = entry

    if not best_by_player:
        return result
    top = max(e.likes for e in best_by_player.values())
    result.top_likes = top
    if top < min_likes:
        return result

    tied = [e for e in best_by_player.values() if e.likes == top]
    tied.sort(key=lambda e: (entry_time(e) or datetime.max.replace(
        tzinfo=timezone.utc)))
    share = points / len(tied)
    result.winners = [Award(e.did, e.handle, e, top, share) for e in tied]
    return result


def fmt_points(value: float) -> str:
    """60 -> "60", 30.0 -> "30", 8.571428 -> "8.57"."""
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.2f}".rstrip("0").rstrip(".")
