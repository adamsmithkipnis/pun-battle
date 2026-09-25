"""SQLite persistence: rounds, the entries and awards that decided them, the
manual theme queue, and every post the bot made.

Times are stored as ISO 8601 UTC strings ("2026-09-22T15:00:00+00:00"), which
sort correctly as text.

A round moves through three statuses, and each move is saved only after the
Bluesky call it depends on has succeeded, so a failure anywhere replays
cleanly on the next tick:

    open       theme posted, taking entries until closes_at
    judged     likes counted and awards saved; waiting to be announced
    announced  its result line went out in the next theme post
    skipped    retired by hand (--skip) without scoring
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

import config

logger = logging.getLogger(__name__)

OPEN, JUDGED, ANNOUNCED, SKIPPED = "open", "judged", "announced", "skipped"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS rounds (
    id INTEGER PRIMARY KEY,
    theme TEXT NOT NULL,
    theme_key TEXT NOT NULL,      -- themes.theme_key(theme): the repeat key
    category TEXT,                -- "a", or "a|b" for a mashup
    category_name TEXT,
    post_uri TEXT,
    post_cid TEXT,
    opened_at TEXT NOT NULL,
    closes_at TEXT NOT NULL,
    status TEXT NOT NULL,
    judged_at TEXT,
    top_likes INTEGER,
    entry_count INTEGER,
    player_count INTEGER,
    components TEXT               -- topic keys used, "|"-separated
);

CREATE INDEX IF NOT EXISTS idx_rounds_status ON rounds (status);
CREATE INDEX IF NOT EXISTS idx_rounds_key ON rounds (theme_key);

-- Every entry as it stood when the round was judged. like_count is Bluesky's
-- raw number; likes is the exact count (NULL where it was provably too low to
-- matter and so never fetched).
CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY,
    round_id INTEGER NOT NULL,
    uri TEXT NOT NULL,
    cid TEXT,
    did TEXT NOT NULL,           -- stable id: handles change, this does not
    handle TEXT,
    text TEXT,
    created_at TEXT,
    like_count INTEGER,
    likes INTEGER,
    UNIQUE (round_id, uri)
);

CREATE INDEX IF NOT EXISTS idx_entries_did ON entries (did);

CREATE TABLE IF NOT EXISTS awards (
    id INTEGER PRIMARY KEY,
    round_id INTEGER NOT NULL,
    did TEXT NOT NULL,
    handle TEXT,
    entry_uri TEXT,
    entry_cid TEXT,
    entry_text TEXT,
    likes INTEGER,
    points REAL NOT NULL,
    tied_with INTEGER DEFAULT 1,  -- how many shared the win
    replied_uri TEXT,             -- the congratulations reply, once sent
    reply_attempts INTEGER DEFAULT 0,
    UNIQUE (round_id, did)
);

CREATE INDEX IF NOT EXISTS idx_awards_did ON awards (did);

CREATE TABLE IF NOT EXISTS theme_queue (
    id INTEGER PRIMARY KEY,
    theme TEXT NOT NULL,
    category TEXT,
    added_at TEXT
);

-- Every post the bot creates, so a reset deletes exactly the bot's own posts.
CREATE TABLE IF NOT EXISTS posts (
    uri TEXT PRIMARY KEY,
    rkey TEXT,
    cid TEXT,
    kind TEXT,                    -- 'theme' | 'winner'
    round_id INTEGER,
    created_at TEXT
);
"""

