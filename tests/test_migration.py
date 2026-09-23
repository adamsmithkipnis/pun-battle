"""Upgrades never break an existing database.

In the Minesweeper bot a column added to the schema but not to the migration
list stalled the live bot for an hour. The guard: every column the code
expects must exist after init_db() on a database created by an older schema.
"""

import sqlite3
import unittest

from helpers import DbTestCase, config, db


def columns(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


class TestMigration(DbTestCase):
    def test_init_is_idempotent(self):
        db.init_db()
        db.init_db()

    def test_added_columns_appear_on_an_old_database(self):
        # Rebuild each table without its added columns, as an older release
        # would have created it, then let init_db migrate it.
        conn = sqlite3.connect(config.DB_PATH)
        for table, added in db._ADDED_COLUMNS.items():
            if not added:
                continue
            keep = columns(conn, table) - {name for name, _ in added}
            conn.execute(f"CREATE TABLE old_{table} AS SELECT "
                         f"{', '.join(sorted(keep))} FROM {table}")
            conn.execute(f"DROP TABLE {table}")
            conn.execute(f"ALTER TABLE old_{table} RENAME TO {table}")
        conn.commit()
        conn.close()

        db.init_db()
        conn = sqlite3.connect(config.DB_PATH)
        for table, added in db._ADDED_COLUMNS.items():
            for name, _ in added:
                self.assertIn(name, columns(conn, table), f"{table}.{name}")
        conn.close()

    def test_every_table_the_code_uses_exists(self):
        conn = sqlite3.connect(config.DB_PATH)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        self.assertTrue({"rounds", "entries", "awards", "theme_queue",
                         "posts"} <= tables)


if __name__ == "__main__":
    unittest.main()
