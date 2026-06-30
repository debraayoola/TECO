"""
utils/database.py
Async SQLite database layer — handles all persistent data for the bot.
"""

import aiosqlite
import os
from datetime import datetime

DB_PATH = os.getenv('DATABASE_PATH', 'data/bot.db')


class Database:
    def __init__(self):
        self.path = DB_PATH
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    # ─── Schema Setup ─────────────────────────────────────────────────────────

    async def initialize(self):
        async with aiosqlite.connect(self.path) as db:

            # Guild-level configuration
            await db.execute('''
                CREATE TABLE IF NOT EXISTS guild_config (
                    guild_id            INTEGER PRIMARY KEY,
                    prefix              TEXT    DEFAULT '!',
                    log_channel         INTEGER,
                    welcome_channel     INTEGER,
                    welcome_message     TEXT,
                    farewell_message    TEXT,
                    auto_role           INTEGER,
                    automod_enabled     INTEGER DEFAULT 1,
                    antiraid_enabled    INTEGER DEFAULT 0,
                    antispam_enabled    INTEGER DEFAULT 1,
                    anti_links          INTEGER DEFAULT 0,
                    anti_caps           INTEGER DEFAULT 0,
                    caps_threshold      INTEGER DEFAULT 70,
                    max_mentions        INTEGER DEFAULT 5,
                    max_messages        INTEGER DEFAULT 5,
                    spam_interval       INTEGER DEFAULT 5,
                    created_at          TEXT    DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Member warnings
            await db.execute('''
                CREATE TABLE IF NOT EXISTS warnings (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id        INTEGER NOT NULL,
                    user_id         INTEGER NOT NULL,
                    moderator_id    INTEGER NOT NULL,
                    reason          TEXT    NOT NULL,
                    timestamp       TEXT    DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Banned words per guild
            await db.execute('''
                CREATE TABLE IF NOT EXISTS banned_words (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id    INTEGER NOT NULL,
                    word        TEXT    NOT NULL,
                    added_by    INTEGER NOT NULL,
                    timestamp   TEXT    DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(guild_id, word)
                )
            ''')

            # Moderation action log
            await db.execute('''
                CREATE TABLE IF NOT EXISTS mod_logs (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id        INTEGER NOT NULL,
                    action          TEXT    NOT NULL,
                    target_id       INTEGER,
                    moderator_id    INTEGER,
                    reason          TEXT,
                    timestamp       TEXT    DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Moderator notes on users
            await db.execute('''
                CREATE TABLE IF NOT EXISTS user_notes (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id        INTEGER NOT NULL,
                    user_id         INTEGER NOT NULL,
                    moderator_id    INTEGER NOT NULL,
                    note            TEXT    NOT NULL,
                    timestamp       TEXT    DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Spam offense tracker
            await db.execute('''
                CREATE TABLE IF NOT EXISTS spam_records (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id        INTEGER NOT NULL,
                    user_id         INTEGER NOT NULL,
                    offense_count   INTEGER DEFAULT 0,
                    last_offense    TEXT    DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(guild_id, user_id)
                )
            ''')

            await db.commit()
        print('✅ Database initialized')

    async def initialize_guild(self, guild_id: int):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                'INSERT OR IGNORE INTO guild_config (guild_id) VALUES (?)',
                (guild_id,)
            )
            await db.commit()

    # ─── Guild Config ─────────────────────────────────────────────────────────

    async def get_guild_config(self, guild_id: int) -> dict:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                'SELECT * FROM guild_config WHERE guild_id = ?', (guild_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return dict(row)
                await self.initialize_guild(guild_id)
                return await self.get_guild_config(guild_id)

    async def update_guild_config(self, guild_id: int, **kwargs):
        if not kwargs:
            return
        set_clause = ', '.join(f'{k} = ?' for k in kwargs)
        values = list(kwargs.values()) + [guild_id]
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                f'UPDATE guild_config SET {set_clause} WHERE guild_id = ?', values
            )
            await db.commit()

    # ─── Warnings ─────────────────────────────────────────────────────────────

    async def add_warning(self, guild_id: int, user_id: int, moderator_id: int, reason: str) -> int:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                'INSERT INTO warnings (guild_id, user_id, moderator_id, reason) VALUES (?, ?, ?, ?)',
                (guild_id, user_id, moderator_id, reason)
            )
            await db.commit()
            return cursor.lastrowid

    async def get_warnings(self, guild_id: int, user_id: int) -> list:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                'SELECT * FROM warnings WHERE guild_id = ? AND user_id = ? ORDER BY timestamp DESC',
                (guild_id, user_id)
            ) as cursor:
                return [dict(r) for r in await cursor.fetchall()]

    async def get_warning_count(self, guild_id: int, user_id: int) -> int:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                'SELECT COUNT(*) FROM warnings WHERE guild_id = ? AND user_id = ?',
                (guild_id, user_id)
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0

    async def clear_warnings(self, guild_id: int, user_id: int) -> int:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                'DELETE FROM warnings WHERE guild_id = ? AND user_id = ?',
                (guild_id, user_id)
            )
            await db.commit()
            return cursor.rowcount

    # ─── Banned Words ─────────────────────────────────────────────────────────

    async def add_banned_word(self, guild_id: int, word: str, added_by: int):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                'INSERT OR IGNORE INTO banned_words (guild_id, word, added_by) VALUES (?, ?, ?)',
                (guild_id, word.lower(), added_by)
            )
            await db.commit()

    async def remove_banned_word(self, guild_id: int, word: str) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                'DELETE FROM banned_words WHERE guild_id = ? AND word = ?',
                (guild_id, word.lower())
            )
            await db.commit()
            return cursor.rowcount > 0

    async def get_banned_words(self, guild_id: int) -> list:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                'SELECT word FROM banned_words WHERE guild_id = ?', (guild_id,)
            ) as cursor:
                return [row[0] for row in await cursor.fetchall()]

    # ─── Mod Logs ─────────────────────────────────────────────────────────────

    async def add_mod_log(self, guild_id: int, action: str,
                          target_id: int = None, moderator_id: int = None, reason: str = None):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                'INSERT INTO mod_logs (guild_id, action, target_id, moderator_id, reason) VALUES (?, ?, ?, ?, ?)',
                (guild_id, action, target_id, moderator_id, reason)
            )
            await db.commit()

    # ─── User Notes ───────────────────────────────────────────────────────────

    async def add_note(self, guild_id: int, user_id: int, moderator_id: int, note: str) -> int:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                'INSERT INTO user_notes (guild_id, user_id, moderator_id, note) VALUES (?, ?, ?, ?)',
                (guild_id, user_id, moderator_id, note)
            )
            await db.commit()
            return cursor.lastrowid

    async def get_notes(self, guild_id: int, user_id: int) -> list:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                'SELECT * FROM user_notes WHERE guild_id = ? AND user_id = ? ORDER BY timestamp DESC',
                (guild_id, user_id)
            ) as cursor:
                return [dict(r) for r in await cursor.fetchall()]

    # ─── Spam Records ─────────────────────────────────────────────────────────

    async def increment_offense(self, guild_id: int, user_id: int) -> int:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                'SELECT offense_count FROM spam_records WHERE guild_id = ? AND user_id = ?',
                (guild_id, user_id)
            ) as cursor:
                row = await cursor.fetchone()

            if row:
                count = row[0] + 1
                await db.execute(
                    'UPDATE spam_records SET offense_count = ?, last_offense = CURRENT_TIMESTAMP WHERE guild_id = ? AND user_id = ?',
                    (count, guild_id, user_id)
                )
            else:
                count = 1
                await db.execute(
                    'INSERT INTO spam_records (guild_id, user_id, offense_count) VALUES (?, ?, 1)',
                    (guild_id, user_id)
                )

            await db.commit()
            return count

    async def reset_offense(self, guild_id: int, user_id: int):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                'UPDATE spam_records SET offense_count = 0 WHERE guild_id = ? AND user_id = ?',
                (guild_id, user_id)
            )
            await db.commit()
