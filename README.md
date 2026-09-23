# Pun Battle

An hourly pun contest on Bluesky: [@punbattle.bsky.social](https://bsky.app/profile/punbattle.bsky.social).

- **Every hour, on the hour**, the bot posts a theme — "LIGHTHOUSES",
  "GARLIC PRESSES", "A SANDWICH'S IDENTITY CRISIS".
- **Reply to the theme post with your best pun.** Direct replies are entries;
  you can post as many as you like, and your best one counts.
- **The pun with the most likes when the hour ends wins 60 points.** Your own
  like doesn't count. A tie splits the 60 evenly.
- The next hour's theme post names the winner, and the bot replies to the
  winning pun with the player's new total and rank.
- Once a day (noon) the post also shows the all-time top three.

Puns posted late in the hour have less time to collect likes — being quick
is part of the game.

## Themes

5,800+ themes in 95 categories (`themes/*.txt`), from cheese to courtrooms to
constellations. No theme repeats within 90 days (in practice, not for eight
months), and no category comes up twice in the same day.

## Running it

It runs on the Mac Mini under launchd, managed by OpenClaw, exactly like the
Battleship and Minesweeper bots. Setup, once:

```bash
git clone https://github.com/adamsmithkipnis/pun-battle.git ~/pun-battle
cd ~/pun-battle
./setup.sh          # builds the venv, writes .env from the example, stops
# put the account's app password into .env, then:
./setup.sh          # tests, installs com.punbattle.bot and com.punbattle.deploy
.venv/bin/python tools/profile.py   # avatar, name, bio, pinned rules post
```

The first theme goes up at the next top of the hour.

After that, `git push` is the deploy. See [CLAUDE.md](CLAUDE.md) for the
operating commands and the rules that must not be broken.

## Layout

| File | What it does |
|---|---|
| `main.py` | Scheduler, the hourly tick, admin commands |
| `judging.py` | Who won and how the points split (pure logic) |
| `themes.py` | Catalog loading and the no-repeat picker (pure logic) |
| `posts.py` | Post copy, fitted to 300 characters (pure logic) |
| `bluesky.py` | AT Protocol: posting, reading replies and likes |
| `db.py` | SQLite: rounds, entries, awards, queue, posts |
| `reset.py` | Wipe posts and/or the database, for testing |
| `tools/profile.py` | Profile copy (name, bio, pinned rules) and the script that applies it |
| `tools/avatar.py` | Draws `assets/avatar.png` (needs Pillow; the bot doesn't) |
| `tests/` | Unit tests, plus `dryrun.py` for an end-to-end run with no network |
