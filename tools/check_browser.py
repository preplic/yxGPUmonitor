#!/usr/bin/env python3
"""Exercise authentication, real charts and responsive views without logging secrets."""

import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlsplit, unquote

from playwright.sync_api import sync_playwright


def login(page, password):
    page.wait_for_function("() => !document.querySelector('button[type=submit]').disabled")
    page.locator("#username").fill("pcpan")
    page.locator("#password").fill(password)
    page.locator("button[type=submit]").click()
    page.locator("#app").wait_for(state="visible")


def session_record(page, action="inspect"):
    return page.evaluate("""async action => {
        const id = sessionStorage.getItem('yxgpu-session');
        const replacement = action === 'wrong-key' ? await crypto.subtle.generateKey(
            {name:'AES-GCM',length:256}, false, ['decrypt']) : null;
        const database = await new Promise((resolve,reject) => {
            const request=indexedDB.open('yxgpu-sessions',1);
            request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);
        });
        try { return await new Promise((resolve,reject) => {
            const task=database.transaction('sessions', action==='inspect'?'readonly':'readwrite');
            const store=task.objectStore('sessions');const request=store.get(id);let result;
            request.onsuccess=()=>{
                const record=request.result;
                if(!record) {result=null;return;}
                if(action==='expire') record.expiresAt=Date.now()-1;
                if(action==='corrupt') record.key='invalid';
                if(action==='wrong-key') record.key=replacement;
                if(action==='removed-user') record.user='not-an-allowed-account';
                if(action==='salt') record.salt='changed';
                if(action!=='inspect') store.put(record);
                result={id,expiresAt:record.expiresAt,fields:Object.keys(record).sort(),
                    cryptoKey:record.key instanceof CryptoKey,extractable:record.key.extractable,
                    usages:record.key.usages};
            };
            task.oncomplete=()=>resolve(result);task.onerror=task.onabort=()=>reject(task.error);
        }); } finally {database.close();}
    }""", action)


