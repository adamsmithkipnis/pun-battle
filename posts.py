"""Post copy. Pure functions: text in, text out, no network, no database.

Every builder returns text that fits Bluesky's 300-character limit on its
own — required lines first, optional lines only while they still fit, and
hashtags last — so bluesky.clamp() is a safety net that should never fire.
The order matters: build, fit to 300, *then* compute facets (UTF-8 bytes).
"""

from __future__ import annotations

import random
from datetime import datetime

import config
from judging import fmt_points

LIMIT = 300


def plural(n, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def fmt_deadline(closes_local: datetime) -> str:
    """"4:00 PM PDT"."""
    return closes_local.strftime("%-I:%M %p %Z").strip()


def pick_hashtags(rng=None) -> list:
    rng = rng or random
    pool = [t for t in config.HASHTAG_POOL
            if t.lower() != config.HASHTAG_ALWAYS.lower()]
    count = max(0, min(config.HASHTAG_COUNT, len(pool)))
    tags = [config.HASHTAG_ALWAYS] if config.HASHTAG_ALWAYS else []
    return tags + rng.sample(pool, count)


def with_tags(text: str, tags: list) -> str:
    """Append hashtags one at a time, only while the post still fits."""
    out, sep = text, "\n\n"
    for tag in tags:
        candidate = f"{out}{sep}{tag}"
        if len(candidate) > LIMIT:
            break
        out, sep = candidate, " "
    return out


# ---------------------------------------------------------------------------
# The hourly theme post
# ---------------------------------------------------------------------------

def _names(handles: list, shown: int) -> str:
    mentions = [f"@{h}" for h in handles[:shown]]
    rest = len(handles) - len(mentions)
    if rest > 0:
        return ", ".join(mentions) + f" +{rest} more"
    if len(mentions) == 1:
        return mentions[0]
    return ", ".join(mentions[:-1]) + " & " + mentions[-1]


def result_lines(theme: str, awards: list, entry_count: int) -> list:
    """Ways to announce last round's result, most detailed first.

    `awards` are rows (or dicts) with handle, likes and points. The caller
    takes the first one that fits alongside the new theme.
    """
    if not awards:
        if not entry_count:
            return [f"No puns for {theme} last round. Fresh start:",
                    "No puns last round. Fresh start:"]
        return [f"Nobody's {theme} pun got a like last round, so no winner.",
                "No likes last round, so no winner."]

    likes = awards[0]["likes"]
    points = fmt_points(awards[0]["points"])
    handles = [a["handle"] for a in awards]
    if len(awards) == 1:
        return [f"🏆 Last round ({theme}): @{handles[0]} wins {points} pts "
                f"with {plural(likes, 'like')}!",
                f"🏆 @{handles[0]} won last round with "
                f"{plural(likes, 'like')}!",
                "🏆 Last round has a winner! Check the replies."]

    out = []
    for shown in range(len(handles), 0, -1):
        out.append(f"🏆 Last round ({theme}): {_names(handles, shown)} tied at "
                   f"{plural(likes, 'like')}, {points} pts each!")
    out.append(f"🏆 Last round ({theme}): {len(handles)} players tied at "
               f"{plural(likes, 'like')}, {points} pts each!")
    out.append(f"🏆 {len(handles)} players tied last round, {points} pts each!")
    return out


def leaderboard_line(leaders: list) -> list:
    """Standings, most detailed first. leaders: [(did, handle, total, wins)]."""
    if not leaders:
        return []
    out = []
    for shown in range(min(3, len(leaders)), 0, -1):
        parts = [f"{i}. @{h} {fmt_points(t)}"
                 for i, (_, h, t, _) in enumerate(leaders[:shown], 1)]
        out.append("📊 All-time: " + " · ".join(parts))
    return out


def build_theme_post(theme: str, closes_local: datetime,
                     previous: dict | None = None,
                     leaders: list | None = None,
                     tags: list | None = None,
                     mashup: bool = False) -> tuple:
    """(text, extra_dids) for the post that opens a round.

    `previous`: {"theme", "awards", "entry_count"} for the round being
    announced, or None for the very first round (or after a --skip).
    `mashup`: the theme is two topics ("DENTISTRY + GEOLOGY") and one pun
    has to cover both.
    """
    deadline = (f"Most likes at {fmt_deadline(closes_local)} wins "
                f"{fmt_points(config.ROUND_POINTS)} pts (ties split).")
    if mashup:
        core = (f"⚔️ MASHUP ROUND: {theme.upper()}\n"
                f"One pun, both topics. {deadline}")
    else:
        core = (f"🎭 New pun theme: {theme.upper()}\n"
                f"Reply with your best pun. {deadline}")

    extra_dids = {}
    head = []
    if previous is not None:
        options = result_lines(previous["theme"], previous["awards"],
                               previous.get("entry_count", 0))
        for line in options:
            if len("\n\n".join([line, core])) <= LIMIT:
                head.append(line)
                break
        for award in previous["awards"]:
            extra_dids[award["handle"]] = award["did"]
    else:
        welcome = "Welcome to Pun Battle! A new theme every hour."
        if len(welcome) + 2 + len(core) <= LIMIT:
            head.append(welcome)

    text = "\n\n".join(head + [core])

    for line in leaderboard_line(leaders or []):
        candidate = f"{text}\n\n{line}"
        if len(candidate) <= LIMIT:
            text = candidate
            for did, handle, _, _ in (leaders or [])[:3]:
                extra_dids[handle] = did
            break

    return with_tags(text, tags or []), extra_dids


# ---------------------------------------------------------------------------
# The personal reply to a winner
# ---------------------------------------------------------------------------

def build_winner_reply(theme: str, likes: int, points: float, tied_with: int,
                       total: float, rank: int, players: int) -> str:
    if tied_with > 1:
        first = (f"🎉 You tied for the win on “{theme}” with "
                 f"{plural(likes, 'like')}! +{fmt_points(points)} pts "
                 f"({tied_with}-way split).")
    else:
        first = (f"🎉 Your pun won “{theme}” with "
                 f"{plural(likes, 'like')}! +{fmt_points(points)} pts.")
    second = (f"Your total: {fmt_points(total)} pts "
              f"(#{rank} of {plural(players, 'player')}).")
    text = f"{first}\n{second}"
    if config.REPLY_HASHTAG:
        text = with_tags(text, [config.REPLY_HASHTAG])
    return text[:LIMIT]
