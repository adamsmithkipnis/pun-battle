"""Play several rounds end to end with fake players, writing every post to
dry-run/ instead of Bluesky. No credentials, no network.

    .venv/bin/python tests/dryrun.py            # 4 rounds
    .venv/bin/python tests/dryrun.py --rounds 12

Each generated post is a .txt file with its character count and the byte
ranges of its facets, so lengths, mention links and tie wording can be read
exactly as they would go out.
"""

import argparse
import os
import random
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

os.environ["POST_MODE"] = "dry"
os.environ["DRY_DIR"] = "dry-run"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config    # noqa: E402
import bluesky   # noqa: E402
import db        # noqa: E402
import judging   # noqa: E402
import main      # noqa: E402

PLAYERS = ["alice.bsky.social", "bob.bsky.social", "cy.bsky.social",
           "dee.bsky.social", "punmaster9000.bsky.social"]


def fake_round(post_uri, opened, rng, store, likers):
    """Invent a few entries for a round, some with ties on purpose."""
    entries = []
    for i, handle in enumerate(rng.sample(PLAYERS, rng.randint(0, len(PLAYERS)))):
        likes = rng.choice([0, 1, 2, 3, 3, 5])
        at = (opened + timedelta(minutes=rng.randint(1, 59))).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z")
        did = f"did:plc:{handle.split('.')[0]}"
        e = judging.Entry(did=did, handle=handle, text=f"pun #{i}",
                          uri=f"at://{did}/app.bsky.feed.post/{post_uri[-3:]}{i}",
                          cid="cid", created_at=at, indexed_at=at,
                          like_count=likes + 1)          # +1: their own like
        entries.append(e)
        likers[e.uri] = {f"did:plc:fan{n}" for n in range(likes)} | {did}
    store[post_uri] = entries


def run(rounds: int, seed: int) -> None:
    shutil.rmtree(config.DRY_DIR, ignore_errors=True)
    tmp = tempfile.mkdtemp(prefix="puntime-dryrun-")
    config.DB_PATH = os.path.join(tmp, "dryrun.db")
    db.init_db()
    bluesky.login()

    rng = random.Random(seed)
    store, likers = {}, {}
    bluesky.get_entries = lambda uri: list(store.get(uri, []))
    bluesky.get_likers = lambda uri: set(likers.get(uri, ()))

    now = datetime(2026, 9, 22, 17, 0, tzinfo=timezone.utc)   # 10 AM PDT
    for _ in range(rounds + 1):
        main.run_tick(now)
        current = db.current_round()
        fake_round(current["post_uri"], now, rng, store, likers)
        now += timedelta(minutes=config.ROUND_MINUTES)

    print(f"\n{rounds} rounds played. Posts written to {config.DRY_DIR}/:\n")
    for name in sorted(os.listdir(config.DRY_DIR)):
        with open(os.path.join(config.DRY_DIR, name)) as handle:
            body = handle.read().split("\n--- ")[0].strip()
        print(f"── {name}\n{body}\n")
    print("Leaderboard:")
    main.print_leaderboard(10)
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=4)
    parser.add_argument("--seed", type=int, default=3)
    args = parser.parse_args()
    run(args.rounds, args.seed)