# Columns added after first release. Existing databases get them by ALTER so
# an upgrade never loses a round in progress. Anything added to _SCHEMA's
# tables later must also be listed here — tests/test_migration.py checks.
_ADDED_COLUMNS = {
    "rounds": [("components", "TEXT")],
    "entries": [],
    "awards": [],
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def from_iso(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def connect_readonly() -> sqlite3.Connection:
    """A connection that cannot write.

    A `file:...?mode=ro` URI does NOT work in WAL mode (SQLite needs to create
    a shared-memory index); query_only gives the same guarantee and works.
    """
    conn = sqlite3.connect(config.DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=1")
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    for table, columns in _ADDED_COLUMNS.items():
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not existing:
            continue
        for name, coltype in columns:
            if name not in existing:
                logger.info("Migrating: adding %s.%s", table, name)
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {coltype}")


def init_db() -> None:
    with _connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA)
        _migrate(conn)


# ---------------------------------------------------------------------------
# Rounds
# ---------------------------------------------------------------------------

def current_round():
    """The newest round that still needs work (open or judged), or None."""
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM rounds WHERE status IN (?, ?) "
            "ORDER BY id DESC LIMIT 1", (OPEN, JUDGED)).fetchone()


def get_round(round_id: int):
    with _connect() as conn:
        return conn.execute("SELECT * FROM rounds WHERE id = ?",
                            (round_id,)).fetchone()


def last_round():
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM rounds ORDER BY id DESC LIMIT 1").fetchone()


def open_round(theme: str, theme_key: str, category: str, category_name: str,
               post_uri: str, post_cid: str, opened_at: datetime,
               closes_at: datetime, components: tuple = (),
               retire_round_id: int | None = None,
               retire_status: str = ANNOUNCED,
               queue_id: int | None = None) -> int:
    """Record a freshly posted theme — and, in the same transaction, retire
    the round it replaced (announced, or skipped) and consume the queue entry
    it came from.

    One transaction so a crash can never leave the new round saved but the
    old one still 'judged' (which would announce it twice).
    """
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO rounds (theme, theme_key, category, category_name,
                   post_uri, post_cid, opened_at, closes_at, status,
                   components)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (theme, theme_key, category, category_name, post_uri, post_cid,
             to_iso(opened_at), to_iso(closes_at), OPEN,
             "|".join(components or (theme_key,))))
        if retire_round_id is not None:
            conn.execute("UPDATE rounds SET status = ? WHERE id = ?",
                         (retire_status, retire_round_id))
        if queue_id is not None:
            conn.execute("DELETE FROM theme_queue WHERE id = ?", (queue_id,))
        return cur.lastrowid


def save_judgement(round_id: int, entries: list, result) -> None:
    """Store the entry snapshot and the awards, and mark the round judged."""
    tied = len(result.winners)
    with _connect() as conn:
        for e in entries:
            conn.execute(
                """INSERT OR REPLACE INTO entries (round_id, uri, cid, did,
                       handle, text, created_at, like_count, likes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (round_id, e.uri, e.cid, e.did, e.handle, e.text,
                 e.created_at, e.like_count, e.likes))
        for award in result.winners:
            conn.execute(
                """INSERT OR REPLACE INTO awards (round_id, did, handle,
                       entry_uri, entry_cid, entry_text, likes, points,
                       tied_with)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (round_id, award.did, award.handle, award.entry.uri,
                 award.entry.cid, award.entry.text, award.likes,
                 award.points, tied))
        conn.execute(
            """UPDATE rounds SET status = ?, judged_at = ?, top_likes = ?,
                   entry_count = ?, player_count = ? WHERE id = ?""",
            (JUDGED, now_iso(), result.top_likes, result.entry_count,
             result.player_count, round_id))


def awards_for(round_id: int) -> list:
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM awards WHERE round_id = ? ORDER BY id",
            (round_id,)).fetchall()


