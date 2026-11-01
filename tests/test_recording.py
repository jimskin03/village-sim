import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import test_server
from village_sim.persistence import read_session, write_session
from village_sim.server import Session


def frames(session):
    connection, cursor = session.archive.reader()
    try:
        return [json.loads(row[0]) for row in cursor]
    finally:
        connection.close()


class ArchiveTests(unittest.TestCase):
    def test_all_frames_survive_restart_and_live_window_remains_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            session = Session(count=1, state_path=path)
            try:
                with session.condition:
                    for _ in range(270):
                        session.sim.step += 1
                        session.publish()
                expected = frames(session)
                self.assertEqual([s["step"] for s in expected], list(range(271)))
                self.assertEqual(len(session.recording), 256)
                checkpoint = read_session(path)
                # Simulate a frame committed just before a failed checkpoint save.
                session.archive.append(session.sim.snapshot())
            finally:
                session.close()
            write_session(path, checkpoint)
            restarted = Session(state_path=path)
            try:
                self.assertEqual(frames(restarted), expected)
                self.assertEqual(restarted.recording, expected[-256:])
                with restarted.condition:
                    restarted.sim.step += 1
                    restarted.publish()
                self.assertEqual(len(frames(restarted)), 272)
            finally:
                restarted.close()

    def test_export_reader_is_stable_during_append_reset_and_prune(self):
        session = Session(count=1)
        try:
            with session.condition:
                session.sim.tick()
                session.publish()
                connection, cursor = session.archive.reader()
                saved = session.checkpoint()
                session.sim.tick()
                session.publish()
                session.resume(saved)
                session.archive.prune()
            try:
                self.assertEqual([json.loads(row[0])["step"] for row in cursor], [0, 1])
            finally:
                connection.close()
            self.assertEqual([s["step"] for s in frames(session)], [1])
            with session.condition:
                with self.assertRaises(ValueError):
                    session.resume({})
            self.assertEqual([s["step"] for s in frames(session)], [1])
        finally:
            path = session.archive.path
            session.close()
            self.assertFalse(path.exists())


class RecordingHTTPTests(unittest.TestCase):
    setUp = test_server.ServerTests.setUp
    tearDown = test_server.ServerTests.tearDown
    request = test_server.ServerTests.request

    def test_export_contains_early_frames_and_resumable_checkpoint(self):
        with self.session.condition:
            for _ in range(270):
                self.session.sim.step += 1
                self.session.publish()
        recording = self.request("/api/recording")
        self.assertEqual([s["step"] for s in recording["snapshots"]], list(range(271)))
        self.assertEqual(recording["session"]["state"]["step"], 270)
        self.assertNotIn("recording", recording["session"])
        self.request("/api/control", {"action": "reset"})
        self.assertEqual(len(self.request("/api/recording")["snapshots"]), 1)

    def test_disk_failure_is_visible_and_never_silently_exports_partial_history(self):
        with patch.object(self.session.archive, "append", side_effect=sqlite3.OperationalError("disk full")):
            state = self.request("/api/control", {"action": "step"})
        self.assertIn("replay archive", state["persistence_error"])
        with self.assertRaises(HTTPError) as error:
            self.request("/api/recording")
        self.assertEqual(error.exception.code, 503)
        self.request("/api/control", {"action": "reset"})
        self.assertEqual(len(self.request("/api/recording")["snapshots"]), 1)
