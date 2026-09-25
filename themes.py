"""The theme catalog and the rules for choosing the next theme.

Pure logic, no network and no database: the caller passes in the history, so
every rule here can be exercised by simulating years of rounds in a test.

The catalog is plain text, one category per file, so anybody (or OpenClaw)
can add topics by editing a file and pushing:

    # Coffee & Tea
    # family: food
    Coffee | brew, grounds, roast, espresso, latte, perk, mug, decaf, bean

The angles after the pipe are never posted. They are the evidence that a
topic is at the right level: pun contests live on a topic's vocabulary, since
nobody may reuse a word, so a topic needs at least eight words that double as
everyday words or sound like something else. Fewer and it is too narrow
("Thermoses"); if they share nothing, it is too broad ("Computers").

Two kinds of round:

* **Solo** — one topic ("FOOTWEAR").
* **Mashup** — two topics from different families ("DENTISTRY + GEOLOGY"),
  one pun must cover both. About one round in four (config.MASHUP_RATE),
  never two in a row.

Choosing, in order of precedence:

1. **Never within 90 days.** No theme — solo topic or mashup pair — whose last
   use is inside config.MIN_THEME_REPEAT_DAYS is ever eligible: not from the
   catalog, not from the manual queue, not when the catalog runs low. If
   nothing is left the pick fails loudly rather than bending the rule.
2. **Spread topics out.** A topic does not reappear in any form, alone or
   half of a mashup, within COMPONENT_SPACING_DAYS. So "Coffee" can't come
   back a week later as "Coffee + Knitting".
3. **Fresh first.** Never-used topics, then ones last used more than
   THEME_REPEAT_DAYS ago, then (only if both run out) anything past the floor.
4. **Spread the categories.** No category used in the last CATEGORY_COOLDOWN
   rounds, relaxed step by step only if that leaves nothing.
5. **Drain evenly.** Categories are drawn weighted by how many eligible topics
   they still have.
"""

from __future__ import annotations

import os
import re
from collections import namedtuple
from datetime import datetime, timedelta
from functools import lru_cache

import config

Theme = namedtuple(
    "Theme", "text category category_name family angles components",
    defaults=("", (), ()))
"""A solo topic, or a mashup whose `components` are its two solo Themes."""

# One past round: its theme key, its categories ("a" or "a|b"), when it
# opened, and the topic keys it used (one, or two for a mashup).
Use = namedtuple("Use", "key category opened_at components")

MASHUP_PREFIX = "mashup:"
MASHUP_JOIN = " + "


class NoThemeAvailable(Exception):
    """Every theme was used inside the hard repeat window."""


_NON_WORD = re.compile(r"[^a-z0-9 ]+")


@lru_cache(maxsize=None)
def normalize(text: str) -> str:
    """A key under which near-identical topics collide.

    "Owls", "owl" and "OWL!" are the same topic for repeat purposes; so are
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


# ---------------------------------------------------------------------------
# Themes and their keys
# ---------------------------------------------------------------------------

def is_mashup(theme: Theme) -> bool:
    return bool(theme.components)


def make_mashup(a: Theme, b: Theme) -> Theme:
    return Theme(
        text=f"{a.text}{MASHUP_JOIN}{b.text}",
        category=f"{a.category}|{b.category}",
        category_name=f"{a.category_name}{MASHUP_JOIN}{b.category_name}",
        family=f"{a.family}|{b.family}",
        components=(a, b))


def component_keys(theme: Theme) -> tuple:
    """The topic keys a round uses: one for solo, two for a mashup."""
    if is_mashup(theme):
        return tuple(normalize(c.text) for c in theme.components)
    return (normalize(theme.text),)


def theme_key(theme: Theme) -> str:
    """The repeat key. A mashup's is order-independent: A + B == B + A."""
    if is_mashup(theme):
        return MASHUP_PREFIX + "|".join(sorted(component_keys(theme)))
    return normalize(theme.text)


def categories_of(theme: Theme) -> tuple:
    return tuple(theme.category.split("|"))


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def _parse_angles(raw: str) -> tuple:
    return tuple(a.strip() for a in raw.split(",") if a.strip())


