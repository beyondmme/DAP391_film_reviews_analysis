"""
Script debug — chạy khi pipeline thất bại để tìm đúng CSS selector.
Dùng wait_for_selector để đảm bảo content đã render xong trước khi phân tích.

Cách dùng:
    python debug_selectors.py
"""
import asyncio
from collections import Counter
from bs4 import BeautifulSoup

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"

TARGETS = [
    {
        "name": "IMDb_Dune_2",
        "url": "https://www.imdb.com/title/tt15239678/reviews",
        "lang": "en-US",
        # Thử chờ element review xuất hiện
        "wait_selector": "[data-testid='review-card-parent'], div.lister-item, div.ipc-html-content-inner-div",
        "scroll_times": 6,
        "extra_wait": 5.0,
    },
    {
        "name": "Spiderum_Dune_2",
        "url": "https://spiderum.com/bai-dang/Review-Dune-Part-Two-2024-su-vi-dai-den-tu-su-gian-di-hv8",
        "lang": "vi-VN",
        "wait_selector": "div.content p, app-post-detail, div.post-details",
        "scroll_times": 4,
        "extra_wait": 6.0,   # Angular cần lâu hơn
    },
]

CANDIDATE_SELECTORS = [
    # IMDb
    "[data-testid='review-overflow']",
    "[data-testid='review-card-parent']",
    "div.ipc-html-content-inner-div",
    "div.text.show-more__control",
    "div.lister-item-content .text",
    "section.ipc-page-section p",
    # Spiderum / Angular
    "div.content p",
    "div.post-details p",
    "div.text p",
    "app-post-detail p",
    "div.ng-star-inserted p",
    "article p",
    "main p",
]

async def scrape(target: dict) -> str:
    from playwright.async_api import async_playwright, TimeoutError as PWTimeout

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await browser.new_context(
            user_agent=_UA,
            viewport={"width": 1280, "height": 900},
            locale=target["lang"],
        )
        await ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )
        page = await ctx.new_page()
        await page.route("**/*.{png,jpg,jpeg,gif,webp,svg,woff,woff2,ttf}", lambda r: r.abort())

        await page.goto(target["url"], wait_until="domcontentloaded", timeout=60000)

        # Chờ selector chính
        try:
            await page.wait_for_selector(target["wait_selector"], timeout=30000, state="attached")
            print(f"   ✅ wait_selector matched!")
        except PWTimeout:
            print(f"   ⚠ wait_selector timeout — tiếp tục...")

        # Scroll
        for i in range(target["scroll_times"]):
            pct = (i + 1) / target["scroll_times"]
            await page.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {pct})")
            await asyncio.sleep(1.2)

        import asyncio as _a
        await _a.sleep(target["extra_wait"])

        html = await page.content()
        await browser.close()
        return html

def analyze(html: str, name: str):
    soup = BeautifulSoup(html, "html.parser")
    print(f"\n{'='*70}")
    print(f"  {name}  |  HTML: {len(html):,} chars")
    print(f"{'='*70}")

    all_cls = []
    for tag in soup.find_all(True):
        all_cls.extend(tag.get("class", []))
    print("\n📌 Top 30 classes:")
    for cls, n in Counter(all_cls).most_common(30):
        print(f"   .{cls:<45} ({n}x)")

    print("\n📌 5 thẻ <p> dài nhất:")
    for p in sorted(soup.find_all("p"), key=lambda x: len(x.get_text()), reverse=True)[:5]:
        par = p.parent
        par_cls = " ".join(par.get("class", [])) if par else ""
        print(f"   <{par.name}.{par_cls}>")
        print(f"   ✏ {p.get_text(strip=True)[:200]}\n")

    print("📌 Selector test results:")
    any_hit = False
    for sel in CANDIDATE_SELECTORS:
        hits = [e for e in soup.select(sel) if len(e.get_text(strip=True)) > 50]
        if hits:
            print(f"   ✅ {len(hits):3d} hits  →  {sel}")
            print(f"           Sample: {hits[0].get_text(strip=True)[:120]}")
            any_hit = True
        else:
            print(f"   ❌   0 hits  →  {sel}")

    if not any_hit:
        print("\n   ⚠ Không có selector nào khớp!")
        print("   → Mở file debug HTML trong browser, F12, tìm phần review và copy selector")

    fname = f"debug_{name}.html"
    with open(fname, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n💾 Saved: {fname}")

async def main():
    for t in TARGETS:
        print(f"\n🔍 Fetching {t['name']}...")
        try:
            html = await scrape(t)
            analyze(html, t["name"])
        except Exception as e:
            print(f"❌ {e}")

asyncio.run(main())