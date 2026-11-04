"""登录态自检：BOSS 直聘服务端爬虫到底能不能用（对应缺陷 D-5 / 任务书 C4）。

**这个脚本回答三个问题**，每个都有明确判据，不靠人猜：

1. **登录态文件在不在、在哪个路径**
   `BossCrawler._launch_context` 读的是 ``settings.crawl_storage_state``
   （默认 ``data/boss_storage_state.json``，**相对路径** → 相对当前工作目录）。
   ``scripts/save_boss_login.py`` 写到的是同一个 setting，所以两边路径必然一致；
   但「相对 CWD」意味着**从别的目录启动就找不到** —— 本脚本会把绝对路径打出来。

2. **登录态本身有效吗**
   解析 storage_state 的 JSON 结构（cookies / origins），统计 Cookie 数量、
   域名、过期时间；再用 Playwright 真正 ``new_context(storage_state=...)`` 加载一次
   —— 能加载不代表能登录（BOSS 的 Cookie 过期后依然能被加载），
   所以还要看第 3 步。

3. **抓取链路通不通**
   用与爬虫**完全相同**的 URL 与选择器访问一次搜索页，记录最终 URL、
   页面标题、正文前 500 字符、是否命中岗位卡片。
   命中 → 通道正常；跳到登录页 / 扫码页 → **无登录态**（这就是 D-5 的现象）。

🔴 **红线**：本脚本**只读不写** —— 不入库、不伪造岗位、不改任何业务数据。
无登录态时它只产出诊断报告，绝不为了让任务「通过」而造数据。

用法：
    python scripts/check_boss_login.py
    python scripts/check_boss_login.py --keyword 应届 --city 长沙
"""
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows 控制台默认 GBK，打印「⚠️」这类符号会直接 UnicodeEncodeError 崩掉脚本。
# 自检脚本崩了就没有报告，所以先把 stdout 的编码错误降级为替换而不是抛异常。
try:
    sys.stdout.reconfigure(errors="replace")
except Exception:  # noqa: BLE001 - 非标准流（被重定向到别的编码）时忽略
    pass

from app.config import settings  # noqa: E402
from app.crawlers.boss import SELECTORS, find_all_elements  # noqa: E402
from app.crawlers.city_map import require_city_code  # noqa: E402


