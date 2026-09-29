"""Post copy. Pure functions: text in, text out, no network, no database.

Every builder returns text that fits Bluesky's 300-character limit on its
own — required lines first, optional lines only while they still fit, and
hashtags last — so bluesky.clamp() is a safety net that should never fire.
The order matters: build, fit to 300, *then* compute facets (UTF-8 bytes).
"""

from __future__ import annotations

import random
import re
from datetime import datetime

import config
from judging import fmt_points

LIMIT = 300


def plural(n, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def fmt_deadline(closes_local: datetime) -> str:
    """"4:00 PM PDT"."""
    return closes_local.strftime("%-I:%M %p %Z").strip()


_TAG_WORD = re.compile(r"[A-Za-z0-9]+")


def topic_hashtag(topic: str) -> str:
    """"Film Noir" -> "#FilmNoir", "The Post Office" -> "#PostOffice".

    Empty when the result would be useless: too short to mean anything, too
    long to be something anyone follows, or no letters at all.
    """
    words = _TAG_WORD.findall(topic)
    if len(words) > 1 and words[0].lower() == "the":
        words = words[1:]
    tag = "".join(w[:1].upper() + w[1:] for w in words)
    if not 3 <= len(tag) <= 24 or not re.search(r"[A-Za-z]", tag):
        return ""
    return "#" + tag


def pick_hashtags(rng=None, topics=(), families=()) -> list:
    """Hashtags for a theme post, highest priority first (see config).

    `topics` are the topic names (one, or two for a mashup) and `families`
    their families. No tag appears twice, compared case-insensitively.
    """
    rng = rng or random
    chosen, seen = [], set()

    def add(tag):
        if tag and tag.lower() not in seen and len(chosen) < config.MAX_HASHTAGS:
            chosen.append(tag)
            seen.add(tag.lower())

    add(config.HASHTAG_ALWAYS)
    if config.HASHTAG_CORE:
        add(rng.choice(config.HASHTAG_CORE))
    for topic in topics:
        add(topic_hashtag(topic))
    for family in dict.fromkeys(families):
        options = config.FAMILY_HASHTAGS.get(family, [])
        if options:
            add(rng.choice(options))
    for tag, chance in config.HASHTAG_BOOSTED.items():
        if rng.random() < chance:
            add(tag)
    pool = [t for t in config.HASHTAG_POOL if t.lower() not in seen]
    for tag in rng.sample(pool, len(pool)):
        add(tag)
    return chosen


def with_tags(text: str, tags: list) -> str:
    """Append hashtags in priority order, skipping any that would push the
    post past the limit (a shorter one further down may still fit)."""
    out, sep = text, "\n\n"
    for tag in tags:
        candidate = f"{out}{sep}{tag}"
        if len(candidate) > LIMIT:
            continue
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


def _split_lines(theme: str, handles: list, points: str) -> list:
    """Nobody got a like, so everyone who entered shares the pot."""
    pot = fmt_points(config.ROUND_POINTS)
    if len(handles) == 1:
        return [f"🏆 Last round ({theme}): no likes, so @{handles[0]} takes "
                f"all {pot} pts just for entering!",
                f"🏆 No likes last round, so @{handles[0]} takes all {pot} pts!",
                f"🏆 No likes last round, so the only punster takes {pot} pts!"]
    out = [f"🏆 Last round ({theme}): no likes, so {_names(handles, shown)} "
           f"split {pot} pts ({points} each)!"
           for shown in range(len(handles), 0, -1)]
    out.append(f"🏆 Last round ({theme}): no likes, so all {len(handles)} "
               f"punsters split {pot} pts!")
    out.append(f"🏆 No likes last round, so all {len(handles)} punsters "
               f"split {pot} pts!")
    return out


def result_lines(theme: str, awards: list, entry_count: int,
                 no_likes_split: bool = False) -> list:
    """Ways to announce last round's result, most detailed first.

    `awards` are rows (or dicts) with handle, likes and points. The caller
    takes the first one that fits alongside the new theme.
    `no_likes_split`: nobody got a like, so the awards are every entrant's
    equal share rather than a win.
    """
    if awards and no_likes_split:
        return _split_lines(theme, [a["handle"] for a in awards],
                            fmt_points(awards[0]["points"]))
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
                               previous.get("entry_count", 0),
                               previous.get("no_likes_split", False))
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
                       total: float, rank: int, players: int,
                       no_likes_split: bool = False) -> str:
    if no_likes_split and tied_with > 1:
        first = (f"🎉 Nobody's \u201c{theme}\u201d pun got a like, so "
                 f"everyone who entered splits the pot: +{fmt_points(points)} "
                 f"pts ({tied_with}-way split).")
    elif no_likes_split:
        first = (f"🎉 No likes on \u201c{theme}\u201d this round, but you "
                 f"were the only one to enter, so the pot is yours: "
                 f"+{fmt_points(points)} pts.")
    elif tied_with > 1:
        first = (f"🎉 You tied for the win on \u201c{theme}\u201d with "
                 f"{plural(likes, 'like')}! +{fmt_points(points)} pts "
                 f"({tied_with}-way split).")
    else:
        first = (f"🎉 Your pun won \u201c{theme}\u201d with "
                 f"{plural(likes, 'like')}! +{fmt_points(points)} pts.")
    second = (f"Your total: {fmt_points(total)} pts "
              f"(#{rank} of {plural(players, 'player')}).")
    text = f"{first}\n{second}"
    if config.REPLY_HASHTAG:
        text = with_tags(text, [config.REPLY_HASHTAG])
    return text[:LIMIT]


# ---------------------------------------------------------------------------
# The reply that closes a round
# ---------------------------------------------------------------------------

def _closing_results(awards: list, entry_count: int, no_likes_split: bool) -> list:
    """One-line results for the closing reply, most detailed first."""
    if not awards:
        return ["No puns this time." if not entry_count
                else "No winner this time."]
    handles = [a["handle"] for a in awards]
    likes = plural(awards[0]["likes"], "like")
    pot = fmt_points(config.ROUND_POINTS)
    if no_likes_split:
        if len(handles) == 1:
            return [f"No likes, so @{handles[0]} takes all {pot} pts.",
                    f"No likes, so the only punster takes {pot} pts."]
        return ([f"No likes, so {_names(handles, n)} split {pot} pts."
                 for n in range(len(handles), 0, -1)]
                + [f"No likes, so all {len(handles)} punsters split {pot} pts."])
    if len(handles) == 1:
        return [f"🏆 @{handles[0]} wins with {likes}!", "🏆 We have a winner!"]
    return ([f"🏆 {_names(handles, n)} tie at {likes}!"
             for n in range(len(handles), 0, -1)]
            + [f"🏆 {len(handles)}-way tie at {likes}!"])


def build_closing_reply(awards: list, entry_count: int, no_likes_split: bool,
                        next_theme: str, next_is_mashup: bool = False) -> tuple:
    """(text, extra_dids) for the reply on a finished round's theme post.

    It tells anyone arriving late that entries are closed, and — with the new
    round's post quoted underneath — where to play instead.
    """
    label = "MASHUP ROUND" if next_is_mashup else "New theme"
    tail = f"⏰ Time's up! This round is closed.\n{{result}}\n\n" \
           f"{label}: {next_theme.upper()} 👇"
    extra_dids = {a["handle"]: a["did"] for a in awards}
    for result in _closing_results(awards, entry_count, no_likes_split):
        text = tail.format(result=result)
        if len(text) <= LIMIT:
            return text, extra_dids
    return f"⏰ Time's up! This round is closed.\n\n{label} 👇", extra_dids
