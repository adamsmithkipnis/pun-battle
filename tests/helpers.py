"""Shared test scaffolding.

Forces POST_MODE=dry *before* config is imported. load_dotenv() never
overrides a variable that is already set, so even on the Mini — where a real
.env with a real app password sits next to the tests — nothing here can post.
"""

import itertools
import logging
import os
import sys
import tempfile
import unittest

os.environ["POST_MODE"] = "dry"

# Failure-path tests log tracebacks on purpose; keep them out of the output.
logging.getLogger().addHandler(logging.NullHandler())
os.environ.setdefault("DRY_DIR", tempfile.mkdtemp(prefix="punbattle-dry-"))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config    # noqa: E402
import bluesky   # noqa: E402
import db        # noqa: E402
import judging   # noqa: E402
import posts     # noqa: E402
import themes    # noqa: E402
import main      # noqa: E402

BOT_DID = "did:plc:dryrun"
_serial = itertools.count(1)


def entry(did, likes=0, handle=None, text="a pun", at="2026-09-22T15:10:00.000Z",
          uri=None):
    """An Entry with a given raw like count."""
    handle = handle or f"{did.rsplit(':', 1)[-1]}.bsky.social"
    uri = uri or f"at://{did}/app.bsky.feed.post/{next(_serial)}"
    return judging.Entry(did=did, handle=handle, text=text, uri=uri,
                         cid="cid", created_at=at, indexed_at=at,
                         like_count=likes)


class DbTestCase(unittest.TestCase):
    """A fresh database file per test, and dry-run posting into a temp dir.

    config.DB_PATH is set directly: the env var was read at import time, so
    setting it here would be too late.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = (config.DB_PATH, config.DRY_DIR)
        config.DB_PATH = os.path.join(self._tmp.name, "test.db")
        config.DRY_DIR = os.path.join(self._tmp.name, "dry")
        db.init_db()
        bluesky.login()

    def tearDown(self):
        config.DB_PATH, config.DRY_DIR = self._saved
        self._tmp.cleanup()
