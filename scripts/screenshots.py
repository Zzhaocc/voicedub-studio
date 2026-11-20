"""控制台截图（开发辅助）。

    python scripts/screenshots.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

BASE = "http://127.0.0.1:8000"
OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots"
CHROME = os.getenv("CHROME_PATH") or str(
    Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright" / "chromium-1252" / "chrome-win64" / "chrome.exe"
)


def main() -> int:
    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME if Path(CHROME).exists() else None)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: errors.append(f"console: {m.text}") if m.type == "error" else None)

        page.goto(BASE, wait_until="networkidle")
        page.wait_for_timeout(2200)
        # 让工作台停在"多角色对白"这条任务上，展示时间轴与播放器
        page.evaluate(
            """() => {
                const items = [...document.querySelectorAll('.job-item')];
                const target = items.find((el) => el.textContent.includes('多角色对白'));
                if (target) target.click();
            }"""
        )
        page.wait_for_timeout(900)
        page.screenshot(path=str(OUT / "console-studio.jpg"), type="jpeg", quality=88)
        print("  ✓ 配音台 → console-studio.jpg")

        page.click('.tab[data-mode="dubbing"]')
        page.wait_for_timeout(400)
        page.evaluate(
            """() => {
                const items = [...document.querySelectorAll('.job-item')];
                const target = items.find((el) => el.textContent.includes('视频配音'));
                if (target) target.click();
            }"""
        )
        page.wait_for_timeout(800)
        page.screenshot(path=str(OUT / "console-dubbing.jpg"), type="jpeg", quality=88)
        print("  ✓ 视频配音 → console-dubbing.jpg")

        page.click('.tab[data-mode="audiobook"]')
        page.wait_for_timeout(400)
        page.evaluate(
            """() => {
                const items = [...document.querySelectorAll('.job-item')];
                const target = items.find((el) => el.textContent.includes('有声书'));
                if (target) target.click();
            }"""
        )
        page.wait_for_timeout(800)
        page.screenshot(path=str(OUT / "console-audiobook.jpg"), type="jpeg", quality=88)
        print("  ✓ 有声书 → console-audiobook.jpg")

        for view, name in [("voices", "音色库"), ("jobs", "任务"), ("metrics", "指标")]:
            page.click(f'.nav-item[data-view="{view}"]')
            page.wait_for_timeout(1200)
            page.screenshot(path=str(OUT / f"console-{view}.jpg"), type="jpeg", quality=88)
            print(f"  ✓ {name} → console-{view}.jpg")

        browser.close()

    if errors:
        print("\n浏览器错误：")
        for err in errors[:8]:
            print("  -", err)
        return 1
    print("\n截图完成，无浏览器错误")
    return 0


if __name__ == "__main__":
    sys.exit(main())
