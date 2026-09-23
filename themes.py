"""The theme catalog and the rule for choosing the next theme.

Pure logic, no network and no database: the caller passes in the history, so
every rule here can be exercised by simulating years of rounds in a test.

The catalog is plain text — `themes/<category>.txt`, one theme per line, with
a `# Display Name` comment on top — so anybody (or OpenClaw) can add themes by
editing a file and pushing.

Choosing a theme, in order of precedence:

1. **Never within 90 days.** A theme whose last use is inside
   config.MIN_THEME_REPEAT_DAYS is never eligible — not from the catalog, not
   from the manual queue, not when the catalog runs low. If nothing is left
   the pick fails loudly rather than bending the rule.
2. **Fresh first.** Themes never used before, then themes last used more than
   THEME_REPEAT_DAYS ago, then (only if both are exhausted) anything past the
   90-day floor.
3. **Spread the categories.** No category used in the last CATEGORY_COOLDOWN
   rounds, relaxed step by step only if that leaves nothing.
4. **Drain evenly.** The category is drawn weighted by how many eligible
   themes it still has, so a big category is not exhausted last and then run
   back to back once the others are gone.
"""

from __future__ import annotations

import os
import re
from collections import namedtuple
from functools import lru_cache
from datetime import datetime, timedelta

import config

Theme = namedtuple("Theme", "text category category_name")
# One past use of a theme: its normalized key, its category, and when.
Use = namedtuple("Use", "key category opened_at")


class NoThemeAvailable(Exception):
    """Every theme was used inside the hard repeat window."""


_NON_WORD = re.compile(r"[^a-z0-9 ]+")


@lru_cache(maxsize=None)
def normalize(text: str) -> str:
    """A key under which near-identical themes collide.

    "Owls", "owl" and "OWL!" are the same theme for repeat purposes; so are
    "The Beach" and "Beach". Plurals are folded per word: "-es" only after
    the sounds that take it (boxes, peaches, dishes), so "lunges" stays
    distinct from "lungs"; otherwise a trailing "s". It over-merges a few
    pairs (news/new) — harmless, since that can only make repeats rarer.
    """
    words = _NON_WORD.sub(" ", text.lower().replace("&", " and ")).split()
    words = [w for w in words if w not in ("the", "a", "an")]
    out = []
    for word in words:
        if len(word) > 4 and word.endswith(("xes", "zes", "ches", "shes", "sses")):
            word = word[:-2]
        elif len(word) > 2 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        out.append(word)
    return " ".join(out)


def load_catalog(directory: str | None = None) -> list:
    """Every theme in themes/*.txt, in file order."""
    directory = directory or config.THEMES_DIR
    catalog = []
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".txt"):
            continue
        category = name[:-4]
        display = category.replace("-", " ").title()
        with open(os.path.join(directory, name), encoding="utf-8") as handle:
            lines = [line.strip() for line in handle]
        # The first comment line names the category; later comments are notes.
        for line in lines:
            if line.startswith("#"):
                display = line.lstrip("#").strip() or display
                break
        for line in lines:
            if line and not line.startswith("#"):
                catalog.append(Theme(line, category, display))
    return catalog


def dedupe(catalog: list) -> list:
    """The catalog with later duplicates (by normalized key) dropped."""
    seen, out = set(), []
    for theme in catalog:
        key = normalize(theme.text)
        if key in seen:
            continue
        seen.add(key)
        out.append(theme)
    return out


def _last_used(history: list) -> dict:
    last = {}
    for use in history:
        if use.key not in last or use.opened_at > last[use.key]:
            last[use.key] = use.opened_at
    return last


def _recent_categories(history: list, cooldown: int) -> list:
    """Categories of the last `cooldown` rounds, most recent first."""
    ordered = sorted(history, key=lambda u: u.opened_at, reverse=True)
    return [u.category for u in ordered[:cooldown]]


def eligible(theme_key: str, last_used: dict, now: datetime,
             floor_days: int = config.MIN_THEME_REPEAT_DAYS) -> bool:
    """True unless the theme was used within the hard floor."""
    when = last_used.get(theme_key)
    return when is None or now - when >= timedelta(days=floor_days)


def pick_next(catalog: list, history: list, now: datetime, rng,
              cooldown: int = None, repeat_days: int = None,
              queue: list = ()) -> tuple:
    """(Theme, from_queue_index) for the next round.

    `history` is every past round as Use records. `queue` is a list of Theme
    records the operator asked for, oldest first; the first one that clears
    the 90-day floor wins and its index is returned so the caller can remove
    it. Queued themes still inside the floor stay queued until they clear it.
    from_queue_index is None when the pick came from the catalog.
    """
    cooldown = config.CATEGORY_COOLDOWN if cooldown is None else cooldown
    return choose(catalog, _last_used(history),
                  _recent_categories(history, cooldown), now, rng,
                  repeat_days, queue)


def choose(catalog: list, last: dict, recent: list, now: datetime, rng,
           repeat_days: int = None, queue: list = ()) -> tuple:
    """pick_next, given the history already boiled down to `last` (theme key
    -> last use) and `recent` (categories of the latest rounds, newest first).

    Split out so a simulation can keep those two up to date incrementally
    instead of rescanning years of history on every pick.
    """
    repeat_days = config.THEME_REPEAT_DAYS if repeat_days is None else repeat_days
    for index, theme in enumerate(queue):
        if eligible(normalize(theme.text), last, now):
            return theme, index

    window = timedelta(days=repeat_days)
    tiers = (
        lambda key: key not in last,
        lambda key: key in last and now - last[key] >= window,
        lambda key: eligible(key, last, now),
    )
    for in_tier in tiers:
        pool = [t for t in catalog if in_tier(normalize(t.text))]
        if pool:
            return _pick_from(pool, recent, rng), None
    raise NoThemeAvailable(
        f"all {len(catalog)} themes were used within the last "
        f"{config.MIN_THEME_REPEAT_DAYS} days; add more themes")


def _pick_from(pool: list, recent: list, rng) -> Theme:
    by_category = {}
    for theme in pool:
        by_category.setdefault(theme.category, []).append(theme)

    # Relax the cooldown one round at a time until something is allowed, so
    # a nearly exhausted catalog still avoids the most recent categories.
    for window in range(len(recent), -1, -1):
        blocked = set(recent[:window])
        allowed = [c for c in sorted(by_category) if c not in blocked]
        if allowed:
            break

    weights = [len(by_category[c]) for c in allowed]
    category = rng.choices(allowed, weights=weights, k=1)[0]
    return rng.choice(by_category[category])


def fresh_days_left(catalog: list, history: list, rounds_per_day: int) -> float:
    """Days of never-used themes remaining at the current pace."""
    used = {u.key for u in history}
    fresh = sum(1 for t in dedupe(catalog) if normalize(t.text) not in used)
    return fresh / max(rounds_per_day, 1)
