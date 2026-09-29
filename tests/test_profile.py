"""The profile copy in tools/profile.py loads and fits Bluesky's limits.

The script only runs by hand on the Mini, so without this a typo in it would
first surface the day someone tries to repost the rules.
"""

import importlib.util
import os
import unittest

import helpers  # noqa: F401  (sets POST_MODE=dry and the import path)

PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "tools", "profile.py")


def load():
    spec = importlib.util.spec_from_file_location("profile_tool", PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestProfileCopy(unittest.TestCase):
    def test_copy_fits(self):
        profile = load()
        self.assertLessEqual(len(profile.DISPLAY_NAME), 64)
        self.assertLessEqual(len(profile.BIO), profile.BIO_LIMIT)
        self.assertLessEqual(len(profile.RULES), 300)
        self.assertLessEqual(len(profile.FINE_PRINT), 300)

    def test_rules_mention_the_current_mechanics(self):
        profile = load()
        self.assertIn("Mashup", profile.RULES)
        self.assertIn("Everyone who entered splits", profile.FINE_PRINT)


if __name__ == "__main__":
    unittest.main()