def assert_logged_out(page):
    page.wait_for_function("() => !document.querySelector('button[type=submit]').disabled")
    assert page.locator("#app").is_hidden()
    assert not page.locator("#main").inner_text()
    assert page.evaluate("sessionStorage.getItem('yxgpu-session')") is None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--browser", required=True)
    parser.add_argument("--config", default=str(Path.home() / ".config/yxgpu-monitor/publish.json"))
    parser.add_argument("--artifacts", default="artifacts")
    parser.add_argument("--proxy-from-env", action="store_true")
    args = parser.parse_args()
    private = json.loads(Path(args.config).read_text())
    artifacts = Path(args.artifacts)
    artifacts.mkdir(parents=True, exist_ok=True)
    errors = []
    checks = []
    proxy = None
    if args.proxy_from_env:
        address = next((os.environ[name] for name in ("https_proxy", "HTTPS_PROXY", "http_proxy", "HTTP_PROXY")
                        if os.environ.get(name)), None)
        if not address:
            parser.error("Selected proxy is not configured")
        parsed = urlsplit(address)
        proxy = {"server": "%s://%s:%d" % (parsed.scheme, parsed.hostname, parsed.port or 80)}
        if parsed.username:
            proxy.update(username=unquote(parsed.username), password=unquote(parsed.password or ""))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=args.browser, headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"], proxy=proxy)
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
        login(page, private["password"])
        record = session_record(page)
        assert record["cryptoKey"] and record["extractable"] is False
        assert record["usages"] == ["decrypt"]
        assert record["fields"] == ["expiresAt", "id", "key", "salt", "user"]
        for _ in range(2):
            page.reload(wait_until="networkidle")
            page.locator("#app").wait_for(state="visible")
            restored = session_record(page)
            assert restored["id"] == record["id"]
            assert restored["expiresAt"] == record["expiresAt"]
        checks.append("reload restores the non-extractable key without extending session expiry")
        updated = page.locator("#freshness")
        assert updated.inner_text().startswith("更新时间 ")
        assert updated.inner_text().endswith(" · 北京时间")
        assert updated.bounding_box()["x"] + updated.bounding_box()["width"] <= page.locator("#themeToggle").bounding_box()["x"]
        checks.append("the update timestamp appears immediately left of the theme toggle")
        other_tab = context.new_page()
        other_tab.goto(args.url, wait_until="networkidle")
        assert_logged_out(other_tab)
        other_tab.close()
        checks.append("a fresh tab cannot reuse another tab's saved session")
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
        assert page.locator('[data-view="proxy"]').count() == 0
        for view in ("users", "gpus", "events"):
            page.locator(f'[data-view="{view}"]').click()
            page.wait_for_timeout(120)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(artifacts / (view + "-desktop.png")), full_page=True)
        assert page.locator(".runtime-row").count() == 1
        assert "代理" not in page.locator("#app").inner_text()
        for source in ("gpu",):
            row = page.locator(f'[data-collector="{source}"]')
            assert row.get_attribute("data-status") == "good"
            assert row.locator(".runtime-state").inner_text() == "运行正常"
            assert row.locator(".runtime-uptime").inner_text() != "—"
            assert row.locator(".runtime-sample").inner_text() != "—"
        assert "北京时间" in page.locator(".runtime-section > .section-heading").inner_text()
        schedule = page.locator(".runtime-schedule").inner_text()
        assert "节点采样 10 秒" in schedule and "网站数据更新 15 分钟" in schedule
        assert "快照内最近成功采样" in page.locator(".runtime-row").inner_text()
        page.evaluate("location.hash='proxy'")
        page.wait_for_function("() => document.querySelector('#main h1')?.textContent === '集群概览'")
        page.locator('[data-view="events"]').click()
        runtime_cases = page.evaluate("""async () => {
            const {collectorRuntime,formatUptime}=await import(new URL('assets/runtime.js',location.href));
            const base={running:true,startedAt:9000,lastSample:9999,intervalSeconds:10,problems:[]};
            return {
                normal:collectorRuntime(base,10000,10000),
                stopped:collectorRuntime({...base,running:false,stoppedAt:9500},10000,10000),
                missingStop:collectorRuntime({...base,running:false},10000,10000),
                stale:collectorRuntime(base,10000,12200),
                lagging:collectorRuntime({...base,lastSample:9900},10000,10000),
                error:collectorRuntime({...base,problems:['error']},10000,10000),
                missing:collectorRuntime(null,10000,10000),
                missingStart:collectorRuntime({...base,startedAt:null},10000,10000),
                duration:formatUptime(90061)
            };
        }""")
        assert runtime_cases["normal"]["status"] == "good" and runtime_cases["normal"]["uptime"] == 1000
        assert runtime_cases["stopped"]["status"] == "bad" and runtime_cases["stopped"]["uptime"] == 500
        assert runtime_cases["missingStop"]["uptime"] is None
        assert runtime_cases["stale"]["status"] == "unknown" and runtime_cases["stale"]["uptime"] == 1000
        assert runtime_cases["lagging"]["status"] == "warn" and runtime_cases["error"]["status"] == "warn"
        assert runtime_cases["missing"]["status"] == "unknown" and runtime_cases["missingStart"]["uptime"] is None
        assert runtime_cases["duration"] == "1 天 1 时 1 分"
        page.locator("#eventFilter").select_option("start")
        page.locator('[data-range="168"]').click()
        assert page.locator("#eventFilter").input_value() == "start"
        page.locator("#eventFilter").select_option("all")
        checks.append("collector status, runtime, stale/stopped/error handling and event filters are correct")
        checks.append("four desktop views fit; proxy auditing is removed; sampling and publication are distinct")
        page.locator('[data-view="overview"]').click()
        page.locator("#themeToggle").click()
        page.wait_for_timeout(400)
        assert page.locator("html").get_attribute("data-theme") == "light"
        page.screenshot(path=str(artifacts / "overview-light.png"), full_page=True)
        page.locator('[data-view="events"]').click()
        page.screenshot(path=str(artifacts / "events-light.png"), full_page=True)
        page.locator('[data-view="overview"]').click()
        page.locator("#themeToggle").click()
        assert page.evaluate("JSON.stringify({...localStorage})").find(private["password"]) == -1
        assert page.evaluate("Object.keys(sessionStorage)") == ["yxgpu-session"]
        assert page.evaluate("JSON.stringify({...sessionStorage})").find(private["password"]) == -1
        assert page.evaluate("sessionStorage.getItem('yxgpu-session')") == record["id"]
        checks.append("theme switch works; Web Storage contains only theme and random session reference")
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(350)
        page.screenshot(path=str(artifacts / "overview-mobile.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        for view in ("gpus", "users", "events", "overview"):
            page.locator("#mobileMenu").click()
            page.locator(f'[data-view="{view}"]').click()
            page.wait_for_timeout(150)
            assert not page.locator("#sidebar").evaluate("el => el.classList.contains('open')")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            if view == "events":
                page.screenshot(path=str(artifacts / "events-mobile.png"), full_page=True)
                assert page.locator(".runtime-row").count() == 1
                assert page.locator(".runtime-stat").evaluate_all("elements => elements.every(el => el.scrollWidth <= el.clientWidth)")
        checks.append("mobile navigation and every view fit 390px")
        page.locator("#mobileMenu").click()
        page.locator("#logout").click()
        assert_logged_out(page)
        page.wait_for_function("""async id => {
            const db=await new Promise(resolve=>{const r=indexedDB.open('yxgpu-sessions',1);r.onsuccess=()=>resolve(r.result)});
            try{return await new Promise(resolve=>{const r=db.transaction('sessions').objectStore('sessions').get(id);r.onsuccess=()=>resolve(!r.result)})}
            finally{db.close()}
        }""", arg=record["id"])
        page.reload(wait_until="networkidle")
        assert_logged_out(page)
        page.screenshot(path=str(artifacts / "login-mobile.png"), full_page=True)
        checks.append("logout deletes the stored key and decrypted content; reload stays signed out")
        page.set_viewport_size({"width": 1440, "height": 1050})
        for action in ("expire", "corrupt", "wrong-key", "salt", "removed-user"):
            login(page, private["password"])
            session_record(page, action)
            page.reload(wait_until="networkidle")
            assert_logged_out(page)
        checks.append("expired, corrupt, wrong-key, changed-salt and removed-user sessions are invalidated")

        page.clock.install()
        login(page, private["password"])
        page.locator("#autoRefresh").uncheck()
        page.clock.fast_forward(12 * 60 * 60 * 1000 + 1000)
        assert_logged_out(page)
        page.clock.resume()
        checks.append("an open dashboard also locks when its 12-hour session expires")

        page.reload(wait_until="networkidle")
        login(page, private["password"])
        pending = []
        page.route("**/data/snapshot.enc.json", lambda route: pending.append(route))
        with page.expect_request("**/data/snapshot.enc.json"):
            page.locator("#refresh").click()
        page.locator("#logout").click()
        assert len(pending) == 1
        with page.expect_response("**/data/snapshot.enc.json"):
            pending[0].continue_()
        page.unroute("**/data/snapshot.enc.json")
        page.wait_for_timeout(300)
        assert_logged_out(page)
        checks.append("an in-flight refresh cannot restore data after logout")

        unavailable = browser.new_context(viewport={"width": 1440, "height": 1050})
        unavailable.add_init_script("Object.defineProperty(window,'indexedDB',{value:{open(){throw new Error('Storage disabled')}}})")
        fallback = unavailable.new_page()
        fallback.on("pageerror", lambda error: errors.append(str(error)))
        fallback.goto(args.url, wait_until="networkidle")
        login(fallback, private["password"])
        assert fallback.evaluate("sessionStorage.getItem('yxgpu-session')") is None
        assert fallback.locator("#toast").is_visible()
        fallback.reload(wait_until="networkidle")
        assert_logged_out(fallback)
        unavailable.close()
        checks.append("disabled browser storage still permits a temporary in-memory login")
        assert not errors, errors
        browser.close()
    report = {"checks": checks, "canvas": canvas, "javascript_errors": errors,
              "viewports": ["1440x1050", "390x844"]}
    (artifacts / "browser-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
