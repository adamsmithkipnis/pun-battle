"""Set up the Bluesky profile: avatar, name, bio, and the pinned rules post.

    .venv/bin/python tools/profile.py --dry-run    # show everything, change nothing
    .venv/bin/python tools/profile.py              # apply it (uses .env credentials)
    .venv/bin/python tools/profile.py --no-post    # profile only, keep the pinned post

Run once on the Mini after setup.sh. Safe to re-run: it updates the profile
in place (keeping any banner or other fields set in the app) and only posts
a new rules thread when there is no pinned post yet, or when --repost is
given — in which case the new thread is pinned and the old one left alone
for you to delete.

The copy lives here so it is versioned with the rules it describes. If the
round length or points change in .env, the text follows.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config    # noqa: E402  (first: loads .env)
import bluesky   # noqa: E402

AVATAR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "assets", "avatar.png")

DISPLAY_NAME = "Pun Battle ⚔️"

_EVERY = ("every hour, on the hour" if config.ROUND_MINUTES == 60
          else f"every {config.ROUND_MINUTES} minutes")
_PTS = config.ROUND_POINTS

BIO = (
    "Your pun vs. theirs. ⚔️\n"
    f"A new theme drops {_EVERY}. Reply with your best pun — "
    f"most likes when time's up wins {_PTS} points. Ties split.\n"
    "Watch for mashup rounds: one pun, two topics.\n"
    "📌 How to play is pinned. #PunBattle"
)

RULES = (
    "📌 How to play Pun Battle\n\n"
    f"⏰ {_EVERY[0].upper() + _EVERY[1:]}, I post a theme\n"
    "💬 Reply with your best pun (as many as you like)\n"
    f"❤️ Most likes when time's up wins {_PTS} pts\n"
    "🤝 Ties split the points\n"
    "⚔️ Mashup rounds: one pun, both topics\n"
    "🏆 Winners get a reply with their total\n\n"
    "May the best pun win! #PunBattle"
)

def _hour(h: int) -> str:
    if h == 12:
        return "noon"
    return f"{(h - 1) % 12 + 1} {'AM' if h < 12 else 'PM'}"


_ZONE = {"America/Los_Angeles": "PT", "America/Denver": "MT",
         "America/Chicago": "CT", "America/New_York": "ET"}.get(
    config.TIMEZONE, config.TIMEZONE)


FINE_PRINT = (
    "The fine print:\n"
    "• Only direct replies to the theme post count\n"
    "• Replies after the deadline don't\n"
    "• Your own like doesn't count, and only your best pun is scored\n"
    f"• A pun needs at least {config.MIN_LIKES} "
    f"like{'s' if config.MIN_LIKES != 1 else ''} to win\n"
    f"• All-time top 3 shown daily at {_hour(config.LEADERBOARD_HOUR)} "
    f"{_ZONE}\n"
    "Puns only — keep it friendly. 🙂"
)

BIO_LIMIT = 256


def show() -> None:
    for label, text, limit in (("Display name", DISPLAY_NAME, 64),
                               ("Bio", BIO, BIO_LIMIT),
                               ("Pinned post", RULES, bluesky.POST_LIMIT),
                               ("Reply under it", FINE_PRINT, bluesky.POST_LIMIT)):
        print(f"── {label} ({len(text)}/{limit})\n{text}\n")
    print(f"── Avatar: {AVATAR}")


def check() -> None:
    problems = [name for name, text, limit in (
        ("display name", DISPLAY_NAME, 64), ("bio", BIO, BIO_LIMIT),
        ("rules", RULES, bluesky.POST_LIMIT),
        ("fine print", FINE_PRINT, bluesky.POST_LIMIT)) if len(text) > limit]
    if problems:
        sys.exit(f"Too long: {', '.join(problems)}")
    if not os.path.exists(AVATAR):
        sys.exit(f"Missing {AVATAR} — run tools/avatar.py first")


def _existing_profile(client):
    try:
        return client.com.atproto.repo.get_record({
            "repo": client.me.did, "collection": "app.bsky.actor.profile",
            "rkey": "self"}).value
    except Exception:
        return None


def _field(record, name):
    if record is None:
        return None
    if isinstance(record, dict):
        return record.get(name) or record.get(
            {"pinned_post": "pinnedPost"}.get(name, name))
    return getattr(record, name, None)


def apply(post: bool, repost: bool) -> None:
    from atproto import models

    bluesky.login()
    client = bluesky._client
    current = _existing_profile(client)

    with open(AVATAR, "rb") as handle:
        avatar_blob = client.upload_blob(handle.read()).blob
    print("uploaded avatar")

    pinned = _field(current, "pinned_post")
    if post and (repost or not pinned):
        root_uri, root_cid = bluesky.post_text(RULES, kind="rules")
        bluesky.post_reply(FINE_PRINT, root_uri, root_cid, kind="rules")
        pinned = models.ComAtprotoRepoStrongRef.Main(uri=root_uri, cid=root_cid)
        print(f"posted rules thread: {root_uri}")
    elif pinned:
        print("keeping the existing pinned post (use --repost to replace it)")

    record = models.AppBskyActorProfile.Record(
        display_name=DISPLAY_NAME,
        description=BIO,
        avatar=avatar_blob,
        banner=_field(current, "banner"),
        pinned_post=pinned,
    )
    client.com.atproto.repo.put_record({
        "repo": client.me.did, "collection": "app.bsky.actor.profile",
        "rkey": "self", "record": record})
    print(f"profile updated for @{config.HANDLE}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-post", action="store_true",
                        help="update the profile only; post nothing")
    parser.add_argument("--repost", action="store_true",
                        help="post and pin a fresh rules thread")
    args = parser.parse_args()

    check()
    show()
    if args.dry_run:
        print("\nDry run; nothing changed.")
        return 0
    if config.POST_MODE == "dry":
        sys.exit("POST_MODE=dry in .env — set it to live to apply the profile")
    apply(post=not args.no_post, repost=args.repost)
    return 0


if __name__ == "__main__":
    sys.exit(main())
