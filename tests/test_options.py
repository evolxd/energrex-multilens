import pathlib
import tempfile
import unittest

import account.db as account_db
from account.options import option_market_value, parse_occ, parse_occ_sym
from account.options_repository import derive_open_options


class OptionsTests(unittest.TestCase):
    def test_parse_occ_separates_option_type_from_legacy_direction(self):
        parsed = parse_occ("QQQ260717P00350000")

        self.assertEqual(parsed["root"], "QQQ")
        self.assertEqual(parsed["expiry"], "2026-07-17")
        self.assertEqual(parsed["strike"], 350.0)
        self.assertEqual(parsed["option_type"], "put")
        self.assertEqual(parsed["call_put"], "P")
        self.assertEqual(parsed["direction"], "Put")

    def test_parse_occ_sym_uses_call_put_field(self):
        parsed = parse_occ_sym("PLTR260717C00150000")

        self.assertEqual(parsed["underlying"], "PLTR")
        self.assertEqual(parsed["call_put"], "C")
        self.assertEqual(parsed["strike"], 150.0)

    def test_option_market_value_is_signed(self):
        self.assertEqual(option_market_value(-2, 1.25), -250.0)
        self.assertEqual(option_market_value(3, 2.0), 600.0)

    def test_derive_open_options_keeps_position_side_and_option_type_separate(self):
        original_path = account_db.DB_PATH
        tmp = tempfile.TemporaryDirectory()
        try:
            account_db.DB_PATH = pathlib.Path(tmp.name) / "test.db"
            account_db.init_db()
            conn = account_db.db()
            conn.execute(
                """
                INSERT INTO options_positions
                  (account_id, symbol, direction, strike, expiry, quantity,
                   unit_cost, current_price, market_value)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    "acct",
                    "QQQ260717P00350000",
                    "short",
                    350,
                    "2026-07-17",
                    -2,
                    1.0,
                    1.25,
                    -250,
                ),
            )
            conn.commit()
            conn.close()

            row = derive_open_options("acct")[0]

            self.assertEqual(row["direction"], "short")
            self.assertEqual(row["option_type"], "put")
            self.assertEqual(row["call_put"], "P")
            self.assertEqual(row["market_value"], -250)
        finally:
            account_db.DB_PATH = original_path
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
