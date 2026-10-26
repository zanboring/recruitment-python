# -*- coding: utf-8 -*-
"""首次登录引导：打开本机 Chrome 登录 BOSS，保存登录态供爬虫复用。

用法：
    .venv\\Scripts\\python.exe scripts/save_boss_login.py

步骤：
    1. 运行后弹出 Chrome 窗口
    2. 在窗口里手动登录 BOSS 直聘（扫码 / 手机验证码）
    3. 登录成功、能看到自己的账号后，回到命令行按回车
    4. 脚本把登录态保存到 data/boss_storage_state.json，后续爬虫自动复用

为什么这样做：BOSS 有强反爬，登录态（Cookie + localStorage）由真实 Chrome
自然维护，比手工复制的 Cookie 更完整、更不易过期。爬虫用 Playwright
``channel="chrome"`` 驱动同一个浏览器，带上这份登录态就是"真人已登录"的会话。
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def main() -> None:
    from playwright.async_api import async_playwright

    from app.config import settings

    state_path = Path(settings.crawl_storage_state)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    channel = settings.crawl_browser_channel or None
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel=channel, headless=False)
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()
        await page.goto("https://www.zhipin.com/web/user/?ka=header-login")

        print("\n" + "=" * 52)
        print("已打开 Chrome 窗口，请在窗口里登录 BOSS 直聘")
        print("（扫码 或 手机验证码，登录成功后能看到你的账号）")
        print("=" * 52)
        input("登录完成后回到这里，按回车保存登录态... ")

        await context.storage_state(path=str(state_path))
        print(f"\n✅ 登录态已保存到：{state_path}")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
