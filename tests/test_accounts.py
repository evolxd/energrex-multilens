"""Tests for account/accounts.py -- 账户注册表（编号+可编辑名字）。

Each test gets its own fresh temp DB (setUp, not setUpClass) because the
accounts table has no per-test scoping key to clean between tests the way
discipline_signals does via account_id -- sharing one DB across tests in
this file would make later tests order-dependent on how many accounts
earlier tests added.
"""
import pathlib
import shutil
import tempfile
import unittest

import account.db as account_db
from account import accounts as acc


class AccountsTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._original_db_path = account_db.DB_PATH
        account_db.DB_PATH = pathlib.Path(self._tmp.name) / "test_accounts.db"
        account_db.init_db()

    def tearDown(self):
        account_db.DB_PATH = self._original_db_path
        self._tmp.cleanup()

    def test_seed_creates_account_1_and_2_with_zero_padded_numbers(self):
        rows = acc.list_accounts()
        self.assertEqual([r["id"] for r in rows], ["account_1", "account_2"])
        self.assertEqual([r["number"] for r in rows], ["001", "002"])
        self.assertEqual([r["label"] for r in rows], ["账户一", "账户二"])

    def test_add_account_assigns_next_number_and_preserves_existing_ids(self):
        before = {r["id"] for r in acc.list_accounts()}
        new = acc.add_account("测试账户")
        self.assertEqual(new["id"], "account_3")
        self.assertEqual(new["number"], "003")
        self.assertEqual(new["label"], "测试账户")
        after_ids = {r["id"] for r in acc.list_accounts()}
        self.assertEqual(after_ids, before | {"account_3"})

    def test_add_account_default_label_when_none_given(self):
        new = acc.add_account()
        self.assertTrue(new["label"])

    def test_add_account_keeps_incrementing_past_ten(self):
        last = None
        for _ in range(9):
            last = acc.add_account()
        self.assertEqual(last["id"], "account_11")
        self.assertEqual(last["number"], "011")

    def test_rename_account_updates_label_not_id(self):
        rows = acc.list_accounts()
        target = rows[0]["id"]
        acc.rename_account(target, "老王的账户")
        updated = next(r for r in acc.list_accounts() if r["id"] == target)
        self.assertEqual(updated["label"], "老王的账户")

    def test_rename_account_rejects_empty_label(self):
        rows = acc.list_accounts()
        with self.assertRaises(ValueError):
            acc.rename_account(rows[0]["id"], "   ")

    def test_account_download_dir_derives_from_number_and_creates_dir(self):
        d = acc.account_download_dir("account_2")
        try:
            self.assertTrue(d.exists())
            self.assertTrue(d.name.endswith("_002"))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_archive_hides_from_default_list_but_not_include_archived(self):
        acc.archive_account("account_2")
        active = {r["id"] for r in acc.list_accounts()}
        self.assertNotIn("account_2", active)
        everyone = {r["id"] for r in acc.list_accounts(include_archived=True)}
        self.assertIn("account_2", everyone)

    def test_archive_does_not_touch_label_or_number(self):
        acc.archive_account("account_2")
        row = next(r for r in acc.list_accounts(include_archived=True)
                   if r["id"] == "account_2")
        self.assertEqual(row["label"], "账户二")
        self.assertEqual(row["number"], "002")

    def test_unarchive_restores_to_default_list(self):
        acc.archive_account("account_1")
        acc.unarchive_account("account_1")
        active = {r["id"] for r in acc.list_accounts()}
        self.assertIn("account_1", active)

    def test_archived_account_number_is_not_reused_by_add_account(self):
        acc.archive_account("account_2")
        new = acc.add_account()
        self.assertEqual(new["id"], "account_3")

    def test_archive_as_the_very_first_db_touch_still_seeds_first(self):
        """Regression: archive_account() used to call _ensure_table() (create
        table only) instead of _seed_if_empty() (create + seed). Called as
        the first touch on a brand-new DB, the UPDATE ran against an empty
        table (0 rows affected), then a later list_accounts() call seeded
        fresh is_active=1 rows -- silently undoing the archive."""
        acc.archive_account("account_2")   # first DB touch in this test
        active = {r["id"] for r in acc.list_accounts()}
        self.assertNotIn("account_2", active)

    def test_rename_as_the_very_first_db_touch_still_seeds_first(self):
        acc.rename_account("account_1", "冷启动改名")
        row = next(r for r in acc.list_accounts() if r["id"] == "account_1")
        self.assertEqual(row["label"], "冷启动改名")


if __name__ == "__main__":
    unittest.main()
