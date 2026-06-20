"""Quick CDP test - run with: python -X utf8 test_cdp.py"""
import sys, time
sys.stdout.reconfigure(encoding="utf-8")

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

opts = Options()
opts.add_experimental_option("debuggerAddress", "localhost:9222")
driver = webdriver.Chrome(options=opts)
print("Attached. Handles:", len(driver.window_handles))

# Open new tab
driver.execute_script("window.open('');")
driver.switch_to.window(driver.window_handles[-1])
print("New tab opened")

driver.get("https://invest.firstrade.com/app/balance")
print("Navigating to balance page…")

for i in range(30):
    body = driver.execute_script(
        "return document.body ? (document.body.innerText || '') : '';")
    print(f"  {i*0.5:.1f}s  body_len={len(body)}")
    if len(body) > 300:
        break
    time.sleep(0.5)

body = driver.execute_script(
    "return document.body ? (document.body.innerText || '') : '';")
print("\nPage text (first 800 chars):")
print(body[:800])
print()

# Run balance JS (Firstrade 繁体中文界面)
js = r"""
var t = document.body ? (document.body.innerText || '') : '';
function findNth(label, n) {
  var idx = t.indexOf(label);
  if (idx < 0) return null;
  var s = t.slice(idx, idx + 400);
  var re = /[+\-]?\$?\s*[\d,]+\.?\d{0,2}/g;
  var found = [], m;
  while ((m = re.exec(s)) !== null) {
    var v = parseFloat(m[0].replace(/[$,\s]/g, ''));
    if (!isNaN(v)) { found.push(v); if (found.length >= n) break; }
  }
  return found.length >= n ? found[n - 1] : null;
}
return {
  total_equity:     findNth('账户总值', 1),
  day_pnl:          findNth('账户总值', 2),
  cash_balance:     findNth('现金结余', 1),
  margin_used:      findNth('融资结余', 1),
  margin_available: findNth('融资购买力', 1)
};
"""
result = driver.execute_script(js)
print("Balance JS result:", result)

driver.close()
driver.switch_to.window(driver.window_handles[0])
print("\nDone. Tab closed, back to original.")