def _report_state_file() -> dict:
    """第 1 问：登录态文件在不在、结构是什么样、Cookie 有没有过期。"""
    raw = settings.crawl_storage_state
    path = Path(raw)
    absolute = path if path.is_absolute() else (Path.cwd() / path)

    info = {
        "configured_path": raw,
        "resolved_path": str(absolute.resolve()),
        "exists": path.exists(),
        "size_bytes": 0,
        "modified_at": "",
        "valid_json": False,
        "cookie_count": 0,
        "cookie_domains": [],
        "session_cookies": 0,
        "expired_cookies": 0,
        "latest_expiry": "",
        "origins": [],
        "local_storage_keys": 0,
        "problem": "",
    }
    if not path.exists():
        info["problem"] = (
            f"登录态文件不存在：{absolute} —— 服务端爬虫会以未登录身份访问，"
            "BOSS 会返回扫码登录页（就是 D-5 现象）"
        )
        return info

    stat = path.stat()
    info["size_bytes"] = stat.st_size
    info["modified_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime))

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        info["valid_json"] = True
    except Exception as e:  # noqa: BLE001
        info["problem"] = f"登录态文件不是合法 JSON：{e}"
        return info

    cookies = data.get("cookies") or []
    info["cookie_count"] = len(cookies)
    info["cookie_domains"] = sorted({c.get("domain", "") for c in cookies if c.get("domain")})

    now = time.time()
    expires_list = []
    expired = 0
    session = 0
    for cookie in cookies:
        expires = cookie.get("expires")
        if not expires or expires <= 0:      # expires=-1 表示会话级 Cookie
            session += 1
            continue
        expires_list.append(expires)
        if expires < now:
            expired += 1
    info["session_cookies"] = session
    info["expired_cookies"] = expired
    if expires_list:
        latest = max(expires_list)
        info["latest_expiry"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(latest))
    else:
        info["latest_expiry"] = "全部为会话级 Cookie（关闭浏览器即失效）"

    origins = data.get("origins") or []
    info["origins"] = [o.get("origin", "") for o in origins]
    info["local_storage_keys"] = sum(len(o.get("localStorage") or []) for o in origins)
    return info


async def _probe_browser(keyword: str, city: str, use_storage_state: bool) -> dict:
    """第 2、3 问：Playwright 能否加载登录态、页面到底返回什么。"""
    from bs4 import BeautifulSoup

    out = {
        "playwright_available": True,
        "browser_channel": settings.crawl_browser_channel or "(playwright 自带 chromium)",
        "browser_version": "",
        "context_loaded_storage_state": False,
        "storage_state_error": "",
        "request_url": "",
        "final_url": "",
        "page_title": "",
        "user_agent": "",
        "job_cards_found": 0,
        "selector_found": "",
        "blocked_signal": False,
        "page_markers": {},
        "first_500_chars": "",
        "network_error": "",
        "looks_like_login_page": False,
    }

    try:
        from playwright.async_api import async_playwright
    except ImportError as e:
        out["playwright_available"] = False
        out["network_error"] = f"Playwright 未安装：{e}"
        return out

    city_code = require_city_code(city)
    url = f"https://www.zhipin.com/web/geek/job?query={keyword}&city={city_code}&page=1"
    out["request_url"] = url

    state_path = Path(settings.crawl_storage_state)
    has_state = state_path.exists()

    try:
        async with async_playwright() as p:
            launch_kwargs = {"headless": True}   # 自检用无头，避免打扰用户
            if settings.crawl_browser_channel:
                launch_kwargs["channel"] = settings.crawl_browser_channel
            browser = await p.chromium.launch(**launch_kwargs)
            out["browser_version"] = browser.version

            ctx_kwargs = {
                "viewport": {"width": 1920, "height": 1080},
                "user_agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"
                ),
            }
            if use_storage_state and has_state:
                try:
                    # 与爬虫同一个入口：Playwright 能否接受这份 storage_state
                    test_ctx = await browser.new_context(storage_state=str(state_path))
                    await test_ctx.close()
                    out["context_loaded_storage_state"] = True
                except Exception as e:  # noqa: BLE001
                    out["storage_state_error"] = f"{type(e).__name__}: {e}"

            context = await browser.new_context(**ctx_kwargs)
            page = await context.new_page()
            try:
                await page.goto(url, timeout=settings.crawl_timeout * 1000)
            except Exception as e:  # noqa: BLE001
                out["network_error"] = f"{type(e).__name__}: {str(e)[:200]}"

            # **必须像爬虫一样等选择器**：BOSS 的列表是 JS 渲染的，
            # goto 之后立刻取 HTML 只会拿到空壳 —— 那时候「0 张卡片」
            # 说明不了任何问题。爬虫等 10 秒，这里等同样的时长。
            from app.crawlers.boss import BossCrawler
            try:
                found = await BossCrawler()._wait_for_any_selector(
                    page, SELECTORS["job_card"], timeout=15000
                )
                out["selector_found"] = found
            except Exception as e:  # noqa: BLE001
                out["selector_found"] = f"（等待 15 秒仍未出现任何岗位卡片容器：{type(e).__name__}）"

            out["final_url"] = page.url
            out["page_title"] = await page.title()
            try:
                out["user_agent"] = await page.evaluate("() => navigator.userAgent")
            except Exception:  # noqa: BLE001
                pass
            html = await page.content()
            out["first_500_chars"] = html[:500]
            out["blocked_signal"] = BossCrawler.detect_blocked(
                html, 200, out["final_url"], out["page_title"]
            )
            soup = BeautifulSoup(html, "html.parser")
            out["job_cards_found"] = len(find_all_elements(soup, SELECTORS["job_card"]))
            # 页面特征：只凭「0 张卡片」说不出到底拿到了什么页面，
            # 把关键标记逐个列出来，才能区分「登录页 / 风控页 / 空壳页」。
            out["page_markers"] = {
                "出现「登录」字样": "登录" in html,
                "出现「扫码」字样": "扫码" in html,
                "出现「安全验证」字样": ("安全验证" in html or "验证码" in html),
                "出现岗位卡片容器 job-card": "job-card" in html,
                "出现职位列表容器 job-list": "job-list" in html,
                "出现「暂无」字样": "暂无" in html,
                "页面字节数": len(html),
            }
            out["looks_like_login_page"] = (
                "登录" in out["page_title"] or "扫码" in html[:5000] or "register" in out["final_url"]
            )
            await browser.close()
    except Exception as e:  # noqa: BLE001
        out["network_error"] = f"{type(e).__name__}: {str(e)[:300]}"

    return out


