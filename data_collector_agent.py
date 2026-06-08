"""
=============================================================
  DAP391 — Cinematic Perception Gap
  Data Collection Pipeline (Stage 1 only)
  
  Sources:
    - IMDb          (English reviews)      → Playwright
    - Spiderum      (Vietnamese articles)  → Playwright  
    - Facebook      (Vietnamese comments)  → Facebook Graph API
  
  Output:
    - raw_reviews.csv   (local, luôn lưu)
    - Google Sheets     (optional, nếu có credentials)
  
  Cài đặt:
    pip install playwright beautifulsoup4 httpx gspread google-auth python-dotenv
    playwright install chromium
=============================================================
"""

import os, asyncio, csv, json, time, random, httpx
from datetime import datetime
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────
# CẤU HÌNH — chỉ cần sửa phần này
# ─────────────────────────────────────────────

# Danh sách phim cần cào (thêm/bớt tùy ý)
FILMS = [
    {
        "name": "Dune_Part_Two",
        "imdb_url":    "https://www.imdb.com/title/tt15239678/reviews",
        "spiderum_urls": [
            # Tìm bài review trên spiderum.com, paste URL vào đây
            # VD: "https://spiderum.com/bai-dang/Review-Dune-2-abc123"
        ],
        "fb_post_ids": [
            # Lấy post_id từ URL Facebook: fb.com/groups/xxx/posts/POST_ID
            # VD: "1234567890"
        ],
    },
    # Thêm phim khác ở đây...
]

SAMPLE_SIZE = 30          # Số review tối đa mỗi nguồn mỗi phim
OUTPUT_CSV  = "raw_reviews.csv"

# Google Sheets (để trống nếu không dùng)
GSHEET_ID          = os.getenv("GSHEET_ID", "")            # ID từ URL sheet
GSHEET_CREDENTIALS = os.getenv("GSHEET_CREDENTIALS", "credentials.json")  # File JSON từ GCP

# Facebook Graph API (để trống nếu không dùng)
FB_ACCESS_TOKEN = os.getenv("FB_ACCESS_TOKEN", "")

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

# ─────────────────────────────────────────────
# PLAYWRIGHT CORE — dùng chung cho IMDb & Spiderum
# ─────────────────────────────────────────────

async def playwright_fetch(url: str, wait_selector: str, lang: str = "en-US",
                            scroll_times: int = 5, extra_wait: float = 3.0) -> str:
    """
    Mở URL bằng headless Chromium, chờ content render xong rồi trả về HTML.
    Dùng wait_for_selector thay vì networkidle — chính xác hơn cho SPA.
    """
    from playwright.async_api import async_playwright, TimeoutError as PWTimeout

    print(f"   🌐 Opening: {url[:80]}...")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        ctx = await browser.new_context(
            user_agent=_UA,
            viewport={"width": 1280, "height": 900},
            locale=lang,
        )
        # Ẩn dấu hiệu automation
        await ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page = await ctx.new_page()
        # Bỏ qua ảnh/font để tải nhanh hơn
        await page.route("**/*.{png,jpg,jpeg,gif,webp,svg,woff,woff2,ttf,eot,ico}",
                         lambda r: r.abort())

        await page.goto(url, wait_until="domcontentloaded", timeout=60000)

        # Chờ element nội dung xuất hiện (quan trọng — không dùng networkidle)
        try:
            await page.wait_for_selector(wait_selector, timeout=25000, state="attached")
            print(f"   ✔ Content loaded (selector matched)")
        except PWTimeout:
            print(f"   ⚠ Selector timeout — proceeding with scroll...")

        # Scroll từ từ để trigger lazy-load
        for i in range(scroll_times):
            pct = (i + 1) / scroll_times
            await page.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {pct:.2f})")
            await asyncio.sleep(1.2)

        await asyncio.sleep(extra_wait)   # Angular/React cần thêm thời gian

        html = await page.content()
        await browser.close()

    print(f"   📄 HTML: {len(html):,} chars")
    return html


