"""完整登录测试：找登录后的 URL、余额/持仓/历史页结构"""
import os, pathlib, time, sys

env = pathlib.Path(__file__).parent / ".env"
for line in env.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip()

USERNAME = os.environ.get("FIRSTRADE_USER_1", "")
PASSWORD = os.environ.get("FIRSTRADE_PASS_1", "")

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager

SS = pathlib.Path(__file__).parent / "data" / "screenshots"
SS.mkdir(parents=True, exist_ok=True)

def log(msg): print(msg, flush=True)
def ss(driver, name):
    driver.save_screenshot(str(SS / name))
    log(f"  [ss] {name}")

svc  = Service(ChromeDriverManager().install())
opts = Options()
opts.add_argument("--no-sandbox")
opts.add_argument("--disable-dev-shm-usage")
opts.add_argument("--window-size=1280,900")
driver = webdriver.Chrome(service=svc, options=opts)
wait   = WebDriverWait(driver, 20)

try:
    # ── 登录 ──────────────────────────────────────────────
    LOGIN_URL = "https://invest.firstrade.com/cgi-bin/login?ft_locale=en-us"
    log(f">>> {LOGIN_URL}")
    driver.get(LOGIN_URL)
    time.sleep(3)
    ss(driver, "10_login_page.png")
    log(f"  URL: {driver.current_url}")

    user_el = wait.until(EC.presence_of_element_located((By.ID, "username")))
    user_el.clear(); user_el.send_keys(USERNAME)
    pass_el = driver.find_element(By.ID, "password")
    pass_el.clear(); pass_el.send_keys(PASSWORD)
    ss(driver, "11_filled.png")

    # 点击 Login 按钮
    for by, sel in [
        (By.XPATH, "//button[normalize-space(text())='Login']"),
        (By.XPATH, "//button[@type='submit']"),
        (By.CSS_SELECTOR, "button[type='submit']"),
    ]:
        els = driver.find_elements(by, sel)
        if els:
            log(f"  Login btn: {els[0].text.strip()}")
            els[0].click()
            break

    log(">>> Waiting for redirect after login...")
    time.sleep(6)
    ss(driver, "12_after_login.png")
    log(f"  URL: {driver.current_url}")
    log(f"  Title: {driver.title}")

    # 检查 2FA
    src = driver.page_source.lower()
    if any(k in src for k in ["verification code", "security code", "two-factor", "enter the code"]):
        log(">>> 2FA detected! Please complete it in the browser window.")
        input("Press Enter here after completing 2FA in Chrome...")
        time.sleep(3)
        ss(driver, "13_after_2fa.png")
        log(f"  URL after 2FA: {driver.current_url}")

    # ── 登录后探测 ─────────────────────────────────────────
    log(f"\n=== Post-login page: {driver.current_url} ===")

    # 找所有导航链接（含 account/balance/position/history）
    log("\n--- Nav links ---")
    seen = set()
    for a in driver.find_elements(By.TAG_NAME, "a"):
        href = (a.get_attribute("href") or "").strip()
        txt  = (a.text or "").strip()
        if href and href not in seen:
            kws = ["balance", "position", "history", "account", "portfolio",
                   "holding", "transaction", "activity", "summary", "main"]
            if any(k in href.lower() for k in kws) or any(k in txt.lower() for k in kws):
                log(f"  [{txt[:35]}] -> {href[:100]}")
                seen.add(href)

    # ── 尝试导航到余额页 ───────────────────────────────────
    candidates = [
        "https://invest.firstrade.com/cgi-bin/main?page=accountsummary",
        "https://invest.firstrade.com/cgi-bin/main?page=balances",
        "https://invest.firstrade.com/cgi-bin/main?page=positions",
        "https://invest.firstrade.com/cgi-bin/main?page=history",
        "https://invest.firstrade.com/cgi-bin/main",
    ]
    for url in candidates:
        log(f"\n>>> GET {url}")
        driver.get(url)
        time.sleep(4)
        final_url = driver.current_url
        log(f"  Final URL: {final_url}")
        log(f"  Title: {driver.title}")
        name = url.split("page=")[-1].replace("/", "_")[:20] + ".png"
        ss(driver, f"30_{name}")

        # 打印所有 $ 相关文本
        body = driver.find_element(By.TAG_NAME, "body").text
        lines_with_dollar = [l.strip() for l in body.splitlines()
                              if "$" in l and len(l.strip()) < 80]
        if lines_with_dollar:
            log(f"  $ lines: {lines_with_dollar[:8]}")

        # 打印 h1/h2/h3 标题
        for htag in ["h1", "h2", "h3"]:
            for h in driver.find_elements(By.TAG_NAME, htag):
                if h.text.strip():
                    log(f"  <{htag}>: {h.text.strip()[:60]}")

finally:
    log("\nDone — check data/screenshots/")
    input("Press Enter to close Chrome...")
    driver.quit()