async def main() -> None:
    parser = argparse.ArgumentParser(description="BOSS 登录态自检（只读，不入库）")
    parser.add_argument("--keyword", default="应届 Python", help="探测用关键词")
    parser.add_argument("--city", default="长沙", help="探测用城市")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = parser.parse_args()

    state = _report_state_file()
    probe = await _probe_browser(args.keyword, args.city, use_storage_state=True)

    # 结论：有登录态且抓到了岗位 → 通道可用；否则一律记「未验证 / 阻塞」
    if state["exists"] and probe["job_cards_found"] > 0:
        verdict = "登录态有效，采集通道可用"
    else:
        verdict = "未验证：没有可用登录态，采集通道阻塞（待用户扫码）"

    payload = {"state": state, "probe": probe, "verdict": verdict}

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print("=" * 60)
    print("BOSS 登录态自检报告")
    print("=" * 60)
    print("\n[1] 登录态文件")
    print(f"  配置路径        : {state['configured_path']}")
    print(f"  解析后的绝对路径: {state['resolved_path']}")
    print(f"  是否存在        : {state['exists']}"
          + (f"（{state['size_bytes']} 字节，修改于 {state['modified_at']}）" if state["exists"] else ""))
    if state["exists"]:
        print(f"  JSON 合法       : {state['valid_json']}")
        print(f"  Cookie 数       : {state['cookie_count']}（会话级 {state['session_cookies']}，已过期 {state['expired_cookies']}）")
        print(f"  Cookie 域名     : {', '.join(state['cookie_domains']) or '（无）'}")
        print(f"  最晚过期时间    : {state['latest_expiry']}")
        print(f"  origins         : {', '.join(state['origins']) or '（无）'}"
              f"（localStorage 项 {state['local_storage_keys']} 条）")
    if state["problem"]:
        print(f"  [!] {state['problem']}")

    print("\n[2] Playwright / 浏览器")
    print(f"  Playwright 可用 : {probe['playwright_available']}")
    print(f"  浏览器通道       : {probe['browser_channel']}")
    print(f"  浏览器版本       : {probe['browser_version'] or '（未启动）'}")
    print(f"  能加载登录态     : {probe['context_loaded_storage_state']}"
          + (f"（错误：{probe['storage_state_error']}）" if probe["storage_state_error"] else ""))

    print("\n[3] 访问搜索页（与爬虫同 URL、同选择器）")
    print(f"  请求 URL         : {probe['request_url']}")
    print(f"  最终 URL         : {probe['final_url'] or '（未取到）'}")
    print(f"  页面标题         : {probe['page_title'] or '（未取到）'}")
    print(f"  User-Agent       : {probe['user_agent'] or '（未取到）'}")
    print(f"  命中岗位卡片数   : {probe['job_cards_found']}")
    print(f"  等待到的选择器   : {probe.get('selector_found') or '（无）'}")
    print(f"  风控信号         : {probe.get('blocked_signal')}")
    if probe.get("page_markers"):
        print("  页面特征         :")
        for name, value in probe["page_markers"].items():
            print(f"    {name:<22}: {value}")
    if probe["network_error"]:
        print(f"  网络错误         : {probe['network_error']}")
    if probe["looks_like_login_page"]:
        print("  [!] 页面表现为登录/注册页 —— 无登录态的典型特征")
    print("  正文前 500 字符  :")
    for line in (probe["first_500_chars"] or "").splitlines()[:12]:
        print(f"    {line[:160]}")

    print(f"\n结论：{verdict}")


if __name__ == "__main__":
    asyncio.run(main())
