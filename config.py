"""Settings, read from .env at import time.

**Import this before anything reads a setting.** Modules read os.environ while
they are being imported, which happens before main() runs — so a load_dotenv()
call inside main() is too late and silently ignores .env entirely. Doing it
here, at module scope, is what makes that impossible.
"""

import os

from dotenv import load_dotenv

load_dotenv()

# Account.
HANDLE = os.environ.get("BLUESKY_HANDLE", "")
APP_PASSWORD = os.environ.get("BLUESKY_APP_PASSWORD", "")

# Rounds. A new theme goes up on the boundary and the previous one is judged
# at the same instant, so a round is exactly ROUND_MINUTES long. Boundaries are
# aligned to the local clock (":00", or ":00/:30" at 30), which is only
# possible when the length divides an hour evenly.
ROUND_MINUTES = int(os.environ.get("ROUND_MINUTES", "60"))
if ROUND_MINUTES <= 0 or 60 % ROUND_MINUTES:
    raise ValueError(f"ROUND_MINUTES must divide 60 evenly, got {ROUND_MINUTES}")

# Deadlines in posts ("most likes at 4:00 PM PDT wins") are shown in this zone,
# and the round boundaries follow its wall clock.
TIMEZONE = os.environ.get("TIMEZONE", "America/Los_Angeles")

# How late a tick may fire and still run. Beyond this the boundary is skipped
# rather than posted late, so a theme only ever appears on the hour: a Mini
# that wakes at 3:40 waits for 4:00 instead of posting immediately.
MISFIRE_GRACE_SECONDS = int(os.environ.get("MISFIRE_GRACE_SECONDS", "300"))

# Scoring. 60 splits evenly between 2, 3, 4, 5 or 6 tied winners, so shared
# wins are whole numbers in all but the rarest ties.
ROUND_POINTS = int(os.environ.get("ROUND_POINTS", "60"))

# A round needs at least this many likes (not counting the author's own) to
# have a winner. Without it, a round where nobody liked anything would hand
# 60 points to every entrant as a many-way tie at zero.
MIN_LIKES = int(os.environ.get("MIN_LIKES", "1"))

# Variety. No category comes back within CATEGORY_COOLDOWN rounds (24 = not
# twice in a day at hourly rounds), and no theme within THEME_REPEAT_DAYS.
CATEGORY_COOLDOWN = int(os.environ.get("CATEGORY_COOLDOWN", "24"))

# Ninety days is a hard floor, not a default: repeats inside three months are
# a product decision that was explicitly ruled out. Refusing to start is
# better than quietly running with a smaller window someone typed into .env.
MIN_THEME_REPEAT_DAYS = 90


def check_repeat_days(days: int) -> int:
    if days < MIN_THEME_REPEAT_DAYS:
        raise ValueError(
            f"THEME_REPEAT_DAYS={days} is below the {MIN_THEME_REPEAT_DAYS}-day "
            "minimum; themes must not repeat within three months")
    return days


THEME_REPEAT_DAYS = check_repeat_days(
    int(os.environ.get("THEME_REPEAT_DAYS", "120")))

# Mashup rounds: two topics from different families, one pun must cover
# both ("DENTISTRY + GEOLOGY"). About one round in four, and at least
# MASHUP_MIN_GAP solo rounds between two mashups. Harder than a solo topic,
# which is why they are a seasoning rather than the meal.
MASHUP_RATE = float(os.environ.get("MASHUP_RATE", "0.25"))
MASHUP_MIN_GAP = int(os.environ.get("MASHUP_MIN_GAP", "1"))

# A topic does not reappear in any form — alone or as half of a mashup —
# within this many days, so "Coffee" can't return a week later as
# "Coffee + Knitting". (Identical themes are still held to the 90-day floor.)
COMPONENT_SPACING_DAYS = int(os.environ.get("COMPONENT_SPACING_DAYS", "30"))

# Local hour whose post also carries the all-time top three, once a day.
LEADERBOARD_HOUR = int(os.environ.get("LEADERBOARD_HOUR", "12"))

DB_PATH = os.environ.get("DB_PATH", "punbattle.db")
LOG_PATH = os.environ.get("LOG_PATH", "")
THEMES_DIR = os.environ.get(
    "THEMES_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "themes"))

# 'live' posts to Bluesky. 'dry' writes every post to DRY_DIR instead and
# never touches the network, so rounds can be played through without
# credentials.
POST_MODE = os.environ.get("POST_MODE", "live")
DRY_DIR = os.environ.get("DRY_DIR", "dry-run")

# Hashtags — how people who don't follow the account find a round. Each theme
# post carries up to MAX_HASHTAGS, in priority order:
#
#   1. HASHTAG_ALWAYS, so the game is findable under its own name
#   2. one pun tag (HASHTAG_CORE), so every round reaches people browsing puns
#   3. the topic itself (#FilmNoir, #Cheetahs; both halves of a mashup), which
#      reaches people who follow that subject rather than puns
#   4. one tag for the topic's family (food -> #foodie, nature -> #wildlife…),
#      then any HASHTAG_BOOSTED tag that wins its coin flip
#   5. general wordplay/game tags, sampled fresh each post
#
# Tags are appended only while they still fit under 300 characters, so reach
# never pushes the theme or the result out of a post.
#
# The pools live here, in code, not in .env. A HASHTAG_POOL line left in the
# Mini's .env once pinned every post to "#PunBattle #puns"; versioned lists
# can't drift like that. main.py warns if the old keys are still set.
HASHTAG_ALWAYS = os.environ.get("HASHTAG_ALWAYS", "#PunBattle")
MAX_HASHTAGS = int(os.environ.get("MAX_HASHTAGS", "6"))

HASHTAG_CORE = ["#puns", "#wordplay", "#punny"]

# Tags that should show up more often than the general rotation gives them:
# tag -> chance per post. #dadjokes feeds pull from it automatically (e.g.
# the "Dad Jokes" feed collects every #dadjokes post), so it earns a slot.
HASHTAG_BOOSTED = {"#dadjokes": 0.75}

HASHTAG_POOL = (
    "#puns #pun #wordplay #punny #dadjokes #jokes #humor #funny #comedy "
    "#wordgames #WritingPrompt #bskygames #gamesky #playtogether"
).split()

FAMILY_HASHTAGS = {
    "food": ["#foodie", "#FoodSky", "#food"],
    "home": ["#home", "#DIY", "#cozy"],
    "nature": ["#nature", "#NatureSky", "#wildlife", "#outdoors"],
    "science": ["#science", "#SciSky", "#STEM"],
    "arts": ["#arts", "#culture", "#ArtSky"],
    "body": ["#health", "#wellness"],
    "work": ["#WorkLife", "#careers"],
    "play": ["#games", "#sports", "#fun"],
    "travel": ["#travel", "#TravelSky", "#geography"],
    "history-fantasy": ["#history", "#HistorySky", "#fantasy"],
}

# Keys from before the pools moved into code; still set means a stale .env.
LEGACY_HASHTAG_KEYS = [k for k in ("HASHTAG_POOL", "HASHTAG_COUNT")
                       if k in os.environ]

# The one tag on replies to individual winners.
REPLY_HASHTAG = os.environ.get("REPLY_HASHTAG", "#PunBattle")

SERVICE = "com.punbattle.bot"
