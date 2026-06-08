"""
Script Cào Dữ Liệu DAP391 - FINAL VERSION (Sử dụng Trình duyệt ảo)
"""
import asyncio
import csv
import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from playwright.async_api import async_playwright

OUTPUT_FILE = "dap391_dataset.csv"
TMDB_API_KEY = "8e513c38fa3cf4e8f800b07465a67b3e"

def save_to_csv(rows):
    import os
    file_exists = os.path.isfile(OUTPUT_FILE)
    with open(OUTPUT_FILE, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f, quoting=csv.QUOTE_ALL)
        if not file_exists:
            w.writerow(["Timestamp", "Source", "Language", "Text"])
        for r in rows:
            w.writerow([datetime.now().strftime("%Y-%m-%d %H:%M"), r['source'], r['lang'], r['text']])
    print(f"   💾 Đã lưu +{len(rows)} dòng vào {OUTPUT_FILE}")

# ==========================================
# 1. NHÁNH REDDIT (Dùng Header Chuẩn của Bot)
# ==========================================
def scrape_reddit():
    print("\n[Reddit] Đang tìm kiếm bình luận tiếng Anh...")
    rows = []
    # Khai báo thân phận rõ ràng để Reddit không block (Không dùng Chrome Header)
    headers = {"User-Agent": "python:dap391_project:v1.0 (by /u/student_dev)"}
    try:
        r = requests.get("https://www.reddit.com/r/movies/search.json",
                         params={"q": "Dune Part Two review", "sort": "top", "limit": 3, "restrict_sr": "on"},
                         headers=headers, timeout=15)
        r.raise_for_status()
        posts = r.json()["data"]["children"]

        for post in posts:
            post_id = post["data"]["id"]
            time.sleep(1.5) # Nghỉ để tránh Rate Limit
            rc = requests.get(f"https://www.reddit.com/r/movies/comments/{post_id}.json",
                              params={"limit": 20, "depth": 1, "sort": "top"},
                              headers=headers, timeout=15)
            rc.raise_for_status()
            comments = rc.json()[1]["data"]["children"]
            
            for c in comments:
                body = c.get("data", {}).get("body", "").strip()
                if len(body) > 50 and "[removed]" not in body:
                    rows.append({"source": "Reddit", "lang": "English", "text": body})
    except Exception as e:
        print(f"   ❌ Lỗi Reddit: {e}")
    
    print(f"   ✅ Reddit: Thu thập được {len(rows)} comments")
    return rows

# ==========================================
# 2. NHÁNH SPIDERUM (Dùng Playwright Trình duyệt ảo)
# ==========================================
async def scrape_spiderum():
    url = "https://spiderum.com/bai-dang/Review-Dune-Part-Two-2024-su-vi-dai-den-tu-su-gian-di-hv8"
    print(f"\n[Spiderum] Đang mở trình duyệt ảo cào tiếng Việt...")
    rows = []
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page()
            
            # Mở trang và chờ Angular render text
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            print("   ⏳ Đang chờ JavaScript render nội dung (5s)...")
            await page.wait_for_timeout(5000) 
            
            html = await page.content()
            await browser.close()

            soup = BeautifulSoup(html, "html.parser")
            # Lấy tất cả các thẻ p (đoạn văn) có độ dài đàng hoàng
            paragraphs = [p.get_text(separator=" ", strip=True) for p in soup.find_all("p")]
            
            for p in paragraphs:
                if len(p) > 60:
                    rows.append({"source": "Spiderum", "lang": "Vietnamese", "text": p})
                    
    except Exception as e:
        print(f"   ❌ Lỗi Spiderum: {e}")
        
    # Giới hạn lấy 30 đoạn dài nhất để test
    rows = rows[:30]
    print(f"   ✅ Spiderum: Thu thập được {len(rows)} đoạn văn")
    return rows

# ==========================================
# 3. NHÁNH TMDB (Yêu cầu VPN)
# ==========================================
def scrape_tmdb():
    print(f"\n[TMDB] Đang cào dữ liệu tiếng Anh...")
    rows = []
    try:
        r = requests.get("https://api.themoviedb.org/3/movie/693134/reviews",
                         params={"api_key": TMDB_API_KEY, "language": "en-US", "page": 1},
                         timeout=10)
        r.raise_for_status()
        reviews = r.json().get("results", [])
        for rev in reviews:
            text = rev.get("content", "").strip()
            if len(text) > 30:
                rows.append({"source": "TMDB", "lang": "English", "text": text})
        print(f"   ✅ TMDB: Thu thập được {len(rows)} reviews")
    except Exception as e:
        print(f"   ⚠ Lỗi TMDB: Máy bạn CHƯA BẬT VPN/1.1.1.1. Bỏ qua nhánh này.")
    return rows

# ==========================================
# CHẠY TỔNG
# ==========================================
async def main():
    print("🚀 BẮT ĐẦU CÀO DỮ LIỆU DAP391...")
    all_data = []

    # Chạy Sync (Reddit + TMDB)
    reddit_data = scrape_reddit()
    if reddit_data: all_data.extend(reddit_data)

    tmdb_data = scrape_tmdb()
    if tmdb_data: all_data.extend(tmdb_data)

    # Chạy Async (Spiderum)
    spiderum_data = await scrape_spiderum()
    if spiderum_data: all_data.extend(spiderum_data)

    # Lưu File
    if all_data:
        save_to_csv(all_data)
        print(f"\n🎉 HOÀN THÀNH! Output: {OUTPUT_FILE}")
    else:
        print("\n❌ Không lấy được dữ liệu nào.")

if __name__ == "__main__":
    asyncio.run(main())