# ─────────────────────────────────────────────
# SOURCE 1: IMDb (English reviews)
# ─────────────────────────────────────────────

async def scrape_imdb(film: dict) -> list[dict]:
    """
    Cào review từ trang /reviews của IMDb.
    Trả về list các dict: {film, source, language, text, url}
    """
    url = film.get("imdb_url")
    if not url:
        return []

    print(f"\n[IMDb] {film['name']}")
    try:
        html = await playwright_fetch(
            url=url,
            # IMDb 2024 dùng data-testid cho review cards
            wait_selector="[data-testid='review-card-parent'], div.lister-item, div.ipc-html-content-inner-div",
            lang="en-US",
            scroll_times=6,
            extra_wait=4.0,
        )
    except Exception as e:
        print(f"   ❌ Scrape failed: {e}")
        return []

    soup = BeautifulSoup(html, "html.parser")
    results = []

    # Selector ladder: IMDb thay đổi HTML nhiều lần — thử từ mới → cũ
    selector_ladder = [
        "[data-testid='review-overflow']",
        "div.ipc-html-content-inner-div",
        "[data-testid='review-card-parent']",
        "div.text.show-more__control",
        "div.lister-item-content .text",
        "section.ipc-page-section p",
    ]

    reviews_els = []
    matched = None
    for sel in selector_ladder:
        els = soup.select(sel)
        els = [e for e in els if len(e.get_text(strip=True)) > 50]
        if els:
            reviews_els = els
            matched = sel
            break

    if not reviews_els:
        print(f"   ❌ No reviews found. Saving debug HTML → debug_imdb_{film['name']}.html")
        with open(f"debug_imdb_{film['name']}.html", "w", encoding="utf-8") as f:
            f.write(html)
        return []

    print(f"   ✔ Matched: '{matched}' → {len(reviews_els)} reviews")
    for el in reviews_els[:SAMPLE_SIZE]:
        text = el.get_text(separator=" ", strip=True)
        if len(text) > 30:
            results.append({
                "film":     film["name"],
                "source":   "IMDb",
                "language": "English",
                "text":     text,
                "url":      url,
            })

    print(f"   ✅ Collected {len(results)} reviews")
    return results


# ─────────────────────────────────────────────
# SOURCE 2: Spiderum (Vietnamese articles)
# ─────────────────────────────────────────────

async def scrape_spiderum(film: dict) -> list[dict]:
    """
    Cào nội dung bài viết review từ Spiderum (Angular SPA).
    Mỗi URL = 1 bài viết dài → tách thành nhiều đoạn paragraph.
    """
    urls = film.get("spiderum_urls", [])
    if not urls:
        print(f"\n[Spiderum] {film['name']}: Không có URL — bỏ qua")
        return []

    all_results = []
    for url in urls:
        print(f"\n[Spiderum] {film['name']}: {url[:70]}...")
        try:
            html = await playwright_fetch(
                url=url,
                # Spiderum Angular: chờ component bài viết mount
                wait_selector="div.content p, app-post-detail, div.post-details p",
                lang="vi-VN",
                scroll_times=4,
                extra_wait=6.0,   # Angular bootstrap cần lâu hơn React
            )
        except Exception as e:
            print(f"   ❌ Scrape failed: {e}")
            continue

        soup = BeautifulSoup(html, "html.parser")
        paragraphs = []

        # Selector ladder cho Spiderum Angular
        selector_ladder = [
            "div.content p",
            "div.post-details p",
            "app-post-detail p",
            "div[class*='content'] p",
            "div.text p",
            "div.ng-star-inserted p",
            "article p",
            "main p",
        ]

        for sel in selector_ladder:
            els = [e for e in soup.select(sel) if len(e.get_text(strip=True)) > 60]
            if els:
                paragraphs = els
                print(f"   ✔ Matched: '{sel}' → {len(els)} paragraphs")
                break

        if not paragraphs:
            print(f"   ❌ No content found. Saving → debug_spiderum_{film['name']}.html")
            with open(f"debug_spiderum_{film['name']}.html", "w", encoding="utf-8") as f:
                f.write(html)
            continue

        count = 0
        for p in paragraphs[:SAMPLE_SIZE]:
            text = p.get_text(separator=" ", strip=True)
            if len(text) > 50:
                all_results.append({
                    "film":     film["name"],
                    "source":   "Spiderum",
                    "language": "Vietnamese",
                    "text":     text,
                    "url":      url,
                })
                count += 1

        print(f"   ✅ Collected {count} paragraphs")
        await asyncio.sleep(random.uniform(2.0, 4.0))

    return all_results