def load_catalog(directory: str | None = None) -> list:
    """Every solo topic in themes/*.txt, in file order.

    Files starting with "_" are not categories (see load_blocked_pairs).
    """
    directory = directory or config.THEMES_DIR
    catalog = []
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".txt") or name.startswith("_"):
            continue
        category = name[:-4]
        display, family = category.replace("-", " ").title(), ""
        with open(os.path.join(directory, name), encoding="utf-8") as handle:
            lines = [line.strip() for line in handle]
        # The first comment names the category; "# family: x" sets the family.
        named = False
        for line in lines:
            if not line.startswith("#"):
                continue
            body = line.lstrip("#").strip()
            if body.lower().startswith("family:"):
                family = body.split(":", 1)[1].strip().lower()
            elif not named and body:
                display, named = body, True
        for line in lines:
            if not line or line.startswith("#"):
                continue
            text, _, angles = line.partition("|")
            catalog.append(Theme(text.strip(), category, display, family,
                                 _parse_angles(angles)))
    return catalog


def load_blocked_pairs(directory: str | None = None) -> frozenset:
    """Mashup pairs that read badly, from themes/_blocked_pairs.txt
    ("Topic A + Topic B" per line), as order-independent pair keys."""
    path = os.path.join(directory or config.THEMES_DIR, "_blocked_pairs.txt")
    if not os.path.exists(path):
        return frozenset()
    keys = set()
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or MASHUP_JOIN not in line:
                continue
            a, b = line.split(MASHUP_JOIN, 1)
            keys.add(MASHUP_PREFIX + "|".join(sorted((normalize(a), normalize(b)))))
    return frozenset(keys)


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


def lookup(catalog: list, text: str) -> Theme | None:
    """The catalog topic matching `text`, if any."""
    key = normalize(text)
    for theme in catalog:
        if normalize(theme.text) == key:
            return theme
    return None


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

class History:
    """Everything the rules need from past rounds, kept incrementally so a
    simulation of years of rounds doesn't rescan the whole history per pick."""

    def __init__(self, cooldown: int | None = None):
        self.cooldown = config.CATEGORY_COOLDOWN if cooldown is None else cooldown
        self.last = {}            # theme key -> last opened
        self.last_topic = {}      # topic key -> last appearance in any form
        self.recent = []          # per round, newest first: tuple of categories
        self.since_mashup = 10**6  # rounds since the last mashup
        self.used = set()         # every topic key ever used

    @classmethod
    def from_uses(cls, uses: list, cooldown: int | None = None) -> "History":
        history = cls(cooldown)
        for use in sorted(uses, key=lambda u: u.opened_at):
            history._record(use.key, tuple(use.category.split("|")),
                            use.opened_at, tuple(use.components))
        return history

    def add(self, theme: Theme, when: datetime) -> None:
        self._record(theme_key(theme), categories_of(theme), when,
                     component_keys(theme))

    def _record(self, key, categories, when, components) -> None:
        if key not in self.last or when > self.last[key]:
            self.last[key] = when
        for topic in components or (key,):
            if topic not in self.last_topic or when > self.last_topic[topic]:
                self.last_topic[topic] = when
            self.used.add(topic)
        self.recent.insert(0, categories)
        del self.recent[self.cooldown:]
        self.since_mashup = 0 if key.startswith(MASHUP_PREFIX) else self.since_mashup + 1

    def recent_categories(self, rounds: int) -> set:
        return {c for cats in self.recent[:rounds] for c in cats}


def eligible(key: str, last: dict, now: datetime,
             floor_days: int = config.MIN_THEME_REPEAT_DAYS) -> bool:
    """True unless the theme was used within the hard floor."""
    when = last.get(key)
    return when is None or now - when >= timedelta(days=floor_days)


def mashup_chance(rate: float, gap: int) -> float:
    """Per-round probability that yields `rate` overall, given that a mashup
    is only possible once `gap` solo rounds have passed since the last one.

    A cycle is one mashup, `gap` forced solos, then 1/p - 1 more solos on
    average before the next mashup: gap + 1/p rounds in all, so
    rate = 1 / (gap + 1/p) and p = 1 / (1/rate - gap). A rate too high for
    the gap simply becomes "every eligible round". At 0.25 with a gap of 1,
    p = 1/3: mashups land unpredictably, never back to back, one in four.
    """
    if rate <= 0:
        return 0.0
    wait = 1 / rate - gap
    return 1.0 if wait <= 1 else 1 / wait


# ---------------------------------------------------------------------------
# Choosing
# ---------------------------------------------------------------------------

