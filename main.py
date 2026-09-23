"""Pun Time — an hourly pun contest on Bluesky.

On the hour, every hour, one tick:

  1. Judge the round that just closed: read the direct replies to its theme
     post, count likes (without self-likes or the bot's), and save the
     winners and their share of the points.
  2. Post the next theme. The same post announces last round's winner, so
     followers see one post an hour, not two.
  3. Reply to each winner's pun with their points and running total.

Each step saves its state only after the Bluesky call it depends on has
succeeded, so a failure at any point replays on the next tick without
double-announcing or double-scoring (see the status notes in db.py).

Themes are only ever posted on the boundary. There is no catch-up at boot: a
Mini that wakes at 3:40 waits for 4:00, judges the overdue round then, and
opens exactly one new round — never a burst of backlogged themes.

Operating commands (see CLAUDE.md):

    main.py                   run the scheduler (what launchd does)
    main.py --status          current round, live entries, queue, runway
    main.py --leaderboard     all-time standings
    main.py --preview 48      the next 48 themes the picker would choose
    main.py --queue "Theme"   put a theme next in line
    main.py --tick            run one tick right now
    main.py --judge-now       judge the open round early and post the next
    main.py --skip            replace the open theme without scoring it
"""

from __future__ import annotations

import argparse
import logging
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import config  # first: loads .env before anything reads a setting

import bluesky
import db
import judging
import posts
import themes

logger = logging.getLogger("main")

TZ = ZoneInfo(config.TIMEZONE)

# A tick that fires this close before a round's close still judges it, so a
# clock a few seconds fast on the Mini can't make a round wait a whole hour.
_CLOSE_TOLERANCE = timedelta(seconds=30)

_catalog = None


def catalog() -> list:
    global _catalog
    if _catalog is None:
        _catalog = themes.load_catalog()
        logger.info("Loaded %d themes in %d categories", len(_catalog),
                    len({t.category for t in _catalog}))
    return _catalog


def rounds_per_day() -> int:
    return 24 * 60 // config.ROUND_MINUTES


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------

