# Working agreement for this repo

Two agents edit this repo: Claude Code (usually from the laptop) and OpenClaw
(on the Mac Mini, at `/Users/robot/pun-battle`). The Mini's checkout is also the
deployment target, so an uncommitted edit there is both a lost change and a
blocked deploy.

## Git

- **Pull before editing**: `git pull --rebase origin main`.
- **Commit and push immediately** when you finish a change. Never leave work
  sitting uncommitted in the Mini's checkout — `deploy.sh` refuses to touch a
  dirty tree, so the bot silently stops receiving updates until it is cleaned
  up.
- `git push` is the deploy. The watcher on the Mini polls origin every five
  minutes and runs `deploy.sh --if-changed`.
- **Never commit `.env`.** It holds the Bluesky app password. It is gitignored;
  keep it that way. `.env` exists only on the Mini and is created by hand once.

## Before pushing

```bash
.venv/bin/python -m unittest discover -s tests -t tests
```

`deploy.sh` runs the same suite and aborts the restart if it fails, so a red
suite means the change never reaches the live bot. The suite takes ~30s: most
of it is simulating two years of hourly theme picks against the real catalog.
If a change touches post copy or judging, also look at a dry run:

```bash
.venv/bin/python tests/dryrun.py --rounds 8   # posts land in dry-run/
```

## Adding themes

Themes live in `themes/<category>.txt`: a `# Display Name` first line, then one
theme per line (Title Case, ≤40 characters, no trailing punctuation). Add to an
existing file or create a new category file, then run the tests —
`test_themes.py` rejects duplicates across *all* files (after folding case,
plurals and "the"), so a theme that would repeat under another name fails the
deploy instead of going out. The catalog should stay well above 4,000 themes;
`main.py --status` shows how many days of never-used themes remain.

To run a specific theme next: `.venv/bin/python main.py --queue "Lighthouses"`.

## Rules that are load-bearing

- **No theme repeats within 90 days, ever.** `config.check_repeat_days`
  refuses to start below 90, and `themes.choose` will raise
  `NoThemeAvailable` rather than bend the rule. Don't add an escape hatch.
- **Themes post only on the hour.** There is deliberately no catch-up tick at
  boot, and `misfire_grace_time` is five minutes: a Mini that wakes at 3:40
  waits for 4:00. Late entries getting less time to collect likes is intended.
- **Save state only after the post succeeds.** The round statuses
  (open → judged → announced) are what let a failed tick replay without
  scoring or announcing twice. `tests/test_tick.py` covers each failure point.
- **Likes exclude the author's own and the bot's.** Bluesky's `likeCount`
  includes both; `judging.resolve_likes` fetches exact likers for the entries
  that could still win.
- Post text: build, fit to 300, *then* compute facets — the offsets are
  UTF-8 bytes, not characters.
- `config.py` must be imported before anything reads a setting; it calls
  `load_dotenv()` at import time on purpose.
- Track DIDs, not handles, for scoring. Handles change.

## Operating the bot

```bash
launchctl list | grep punbattle                        # is it running
launchctl kickstart -k gui/$(id -u)/com.punbattle.bot  # restart
tail -f punbattle.log                                  # watch it
.venv/bin/python main.py --status                    # current round, live entries
.venv/bin/python main.py --leaderboard 20            # all-time standings
.venv/bin/python main.py --preview 48                # the next two days of themes
.venv/bin/python main.py --queue "Theme"             # run this theme next
.venv/bin/python main.py --skip                      # replace a bad theme, unscored
.venv/bin/python reset.py --dry-run --all            # see what a wipe would do
.venv/bin/python tools/profile.py --dry-run          # profile + pinned rules copy
```

If you change the rules (points, round length, likes), update the copy in
`tools/profile.py` and re-run it with `--repost` so the pinned post matches.
`reset.py --posts` deletes *every* post, the pinned rules thread included —
re-run `tools/profile.py` afterwards.

`--skip`, `--tick` and `--judge-now` post immediately, off the hour — they are
for fixing problems and testing, not for normal operation.