def theme_history() -> list:
    """(theme_key, category, opened_at, component keys) for every round ever
    posted. Rounds from before mashups existed used one topic: their own key."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT theme_key, category, opened_at, components FROM rounds"
        ).fetchall()
    return [(r["theme_key"], r["category"] or "", from_iso(r["opened_at"]),
             tuple((r["components"] or r["theme_key"]).split("|")))
            for r in rows]


def round_count() -> int:
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM rounds").fetchone()[0]


# ---------------------------------------------------------------------------
# Congratulation replies
# ---------------------------------------------------------------------------

MAX_REPLY_ATTEMPTS = 3


def pending_replies() -> list:
    """Awards from announced rounds whose winner has not been replied to.

    Only announced rounds: the public result goes out first, then the
    personal reply. Gives up after a few attempts — the usual cause is that
    the winner deleted their pun, and retrying that forever is just noise.
    """
    with _connect() as conn:
        return conn.execute(
            """SELECT a.*, r.theme, r.post_uri AS root_uri,
                      r.post_cid AS root_cid
               FROM awards a JOIN rounds r ON r.id = a.round_id
               WHERE a.replied_uri IS NULL AND r.status = ?
                 AND a.reply_attempts < ?
               ORDER BY a.id""", (ANNOUNCED, MAX_REPLY_ATTEMPTS)).fetchall()


def mark_replied(award_id: int, uri: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE awards SET replied_uri = ? WHERE id = ?",
                     (uri, award_id))


def count_reply_attempt(award_id: int) -> None:
    with _connect() as conn:
        conn.execute("UPDATE awards SET reply_attempts = reply_attempts + 1 "
                     "WHERE id = ?", (award_id,))


# ---------------------------------------------------------------------------
# Scores
# ---------------------------------------------------------------------------

def player_total(did: str) -> float:
    with _connect() as conn:
        row = conn.execute("SELECT COALESCE(SUM(points), 0) FROM awards "
                           "WHERE did = ?", (did,)).fetchone()
    return float(row[0])


def player_rank(did: str) -> tuple:
    """(rank, players) — standard competition ranking over everyone who has
    ever entered, so a player tied for second is "#2", and the field counts
    people who played without winning too."""
    total = player_total(did)
    with _connect() as conn:
        players = conn.execute(
            "SELECT COUNT(DISTINCT did) FROM ("
            " SELECT did FROM entries UNION SELECT did FROM awards)").fetchone()[0]
        ahead = conn.execute(
            """SELECT COUNT(*) FROM (SELECT did, SUM(points) AS total
                   FROM awards GROUP BY did) WHERE total > ? + 1e-9""",
            (total,)).fetchone()[0]
    return ahead + 1, max(players, 1)


def leaderboard(limit: int = 10) -> list:
    """[(did, handle, total, wins)] — handle is the most recent one seen."""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT did, SUM(points) AS total, COUNT(*) AS wins,
                      (SELECT handle FROM awards a2 WHERE a2.did = a.did
                       ORDER BY a2.id DESC LIMIT 1) AS handle
               FROM awards a GROUP BY did
               ORDER BY total DESC, wins DESC, MIN(id) ASC
               LIMIT ?""", (limit,)).fetchall()
    return [(r["did"], r["handle"], float(r["total"]), r["wins"]) for r in rows]


# ---------------------------------------------------------------------------
# Theme queue
# ---------------------------------------------------------------------------

def queue_theme(theme: str, category: str = "custom") -> None:
    with _connect() as conn:
        conn.execute("INSERT INTO theme_queue (theme, category, added_at) "
                     "VALUES (?, ?, ?)", (theme, category, now_iso()))


def queued_themes() -> list:
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM theme_queue ORDER BY id").fetchall()


# ---------------------------------------------------------------------------
# Posts (for reset.py)
# ---------------------------------------------------------------------------

def remember_post(uri: str, cid: str, kind: str, round_id: int | None) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO posts (uri, rkey, cid, kind, round_id, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (uri, uri.rsplit("/", 1)[-1], cid, kind, round_id, now_iso()))


def count_posts() -> int:
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]


def forget_posts(uris: list) -> None:
    with _connect() as conn:
        conn.executemany("DELETE FROM posts WHERE uri = ?",
                         [(u,) for u in uris])


def reset_all(keep_scores: bool = False) -> None:
    """Clear the database. keep_scores keeps rounds, entries and awards (the
    history that scores and the no-repeat rule are built from)."""
    with _connect() as conn:
        conn.execute("DELETE FROM posts")
        conn.execute("DELETE FROM theme_queue")
        if not keep_scores:
            conn.execute("DELETE FROM awards")
            conn.execute("DELETE FROM entries")
            conn.execute("DELETE FROM rounds")