def next_boundary(now: datetime, minutes: int | None = None,
                  tz: ZoneInfo | None = None) -> datetime:
    """The first round boundary strictly after `now`, on the local clock.

    Floors to the boundary in local time, then adds the round length in UTC:
    adding in local wall-clock time would land on a nonexistent 2:30 AM on
    the night the clocks spring forward.
    """
    minutes = minutes or config.ROUND_MINUTES
    tz = tz or TZ
    local = now.astimezone(tz)
    floored = local.replace(minute=(local.minute // minutes) * minutes,
                            second=0, microsecond=0)
    return (floored.astimezone(timezone.utc)
            + timedelta(minutes=minutes)).astimezone(tz)


def is_leaderboard_round(now: datetime) -> bool:
    """The first round of LEADERBOARD_HOUR, once a day."""
    local = now.astimezone(TZ)
    return (local.hour == config.LEADERBOARD_HOUR
            and local.minute < config.ROUND_MINUTES)


def _history() -> list:
    return [themes.Use(k, c, t) for k, c, t in db.theme_history()]


def _retry(fn, *args, attempts: int = 3):
    """Call fn, retrying transient network failures with a short backoff.

    Judging happens once per round, so a single dropped request should not
    cost the round an hour.
    """
    delays = [5, 30, 90]
    for attempt in range(attempts):
        try:
            return fn(*args)
        except Exception as exc:
            if attempt == attempts - 1:
                raise
            pause = 60 if bluesky.is_rate_limited(exc) else delays[attempt]
            logger.warning("%s failed (%s); retrying in %ds",
                           getattr(fn, "__name__", "call"), exc, pause)
            time.sleep(pause)


# ---------------------------------------------------------------------------
# The three steps of a tick
# ---------------------------------------------------------------------------

def judge(round_row) -> judging.Result:
    """Count the round's entries and save the result; mark it judged."""
    closes = db.from_iso(round_row["closes_at"])
    bot_did = bluesky.get_did()
    raw = _retry(bluesky.get_entries, round_row["post_uri"])
    entries = judging.valid_entries(raw, bot_did, closes)
    calls = judging.resolve_likes(
        entries, lambda uri: _retry(bluesky.get_likers, uri), bot_did,
        config.MIN_LIKES)
    result = judging.score_round(entries, config.ROUND_POINTS, config.MIN_LIKES)
    db.save_judgement(round_row["id"], entries, result)
    logger.info(
        "Judged round %d (%s): %d entries from %d players, %d likes checks, "
        "top %d likes, winners: %s",
        round_row["id"], round_row["theme"], result.entry_count,
        result.player_count, calls, result.top_likes,
        ", ".join(f"@{a.handle} +{judging.fmt_points(a.points)}"
                  for a in result.winners) or "none")
    return result


def post_next_theme(now: datetime, previous=None, closes_at: datetime = None,
                    retire_status: str = db.ANNOUNCED) -> int:
    """Pick and post the next theme; record the new round.

    `previous` is the round being retired: a judged round is announced in
    the post; a skipped one is retired silently. Nothing is saved unless the
    post goes out.
    """
    queue_rows = db.queued_themes()
    queue = [themes.Theme(r["theme"], r["category"] or "custom",
                          (r["category"] or "custom").replace("-", " ").title())
             for r in queue_rows]
    theme, queue_index = themes.pick_next(
        catalog(), _history(), now, random.Random(), queue=queue)

    closes = closes_at or next_boundary(now)
    announce = None
    if previous is not None and retire_status == db.ANNOUNCED:
        announce = {
            "theme": previous["theme"],
            "awards": [dict(a) for a in db.awards_for(previous["id"])],
            "entry_count": previous["entry_count"] or 0,
        }
    leaders = db.leaderboard(3) if is_leaderboard_round(now) else None
    text, extra_dids = posts.build_theme_post(
        theme.text, closes.astimezone(TZ), announce, leaders,
        posts.pick_hashtags())

    uri, cid = bluesky.post_text(text, "theme", extra_dids)

    round_id = db.open_round(
        theme.text, themes.normalize(theme.text), theme.category,
        theme.category_name, uri, cid, now, closes,
        retire_round_id=previous["id"] if previous is not None else None,
        retire_status=retire_status,
        queue_id=queue_rows[queue_index]["id"] if queue_index is not None else None)
    _remember(uri, cid, "theme", round_id)
    logger.info("Round %d open: %s [%s], closes %s", round_id, theme.text,
                theme.category, closes.strftime("%H:%M %Z"))
    return round_id


def send_winner_replies() -> None:
    """Reply to every winner not yet told. Best effort: a failure is logged
    and retried next tick, and never undoes a round."""
    for row in db.pending_replies():
        total = db.player_total(row["did"])
        rank, players = db.player_rank(row["did"])
        text = posts.build_winner_reply(
            row["theme"], row["likes"], row["points"], row["tied_with"],
            total, rank, players)
        try:
            uri, cid = bluesky.post_reply(
                text, row["entry_uri"], row["entry_cid"],
                row["root_uri"], row["root_cid"], kind="winner")
        except Exception:
            db.count_reply_attempt(row["id"])
            logger.exception("Winner reply to @%s failed (attempt %d)",
                             row["handle"], row["reply_attempts"] + 1)
            continue
        db.mark_replied(row["id"], uri)
        _remember(uri, cid, "winner", row["round_id"])
        logger.info("Told @%s: +%s, total %s (#%d of %d)", row["handle"],
                    judging.fmt_points(row["points"]),
                    judging.fmt_points(total), rank, players)


def _remember(uri: str, cid: str, kind: str, round_id) -> None:
    try:
        db.remember_post(uri, cid, kind, round_id)
    except Exception:
        logger.exception("Could not log post %s", uri)


def run_tick(now: datetime | None = None, force_judge: bool = False) -> None:
    now = now or datetime.now(timezone.utc)
    current = db.current_round()

    if current is not None and current["status"] == db.OPEN:
        closes = db.from_iso(current["closes_at"])
        if now < closes - _CLOSE_TOLERANCE and not force_judge:
            logger.info("Round %d (%s) is open until %s; nothing to do",
                        current["id"], current["theme"],
                        closes.astimezone(TZ).strftime("%H:%M %Z"))
            send_winner_replies()
            return
        judge(current)
        current = db.get_round(current["id"])

    post_next_theme(now, previous=current)
    send_winner_replies()


def tick() -> None:
    """The scheduled job. Never lets an exception kill the scheduler."""
    try:
        run_tick()
    except themes.NoThemeAvailable:
        logger.error("NO THEME AVAILABLE — every theme was used in the last "
                     "%d days. Add themes to themes/*.txt and push.",
                     config.MIN_THEME_REPEAT_DAYS)
    except Exception:
        logger.exception("Tick failed; it will be retried at the next boundary")


# ---------------------------------------------------------------------------
# Admin commands
# ---------------------------------------------------------------------------

def skip_current(now: datetime) -> None:
    """Retire the open theme unscored and post a replacement that closes at
    the same boundary, so the schedule stays on the hour."""
    current = db.current_round()
    if current is None or current["status"] != db.OPEN:
        print("No open round to skip.")
        return
    closes = db.from_iso(current["closes_at"])
    post_next_theme(now, previous=current, closes_at=closes,
                    retire_status=db.SKIPPED)
    print(f"Skipped {current['theme']!r}.")


def print_status() -> None:
    current = db.current_round() or db.last_round()
    if current is None:
        print("No rounds yet. The first theme posts at the next boundary: "
              f"{next_boundary(datetime.now(timezone.utc)).strftime('%H:%M %Z')}")
    else:
        closes = db.from_iso(current["closes_at"]).astimezone(TZ)
        print(f"Round {current['id']}: {current['theme']} "
              f"[{current['category']}] — {current['status']}, "
              f"closes {closes.strftime('%a %H:%M %Z')}")
        print(f"  post: {current['post_uri']}")
        if current["status"] == db.OPEN and config.POST_MODE != "dry":
            entries = judging.valid_entries(
                bluesky.get_entries(current["post_uri"]), bluesky.get_did(),
                db.from_iso(current["closes_at"]))
            print(f"  {len(entries)} entries so far")
            for e in sorted(entries, key=lambda e: -e.like_count)[:3]:
                print(f"    {e.like_count:>3} ♥  @{e.handle}: {e.text[:60]!r}")
    queued = db.queued_themes()
    print(f"Queue: {', '.join(r['theme'] for r in queued) or 'empty'}")
    days = themes.fresh_days_left(catalog(), _history(), rounds_per_day())
    print(f"Never-used themes left: {days:.0f} days at {rounds_per_day()}/day")
    print(f"Rounds played: {db.round_count()}")


def print_leaderboard(limit: int) -> None:
    rows = db.leaderboard(limit)
    if not rows:
        print("No points awarded yet.")
    for i, (_, handle, total, wins) in enumerate(rows, 1):
        print(f"{i:>3}. @{handle:<32} {judging.fmt_points(total):>8} pts "
              f"({wins} win{'s' if wins != 1 else ''})")


def preview(count: int) -> None:
    """Show the next `count` picks without posting or saving anything."""
    now = datetime.now(timezone.utc)
    history = _history()
    boundary = next_boundary(now)
    rng = random.Random()
    for i in range(count):
        theme, _ = themes.pick_next(catalog(), history, boundary, rng)
        print(f"{boundary.astimezone(TZ).strftime('%a %H:%M')}  "
              f"{theme.text:<40} [{theme.category}]")
        history.append(themes.Use(themes.normalize(theme.text),
                                  theme.category, boundary))
        boundary = next_boundary(boundary)
    days = themes.fresh_days_left(catalog(), _history(), rounds_per_day())
    if days < 30:
        print(f"\nWARNING: only {days:.0f} days of never-used themes left.")


# ---------------------------------------------------------------------------

def setup_logging() -> None:
    """Log to LOG_PATH, and to the screen as well when interactive.

    launchd redirects stdout into the same file named by LOG_PATH, so adding a
    stdout handler on top of the file handler writes every line twice. stdout
    is a TTY only when running in the foreground, which is exactly when the
    screen echo is wanted.
    """
    handlers = []
    if config.LOG_PATH:
        handlers.append(logging.FileHandler(config.LOG_PATH))
    if not handlers or sys.stdout.isatty():
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def make_trigger():
    """Fires on every round boundary of the local clock — :00 at hourly."""
    from apscheduler.triggers.cron import CronTrigger
    minute = "0" if config.ROUND_MINUTES == 60 else f"*/{config.ROUND_MINUTES}"
    return CronTrigger(minute=minute, second=0, timezone=TZ)


def run_scheduler() -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler

    scheduler = BlockingScheduler(timezone=TZ)
    scheduler.add_job(
        tick, make_trigger(), id="tick",
        coalesce=True, max_instances=1,
        misfire_grace_time=config.MISFIRE_GRACE_SECONDS)

    days = themes.fresh_days_left(catalog(), _history(), rounds_per_day())
    if days < 30:
        logger.warning("Only %.0f days of never-used themes left — add more "
                       "to themes/*.txt", days)
    now = datetime.now(timezone.utc)
    logger.info("Scheduler started; a round every %d minutes, next at %s",
                config.ROUND_MINUTES, next_boundary(now).strftime("%H:%M %Z"))
    # Replies left over from before a restart go out now; themes wait.
    try:
        send_winner_replies()
    except Exception:
        logger.exception("Startup reply pass failed")
    scheduler.start()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--leaderboard", type=int, nargs="?", const=10,
                        metavar="N")
    parser.add_argument("--preview", type=int, metavar="N")
    parser.add_argument("--queue", metavar="THEME")
    parser.add_argument("--category", default="custom",
                        help="category for --queue (default: custom)")
    parser.add_argument("--tick", action="store_true",
                        help="run one tick now, as if at a boundary")
    parser.add_argument("--judge-now", action="store_true",
                        help="judge the open round early and post the next")
    parser.add_argument("--skip", action="store_true",
                        help="replace the open theme without scoring it")
    args = parser.parse_args()

    setup_logging()
    db.init_db()

    if args.preview:
        preview(args.preview)
        return 0
    if args.queue:
        db.queue_theme(args.queue.strip(), args.category)
        print(f"Queued {args.queue.strip()!r}; it goes up at the next boundary "
              "unless it ran in the last "
              f"{config.MIN_THEME_REPEAT_DAYS} days.")
        return 0
    if args.leaderboard:
        print_leaderboard(args.leaderboard)
        return 0

    logger.info("Pun Time starting (%d-minute rounds, %s pts, %s, "
                "posting %s)", config.ROUND_MINUTES, config.ROUND_POINTS,
                config.TIMEZONE, config.POST_MODE)
    bluesky.login_with_retry()

    now = datetime.now(timezone.utc)
    if args.status:
        print_status()
    elif args.tick:
        run_tick(now)
    elif args.judge_now:
        run_tick(now, force_judge=True)
    elif args.skip:
        skip_current(now)
    else:
        run_scheduler()
    return 0


if __name__ == "__main__":
    sys.exit(main())
