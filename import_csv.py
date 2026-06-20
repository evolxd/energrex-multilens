"""导入 data/firstrade/latest.csv → SQLite energrex.db"""
import pathlib, sqlite3, datetime, re, sys
import pandas as pd
import pytz

_ROOT    = pathlib.Path(__file__).parent
_DB_PATH = _ROOT / "data" / "energrex.db"
_CSV     = _ROOT / "data" / "firstrade" / "latest.csv"
_ET      = pytz.timezone("America/New_York")
ACCT_ID  = "account_1"

ZH_ALIAS = {
    "trade date":  "日期",
    "date":        "日期",
    "action":      "交易类别",
    "type":        "交易类别",
    "quantity":    "数量",
    "description": "说明",
    "symbol":      "代号",
    "price":       "价格",
    "amount":      "金额",
}
ZH_TYPE_MAP = {
    "卖出开仓": "SELL TO OPEN",
    "买进开仓": "BUY TO OPEN",
    "卖出平仓": "SELL TO CLOSE",
    "买进平仓": "BUY TO CLOSE",
    "卖出":     "SELL",
    "买进":     "BUY",
    "股息":     "DIVIDEND",
    "利息收入": "INTEREST",
    "利息":     "INTEREST",
    "其他":     "OTHER",
    "手续费":   "FEE",
    "交易费":   "FEE",
    "存入":     "DEPOSIT",
    "取出":     "WITHDRAWAL",
    "转入":     "TRANSFER IN",
    "转出":     "TRANSFER OUT",
    "到期":     "EXPIRED",
    "期权到期": "OPTION EXPIRED",
    "行权":     "EXERCISE",
    "被行权":   "ASSIGNED",
}

def _parse_date(s: str) -> str:
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.datetime.strptime(s.strip(), fmt).strftime("%Y-%m-%d")
        except Exception:
            pass
    return ""

def _parse_money(s: str):
    try:
        return float(re.sub(r"[,$\s]", "", s or ""))
    except Exception:
        return None

def load_csv(path: pathlib.Path) -> pd.DataFrame:
    for enc in ("utf-8-sig", "gbk", "utf-8", "latin-1"):
        try:
            df = pd.read_csv(path, encoding=enc, thousands=",")
            print(f"  编码: {enc}  行: {len(df)}  列: {list(df.columns)}")
            return df.dropna(how="all")
        except Exception as e:
            print(f"  {enc} 失败: {e}")
    return pd.DataFrame()

def parse_rows(df: pd.DataFrame) -> list[dict]:
    col = {c.lower().strip(): c for c in df.columns}

    def _get(row, *keys):
        for k in keys:
            if k in col:
                v = row.get(col[k], "")
                if pd.notna(v) and str(v).strip():
                    return str(v).strip()
            zh = ZH_ALIAS.get(k)
            if zh and zh in df.columns:
                v = row.get(zh, "")
                if pd.notna(v) and str(v).strip():
                    return str(v).strip()
        return ""

    rows = []
    for _, row in df.iterrows():
        td      = _parse_date(_get(row, "trade date", "run date", "date"))
        raw_typ = _get(row, "action", "type", "activity type")
        typ     = ZH_TYPE_MAP.get(raw_typ.strip(), raw_typ.upper())
        sym     = _get(row, "symbol", "ticker").upper()
        desc    = _get(row, "description")
        qty     = _parse_money(_get(row, "quantity", "shares"))
        prc     = _parse_money(_get(row, "price"))
        amt     = _parse_money(_get(row, "amount"))
        if not td and not typ:
            continue
        rows.append({
            "trade_date": td, "settlement_date": "",
            "type": typ, "symbol": sym, "description": desc,
            "quantity": qty, "price": prc, "amount": amt,
        })
    return rows

def save_to_db(rows: list[dict], acct_id: str) -> int:
    conn = sqlite3.connect(str(_DB_PATH))
    inserted = 0
    for r in rows:
        cur = conn.execute("""
            INSERT OR IGNORE INTO transactions
            (account_id,trade_date,settlement_date,type,symbol,
             description,quantity,price,amount)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (acct_id, r["trade_date"], r["settlement_date"],
              r["type"], r["symbol"], r["description"],
              r["quantity"], r["price"], r["amount"]))
        inserted += cur.rowcount
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM transactions WHERE account_id=?", (acct_id,)).fetchone()[0]
    conn.close()
    return inserted, total

def main():
    print(f"CSV: {_CSV}")
    if not _CSV.exists():
        print("文件不存在！")
        sys.exit(1)

    df = load_csv(_CSV)
    if df.empty:
        print("无法读取 CSV")
        sys.exit(1)

    zh_match = set(df.columns) & {"日期", "交易类别", "金额", "说明", "代号"}
    print(f"  中文列匹配: {zh_match}")

    rows = parse_rows(df)
    print(f"  解析行数: {len(rows)}")

    if not rows:
        print("无有效数据行")
        sys.exit(1)

    # 预览前 5 行
    for r in rows[:5]:
        print(f"    {r['trade_date']}  {r['type']:<18}  {r['symbol']:<25}  qty={r['quantity']}  amt={r['amount']}")

    inserted, total = save_to_db(rows, ACCT_ID)
    print(f"\n写入完成: 新增 {inserted} 行 / 表中合计 {total} 行")

if __name__ == "__main__":
    main()