def choose(catalog: list, history: History, now: datetime, rng,
           repeat_days: int | None = None, queue: list = (),
           mashup_rate: float | None = None,
           blocked_pairs: frozenset = frozenset()) -> tuple:
    """(Theme, from_queue_index) for the next round.

    `queue` is a list of Themes the operator asked for, oldest first; the
    first that clears the 90-day floor wins and its index is returned so the
    caller can remove it. The queue bypasses spacing and cooldowns — it is a
    deliberate choice — but never the floor.
    """
    repeat_days = config.THEME_REPEAT_DAYS if repeat_days is None else repeat_days
    rate = config.MASHUP_RATE if mashup_rate is None else mashup_rate

    for index, theme in enumerate(queue):
        if eligible(theme_key(theme), history.last, now):
            return theme, index

    if (history.since_mashup >= config.MASHUP_MIN_GAP
            and rng.random() < mashup_chance(rate, config.MASHUP_MIN_GAP)):
        mashup = _choose_mashup(catalog, history, now, rng, repeat_days,
                                blocked_pairs)
        if mashup is not None:
            return mashup, None
    return _choose_solo(catalog, history, now, rng, repeat_days), None


def _spaced(key: str, history: History, now: datetime) -> bool:
    when = history.last_topic.get(key)
    return when is None or now - when >= timedelta(days=config.COMPONENT_SPACING_DAYS)


def _tiers(history: History, now: datetime, repeat_days: int):
    """Filters from most to least preferred, over topic keys. Every tier keeps
    the 90-day floor; only the last gives up topic spacing."""
    window = timedelta(days=repeat_days)
    last = history.last
    return (
        lambda k: k not in history.used,
        lambda k: _spaced(k, history, now) and (k not in last or now - last[k] >= window),
        lambda k: _spaced(k, history, now) and eligible(k, last, now),
        lambda k: eligible(k, last, now),
    )


def _choose_solo(catalog, history, now, rng, repeat_days) -> Theme:
    # Category spread beats freshness: an older topic from a rested category
    # is better than a fresh one from a category we just had. So walk every
    # tier with the full cooldown first, and only then relax it.
    tiers = _tiers(history, now, repeat_days)
    blocked = history.recent_categories(history.cooldown)
    for in_tier in tiers:
        pool = [t for t in catalog if t.category not in blocked
                and in_tier(normalize(t.text))]
        if pool:
            return _pick_from(pool, history, rng)
    for in_tier in tiers:
        pool = [t for t in catalog if in_tier(normalize(t.text))]
        if pool:
            return _pick_from(pool, history, rng)
    raise NoThemeAvailable(
        f"all {len(catalog)} topics were used within the last "
        f"{config.MIN_THEME_REPEAT_DAYS} days; add more topics")


def _choose_mashup(catalog, history, now, rng, repeat_days,
                   blocked_pairs) -> Theme | None:
    """Two spaced topics from different families and fresh categories, as a
    pair not seen within the floor. None if no such pair turns up."""
    pool = [t for t in catalog
            if _spaced(normalize(t.text), history, now) and t.family]
    blocked = history.recent_categories(history.cooldown)
    pool = [t for t in pool if t.category not in blocked]
    if not pool:
        return None
    for _ in range(25):
        a = _pick_from(pool, history, rng)
        partners = [t for t in pool
                    if t.family != a.family and t.category != a.category]
        if not partners:
            return None
        b = _pick_from(partners, history, rng)
        mashup = make_mashup(a, b) if rng.random() < 0.5 else make_mashup(b, a)
        key = theme_key(mashup)
        if key in blocked_pairs or not eligible(key, history.last, now):
            continue
        return mashup
    return None


def _pick_from(pool: list, history: History, rng) -> Theme:
    by_category = {}
    for theme in pool:
        by_category.setdefault(theme.category, []).append(theme)

    # Relax the cooldown one round at a time until something is allowed, so
    # a nearly exhausted catalog still avoids the most recent categories.
    for window in range(len(history.recent), -1, -1):
        blocked = history.recent_categories(window)
        allowed = [c for c in sorted(by_category) if c not in blocked]
        if allowed:
            break

    weights = [len(by_category[c]) for c in allowed]
    category = rng.choices(allowed, weights=weights, k=1)[0]
    return rng.choice(by_category[category])


def fresh_days_left(catalog: list, history: History, rounds_per_day: int) -> float:
    """Days of never-used topics remaining at the current pace.

    Each round uses 1 + MASHUP_RATE topics on average (a mashup uses two).
    """
    fresh = sum(1 for t in dedupe(catalog) if normalize(t.text) not in history.used)
    per_day = rounds_per_day * (1 + config.MASHUP_RATE)
    return fresh / max(per_day, 1)
