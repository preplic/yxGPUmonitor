#!/usr/bin/env python3
"""Exercise authentication, real charts and responsive views without logging secrets."""

import argparse
import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--browser", required=True)
    parser.add_argument("--config", default=str(Path.home() / ".config/yxgpu-monitor/publish.json"))
    parser.add_argument("--artifacts", default="artifacts")
    args = parser.parse_args()
    private = json.loads(Path(args.config).read_text())
    artifacts = Path(args.artifacts)
    artifacts.mkdir(parents=True, exist_ok=True)
    errors = []
    checks = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=args.browser, headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = browser.new_context(viewport={"width": 1440, "height": 1050}, device_scale_factor=1)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(args.url, wait_until="networkidle")
        page.screenshot(path=str(artifacts / "login-desktop.png"), full_page=True)
        assert page.locator("#app").is_hidden()
        assert not page.locator("#main").inner_text()
        page.locator("#username").fill("pcpan")
        page.locator("#password").fill("deliberately-wrong-password")
        page.locator("button[type=submit]").click()
        page.wait_for_function("() => document.querySelector('#loginError').textContent.length > 0")
        assert page.locator("#app").is_hidden()
        checks.append("incorrect password cannot unlock data")
        page.locator("#username").fill("not-an-allowed-account")
        page.locator("#password").fill(private["password"])
        page.locator("button[type=submit]").click()
        page.wait_for_function("() => !document.querySelector('button[type=submit]').disabled")
        assert page.locator("#app").is_hidden()
        checks.append("unlisted username is rejected")
        page.locator("#username").fill("pcpan")
        page.locator("#password").fill(private["password"])
        page.locator("button[type=submit]").click()
        page.locator("#app").wait_for(state="visible")
        page.wait_for_timeout(600)
        assert page.locator(".gpu-card").count() == 8
        assert page.locator("#password").input_value() == ""
        canvas = page.evaluate("""() => { const canvas=document.querySelector('#occupancyChart canvas');
            const data=canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data;
            let painted=0;const colors=new Set();for(let i=0;i<data.length;i+=4){if(data[i+3]){painted++;colors.add([data[i],data[i+1],data[i+2]].join(','))}}
            return {painted, colors:colors.size, width:canvas.width,height:canvas.height}; }""")
        assert canvas["painted"] > 100 and canvas["colors"] > 5, canvas
        checks.append("real encrypted data decrypts and paints nonblank chart")
        page.screenshot(path=str(artifacts / "overview-desktop.png"), full_page=True)
        page.locator('[data-range="6"]').click()
        page.locator('[data-chart-mode="users"]').click()
        page.locator('[data-gpu="0"]').click()
        assert page.locator("#detailDialog").is_visible()
        page.locator("[data-close-dialog]").click()
        checks.append("time range, user chart and GPU detail work")
        page.locator('[data-view="users"]').click()
        page.locator("#userSearch").fill("no-such-user")
        assert page.locator(".empty-state").is_visible()
        page.locator("#userSearch").fill("")
        page.locator("[data-sort=uid]").click()
        page.locator("[data-user]").first.click()
        assert page.locator("#detailDialog canvas").count() == 1
        page.locator("[data-close-dialog]").click()
        with page.expect_download() as downloaded:
            page.locator("[data-export]").click()
        assert downloaded.value.suggested_filename.endswith(".csv")
        checks.append("user search, sorting, details and CSV export work")
        for view in ("users", "gpus", "proxy", "events"):
            page.locator(f'[data-view="{view}"]').click()
            page.wait_for_timeout(120)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(artifacts / (view + "-desktop.png")), full_page=True)
        checks.append("all five desktop views fit the viewport")
        page.locator('[data-view="overview"]').click()
        page.locator("#themeToggle").click()
        page.wait_for_timeout(400)
        assert page.locator("html").get_attribute("data-theme") == "light"
        page.screenshot(path=str(artifacts / "overview-light.png"), full_page=True)
        page.locator("#themeToggle").click()
        assert page.evaluate("JSON.stringify({...localStorage})").find(private["password"]) == -1
        assert page.evaluate("JSON.stringify({...sessionStorage})") == "{}"
        checks.append("theme switch works; no credentials in browser storage")
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(350)
        page.screenshot(path=str(artifacts / "overview-mobile.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        for view in ("gpus", "users", "proxy", "events", "overview"):
            page.locator("#mobileMenu").click()
            page.locator(f'[data-view="{view}"]').click()
            page.wait_for_timeout(150)
            assert not page.locator("#sidebar").evaluate("el => el.classList.contains('open')")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        checks.append("mobile navigation and every view fit 390px")
        page.locator("#mobileMenu").click()
        page.locator("#logout").click()
        assert page.locator("#app").is_hidden()
        assert not page.locator("#main").inner_text()
        page.screenshot(path=str(artifacts / "login-mobile.png"), full_page=True)
        checks.append("logout removes decrypted content")
        assert not errors, errors
        browser.close()
    report = {"checks": checks, "canvas": canvas, "javascript_errors": errors,
              "viewports": ["1440x1050", "390x844"]}
    (artifacts / "browser-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
