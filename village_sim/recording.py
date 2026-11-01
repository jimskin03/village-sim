"""Disk-backed replay frames with independent, stable export readers."""

import json
from pathlib import Path
import sqlite3
import tempfile
import uuid


class RecordingArchive:
    def __init__(self, state_path=None, saved=None):
        self.temporary = tempfile.TemporaryDirectory() if state_path is None else None
        self.path = (Path(self.temporary.name) / "recording.sqlite3" if self.temporary
                     else Path(str(state_path) + ".recording.sqlite3"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("CREATE TABLE IF NOT EXISTS frames (story TEXT, seq INTEGER, data TEXT, PRIMARY KEY(story, seq))")
        self.story = uuid.uuid4().hex
        self.count = 0
        self.failed = False
        if saved:
            if (not isinstance(saved, dict) or not isinstance(saved.get("id"), str)
                    or type(saved.get("count")) is not int or saved["count"] < 1
                    or type(saved.get("failed", False)) is not bool):
                self.close()
                raise ValueError("Invalid recording archive reference")
            self.story, self.count = saved["id"], saved["count"]
            self.failed = saved.get("failed", False)
            count = self.connection.execute(
                "SELECT count(*) FROM frames WHERE story=? AND seq<?", (self.story, self.count)).fetchone()[0]
            if count != self.count:
                self.close()
                raise ValueError("The recording archive is missing frames; restore the checkpoint and its .recording.sqlite3 file together")
            # A crash between archiving and checkpoint replacement can leave
            # extra frames. The atomic checkpoint is the committed boundary.
            with self.connection:
                self.connection.execute("DELETE FROM frames WHERE story=? AND seq>=?", (self.story, self.count))

    def reference(self):
        return {"id": self.story, "count": self.count, "failed": self.failed}

    def start(self, snapshot):
        previous = self.reference()
        self.story, self.count, self.failed = uuid.uuid4().hex, 0, False
        try:
            self.append(snapshot)
        except Exception:
            self.select(previous)
            raise
        return previous

    def select(self, reference):
        self.story, self.count, self.failed = reference["id"], reference["count"], reference["failed"]

    def append(self, snapshot):
        data = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
        with self.connection:
            self.connection.execute("INSERT INTO frames VALUES (?, ?, ?)", (self.story, self.count, data))
        self.count += 1

    def recent(self):
        rows = self.connection.execute(
            "SELECT data FROM frames WHERE story=? AND seq<? ORDER BY seq DESC LIMIT 256", (self.story, self.count))
        return list(reversed([json.loads(row[0]) for row in rows]))

    def reader(self):
        if self.failed:
            raise ValueError("The replay archive could not be written completely. Start a new story after checking disk space.")
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("BEGIN")
            cursor = connection.execute("SELECT data FROM frames WHERE story=? AND seq<? ORDER BY seq", (self.story, self.count))
            return connection, cursor
        except Exception:
            connection.close()
            raise

    def prune(self):
        # WAL readers retain their snapshot even if the live story is reset.
        with self.connection:
            self.connection.execute("DELETE FROM frames WHERE story<>?", (self.story,))

    def close(self):
        self.connection.close()
        if self.temporary:
            self.temporary.cleanup()
