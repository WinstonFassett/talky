"""Self-contained tests for talky_auth. No TTY required — prompts are mocked."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers to patch talky_auth's CREDS_DIR to a temp dir
# ---------------------------------------------------------------------------

def make_module(creds_dir: Path):
    """Re-import talky_auth with CREDS_DIR pointing at creds_dir."""
    import importlib
    import talky_auth
    importlib.reload(talky_auth)
    talky_auth.CREDS_DIR = creds_dir
    return talky_auth


class TestCredIO(unittest.TestCase):
    """Read / write / delete credential files."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.creds = Path(self.tmp.name)
        self.mod = make_module(self.creds)

    def tearDown(self):
        self.tmp.cleanup()

    def _provider(self, name="deepgram"):
        return next(p for p in self.mod.PROVIDERS if p["name"] == name)

    def test_read_missing(self):
        self.assertIsNone(self.mod._read_cred(self._provider()))

    def test_write_then_read(self):
        p = self._provider()
        self.mod._write_cred(p, "dg-secret")
        self.assertEqual(self.mod._read_cred(p), "dg-secret")

    def test_write_preserves_other_keys(self):
        p = self._provider()
        path = self.creds / p["file"]
        path.write_text(json.dumps({"other_key": "other_value", p["field"]: "old"}))
        self.mod._write_cred(p, "new")
        data = json.loads(path.read_text())
        self.assertEqual(data["other_key"], "other_value")
        self.assertEqual(data[p["field"]], "new")

    def test_delete_removes_file_when_empty(self):
        p = self._provider()
        self.mod._write_cred(p, "dg-secret")
        self.mod._delete_cred(p)
        self.assertFalse((self.creds / p["file"]).exists())

    def test_delete_preserves_file_with_other_keys(self):
        p = self._provider()
        path = self.creds / p["file"]
        path.write_text(json.dumps({"other_key": "val", p["field"]: "dg-secret"}))
        self.mod._delete_cred(p)
        data = json.loads(path.read_text())
        self.assertNotIn(p["field"], data)
        self.assertEqual(data["other_key"], "val")

    def test_delete_nonexistent(self):
        self.mod._delete_cred(self._provider())  # should not raise

    def test_mask_short(self):
        self.assertIn("••••••", self.mod._mask("ab"))

    def test_mask_long(self):
        result = self.mod._mask("dg-abcdefghijk")
        self.assertTrue(result.startswith("dg-abcde"))
        self.assertIn("••••••", result)

    def test_status_set(self):
        p = self._provider()
        self.mod._write_cred(p, "dg-secret")
        self.assertIn("✓", self.mod._status(p))

    def test_status_unset(self):
        self.assertIn("✗", self.mod._status(self._provider()))


class TestPromptConstruction(unittest.TestCase):
    """Prompt objects must not raise during construction (no TTY needed)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mod = make_module(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_select_accepts_separator(self):
        from InquirerPy import inquirer
        from InquirerPy.base.control import Choice
        from InquirerPy.separator import Separator

        choices = [Choice(p, self.mod._provider_label(p)) for p in self.mod.PROVIDERS]
        choices += [Separator(), Choice(None, "done")]
        # Must not raise
        inquirer.select(message="Select provider:", choices=choices)

    def test_fuzzy_rejects_separator(self):
        from InquirerPy import inquirer
        from InquirerPy.base.control import Choice
        from InquirerPy.separator import Separator

        choices = [Choice(p, "label") for p in self.mod.PROVIDERS]
        choices += [Separator(), Choice(None, "done")]
        with self.assertRaises(Exception):
            inquirer.fuzzy(message="Select provider:", choices=choices)


class TestFlowMocked(unittest.TestCase):
    """Full interactive flow with mocked prompts."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.creds = Path(self.tmp.name)
        self.mod = make_module(self.creds)

    def tearDown(self):
        self.tmp.cleanup()

    def _provider(self, name="deepgram"):
        return next(p for p in self.mod.PROVIDERS if p["name"] == name)

    def test_set_new_credential(self):
        p = self._provider("deepgram")
        with patch("talky_auth.inquirer") as mock_inq:
            mock_inq.select.return_value.execute.return_value = "Set"
            mock_inq.secret.return_value.execute.return_value = "dg-newkey"
            self.mod._handle_provider(p)
        self.assertEqual(self.mod._read_cred(p), "dg-newkey")

    def test_edit_existing_credential(self):
        p = self._provider("deepgram")
        self.mod._write_cred(p, "dg-old")
        with patch("talky_auth.inquirer") as mock_inq:
            mock_inq.select.return_value.execute.return_value = "Edit"
            mock_inq.secret.return_value.execute.return_value = "dg-new"
            self.mod._handle_provider(p)
        self.assertEqual(self.mod._read_cred(p), "dg-new")

    def test_delete_with_confirm(self):
        p = self._provider("deepgram")
        self.mod._write_cred(p, "dg-secret")
        with patch("talky_auth.inquirer") as mock_inq:
            mock_inq.select.return_value.execute.return_value = "Delete"
            mock_inq.confirm.return_value.execute.return_value = True
            self.mod._handle_provider(p)
        self.assertIsNone(self.mod._read_cred(p))

    def test_delete_cancelled(self):
        p = self._provider("deepgram")
        self.mod._write_cred(p, "dg-secret")
        with patch("talky_auth.inquirer") as mock_inq:
            mock_inq.select.return_value.execute.return_value = "Delete"
            mock_inq.confirm.return_value.execute.return_value = False
            self.mod._handle_provider(p)
        self.assertEqual(self.mod._read_cred(p), "dg-secret")

    def test_back_does_nothing(self):
        p = self._provider("deepgram")
        with patch("talky_auth.inquirer") as mock_inq:
            mock_inq.select.return_value.execute.return_value = "Back"
            self.mod._handle_provider(p)
        self.assertIsNone(self.mod._read_cred(p))

    def test_main_loop_exits_on_done(self):
        with patch("talky_auth.inquirer") as mock_inq:
            mock_inq.select.return_value.execute.return_value = None  # "done"
            self.mod.run_auth_tui()  # must return cleanly

    def test_main_loop_set_then_done(self):
        p = self._provider("cartesia")
        call_count = 0

        def select_side_effect(**kwargs):
            nonlocal call_count
            m = MagicMock()
            call_count += 1
            if call_count == 1:
                m.execute.return_value = p        # pick cartesia
            elif call_count == 2:
                m.execute.return_value = "Set"    # action
            else:
                m.execute.return_value = None     # done
            return m

        with patch("talky_auth.inquirer") as mock_inq:
            mock_inq.select.side_effect = select_side_effect
            mock_inq.secret.return_value.execute.return_value = "sk-cart-123"
            self.mod.run_auth_tui()

        self.assertEqual(self.mod._read_cred(p), "sk-cart-123")


if __name__ == "__main__":
    unittest.main(verbosity=2)