# ─────────────────────────────────────────────
# SOURCE 3: Facebook (Vietnamese comments)
# ─────────────────────────────────────────────

async def scrape_facebook(film: dict) -> list[dict]:
    """
    Lấy comments từ Facebook posts qua Graph API.
    
    Cách lấy Access Token:
    1. Vào https://developers.facebook.com/tools/explorer/
    2. Tạo app → lấy User Access Token
    3. Cần permission: pages_read_engagement hoặc groups_access_member_content
    4. Lưu vào .env: FB_ACCESS_TOKEN=xxx
    
    Cách lấy Post ID:
    - URL post: fb.com/groups/GROUP_ID/posts/POST_ID → dùng "GROUP_ID_POST_ID"
    - Hoặc dùng Graph API Explorer để tìm
    """
    post_ids = film.get("fb_post_ids", [])
    if not post_ids:
        print(f"\n[Facebook] {film['name']}: Không có post IDs — bỏ qua")
        return []

    if not FB_ACCESS_TOKEN:
        print(f"\n[Facebook] ⚠ Chưa có FB_ACCESS_TOKEN trong .env — bỏ qua")
        print(f"   → Xem hướng dẫn trong comment của hàm scrape_facebook()")
        return []

    all_results = []
    async with httpx.AsyncClient(timeout=20) as client:
        for post_id in post_ids:
            print(f"\n[Facebook] Post: {post_id}")
            try:
                # Graph API: lấy comments của post
                r = await client.get(
                    f"https://graph.facebook.com/v19.0/{post_id}/comments",
                    params={
                        "fields": "message,created_time,from",
                        "limit": SAMPLE_SIZE,
                        "access_token": FB_ACCESS_TOKEN,
                    }
                )
                r.raise_for_status()
                data = r.json()

                if "error" in data:
                    print(f"   ❌ API Error: {data['error']['message']}")
                    continue

                comments = data.get("data", [])
                print(f"   ✔ Got {len(comments)} comments")

                for c in comments:
                    msg = c.get("message", "").strip()
                    if len(msg) > 20:
                        all_results.append({
                            "film":     film["name"],
                            "source":   "Facebook",
                            "language": "Vietnamese",
                            "text":     msg,
                            "url":      f"https://www.facebook.com/{post_id}",
                        })

                await asyncio.sleep(1.0)   # Tránh rate limit

            except Exception as e:
                print(f"   ❌ {e}")

    print(f"   ✅ Facebook total: {len(all_results)} comments")
    return all_results


# ─────────────────────────────────────────────
# OUTPUT: CSV local
# ─────────────────────────────────────────────

def save_csv(rows: list[dict], filename: str = OUTPUT_CSV):
    """Lưu vào CSV với header đầy đủ cho ML pipeline."""
    fields = ["timestamp", "film", "source", "language", "text", "url"]
    file_exists = os.path.isfile(filename)

    with open(filename, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, quoting=csv.QUOTE_ALL)
        if not file_exists:
            writer.writeheader()
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        for row in rows:
            writer.writerow({**{"timestamp": ts}, **row})

    print(f"   💾 Saved {len(rows)} rows → {filename}")


# ─────────────────────────────────────────────
# OUTPUT: Google Sheets (optional)
# ─────────────────────────────────────────────

