"""MiMo Balance Checker - auto-detect login completion."""

import json
import re
import os
from pathlib import Path

COOKIE_FILE = Path(__file__).parent / "mimo_cookies.json"
BALANCE_URL = "https://platform.xiaomimimo.com/#/console/balance"


def find_browser():
    from pathlib import Path
    home = Path.home()
    for p in [
        home / "AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe",
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    ]:
        if os.path.exists(p):
            return p
    return None


def is_logged_in(page):
    """Check if we're on the actual balance page, not the login page."""
    url = page.url
    # 登录页在 account.xiaomi.com，余额页在 platform.xiaomimimo.com
    if "account.xiaomi.com" in url:
        return False
    if "platform.xiaomimimo.com" in url and "login" not in url:
        return True
    return False


def scrape_balance(page):
    """Extract balance from page text."""
    page.wait_for_timeout(3000)
    results = {}
    try:
        text = page.inner_text("body")
        results["page_text"] = text[:3000]

        patterns = [
            r'余额[：:\s]*[¥￥]?\s*([\d,.]+)',
            r'总余额[：:\s]*[¥￥]?\s*([\d,.]+)',
            r'充值余额[：:\s]*[¥￥]?\s*([\d,.]+)',
            r'赠送余额[：:\s]*[¥￥]?\s*([\d,.]+)',
            r'([\d,.]+)\s*元',
        ]
        for pat in patterns:
            matches = re.findall(pat, text, re.IGNORECASE)
            if matches:
                results.setdefault("balances", []).extend(matches)
    except Exception as e:
        results["error"] = str(e)

    try:
        ss = Path(__file__).parent / "mimo_balance_screenshot.png"
        page.screenshot(path=str(ss), full_page=True)
        results["screenshot"] = str(ss)
        print(f"截图: {ss}")
    except:
        pass

    return results


def load_cookies():
    """Load saved cookies."""
    if not COOKIE_FILE.exists():
        print(f"Cookie 文件不存在: {COOKIE_FILE}")
        return []
    return json.loads(COOKIE_FILE.read_text())


def query_with_cookies(cookies):
    """Use saved cookies to query balance (headless)."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        exe = find_browser()
        kw = {"executable_path": exe} if exe else {}
        browser = p.chromium.launch(headless=True, **kw)
        context = browser.new_context()
        context.add_cookies(cookies)
        page = context.new_page()
        page.goto(BALANCE_URL)
        page.wait_for_load_state("networkidle", timeout=30000)
        page.wait_for_timeout(3000)
        balance = scrape_balance(page)
        browser.close()
    return balance


def main():
    from playwright.sync_api import sync_playwright

    print("=" * 50)
    print("MiMo 余额查询")
    print("=" * 50)
    print("浏览器即将打开，请登录小米账号。")
    print("登录成功后脚本自动检测，最长等 5 分钟...\n")

    with sync_playwright() as p:
        exe = find_browser()
        kw = {"executable_path": exe} if exe else {}
        browser = p.chromium.launch(headless=False, **kw)
        context = browser.new_context()
        page = context.new_page()
        page.goto(BALANCE_URL)
        # Wait for redirect to complete before checking URL
        page.wait_for_timeout(3000)

        print("等待登录...")
        # 轮询检测：每2秒检查一次是否已登录
        import time
        start = time.time()
        timeout = 300  # 5分钟

        while time.time() - start < timeout:
            if is_logged_in(page):
                print("检测到已登录！")
                break
            page.wait_for_timeout(2000)
            elapsed = int(time.time() - start)
            if elapsed % 30 == 0 and elapsed > 0:
                print(f"  已等待 {elapsed}s ...")
        else:
            print("等待超时")

        # 等页面稳定
        page.wait_for_load_state("networkidle", timeout=30000)
        page.wait_for_timeout(5000)

        print(f"当前页面: {page.url}")
        balance = scrape_balance(page)
        cookies = context.cookies()
        browser.close()

    # 保存 cookies
    mimo = [c for c in cookies if "xiaomimimo" in c.get("domain", "") or "xiaomi" in c.get("domain", "")]
    if mimo:
        COOKIE_FILE.write_text(json.dumps(mimo, indent=2, ensure_ascii=False))
        print(f"\nCookie 已保存: {COOKIE_FILE} ({len(mimo)} 个)")

    print("\n" + "=" * 50)
    print("查询结果:")
    print("=" * 50)
    print(json.dumps(balance, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