def save_google_sheets(rows: list[dict]):
    """
    Đẩy data lên Google Sheets.
    
    Setup một lần:
    1. Vào https://console.cloud.google.com/
    2. Tạo project → Enable "Google Sheets API" + "Google Drive API"
    3. Tạo Service Account → Download JSON credentials
    4. Lưu file JSON cạnh script, đặt tên credentials.json
    5. Mở Google Sheet → Share với email của Service Account (editor)
    6. Copy Sheet ID từ URL → lưu vào .env: GSHEET_ID=xxx
    """
    if not GSHEET_ID:
        print("   ⚠ GSHEET_ID chưa set trong .env — bỏ qua Google Sheets")
        return
    if not os.path.isfile(GSHEET_CREDENTIALS):
        print(f"   ⚠ {GSHEET_CREDENTIALS} không tồn tại — bỏ qua Google Sheets")
        print(f"   → Xem hướng dẫn trong comment của hàm save_google_sheets()")
        return

    try:
        import gspread
        from google.oauth2.service_account import Credentials

        scopes = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_file(GSHEET_CREDENTIALS, scopes=scopes)
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(GSHEET_ID)

        # Dùng sheet "Raw Reviews", tạo nếu chưa có
        try:
            ws = sh.worksheet("Raw Reviews")
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet("Raw Reviews", rows=5000, cols=10)
            ws.append_row(["timestamp", "film", "source", "language", "text", "url"])

        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        batch = []
        for row in rows:
            batch.append([
                ts,
                row.get("film", ""),
                row.get("source", ""),
                row.get("language", ""),
                row.get("text", ""),
                row.get("url", ""),
            ])

        # Đẩy theo batch để tránh rate limit
        for i in range(0, len(batch), 50):
            ws.append_rows(batch[i:i+50], value_input_option="RAW")
            time.sleep(1.0)

        print(f"   ✅ Google Sheets: {len(rows)} rows pushed to '{ws.title}'")
        print(f"   🔗 https://docs.google.com/spreadsheets/d/{GSHEET_ID}")

    except ImportError:
        print("   ❌ gspread chưa cài: pip install gspread google-auth")
    except Exception as e:
        print(f"   ❌ Google Sheets error: {e}")


# ─────────────────────────────────────────────
# MAIN PIPELINE
# ─────────────────────────────────────────────

async def collect_film(film: dict) -> list[dict]:
    """Chạy tất cả sources cho 1 phim."""
    print(f"\n{'═'*60}")
    print(f"🎬  Film: {film['name']}")
    print(f"{'═'*60}")

    all_rows = []
    all_rows += await scrape_imdb(film)
    all_rows += await scrape_spiderum(film)
    all_rows += await scrape_facebook(film)

    return all_rows


async def main():
    print("🚀 DAP391 Data Collection Pipeline")
    print(f"   Films: {[f['name'] for f in FILMS]}")
    print(f"   Sample size per source: {SAMPLE_SIZE}")
    print(f"   Output: {OUTPUT_CSV}")
    if GSHEET_ID:
        print(f"   Google Sheets: enabled (ID: {GSHEET_ID[:20]}...)")
    if FB_ACCESS_TOKEN:
        print(f"   Facebook API: enabled")
    print()

    all_data = []
    for film in FILMS:
        rows = await collect_film(film)
        all_data.extend(rows)
        # Lưu ngay sau mỗi phim (tránh mất data nếu crash giữa chừng)
        if rows:
            save_csv(rows)
        await asyncio.sleep(2.0)

    # Push lên Google Sheets sau khi thu thập xong tất cả
    if all_data:
        save_google_sheets(all_data)

    print(f"\n{'═'*60}")
    print(f"✅ DONE — Collected {len(all_data)} total reviews")
    print(f"   Breakdown:")

    from collections import Counter
    for (src, lang), count in Counter(
        (r["source"], r["language"]) for r in all_data
    ).most_common():
        print(f"     {src:15s} ({lang:12s}): {count} reviews")

    print(f"\n   Output file: {os.path.abspath(OUTPUT_CSV)}")


if __name__ == "__main__":
    asyncio.run(main())