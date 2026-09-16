"""
fetch_clips.py
يبحث في Pexels عن كليبات فيديو حقيقية بناءً على visual_keywords،
ويتجنب أي كليب اتستخدم قبل كده باستخدام state/used_clips.json.

يحتاج: متغير بيئة PEXELS_API_KEY (مجاني من https://www.pexels.com/api/)
"""
import os
import json
import sys
import requests
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
STATE_DIR = SCRIPT_DIR.parent / "state"
EPISODE_PATH = STATE_DIR / "current_episode.json"
USED_CLIPS_PATH = STATE_DIR / "used_clips.json"
CLIPS_DIR = SCRIPT_DIR.parent / "downloaded_clips"

PEXELS_SEARCH_URL = "https://api.pexels.com/videos/search"
MAX_PAGES_TO_TRY = 3       # لو أول صفحة كلها مكررة، جرّب صفحات تانية
CLIPS_PER_KEYWORD = 1
MIN_DURATION_SECONDS = 4   # نتجنب الكليبات القصيرة جدًا


def load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def search_pexels(keyword: str, api_key: str, used_ids: set) -> dict | None:
    headers = {"Authorization": api_key}

    for page in range(1, MAX_PAGES_TO_TRY + 1):
        params = {
            "query": keyword,
            "orientation": "portrait",  # مناسب للشورتس 9:16
            "per_page": 10,
            "page": page,
        }
        resp = requests.get(PEXELS_SEARCH_URL, headers=headers, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()

        for video in data.get("videos", []):
            if video["id"] in used_ids:
                continue
            if video["duration"] < MIN_DURATION_SECONDS:
                continue

            # اختار أفضل جودة فيديو ملف (HD لو موجود)
            video_files = sorted(
                video["video_files"],
                key=lambda f: f.get("height", 0),
                reverse=True,
            )
            hd_files = [f for f in video_files if 720 <= f.get("height", 0) <= 1080]
            chosen_file = hd_files[0] if hd_files else video_files[0]

            return {
                "id": video["id"],
                "url": chosen_file["link"],
                "keyword": keyword,
                "duration": video["duration"],
            }

    return None  # مفيش نتيجة جديدة بعد كل المحاولات


def download_clip(url: str, dest: Path):
    resp = requests.get(url, stream=True, timeout=60)
    resp.raise_for_status()
    with open(dest, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)


def main():
    api_key = os.environ.get("PEXELS_API_KEY")
    if not api_key:
        sys.exit("خطأ: لازم تضيف PEXELS_API_KEY في GitHub Secrets")

    episode = load_json(EPISODE_PATH, None)
    if episode is None:
        sys.exit("خطأ: مفيش current_episode.json — شغّل generate_script.py الأول")

    used_data = load_json(USED_CLIPS_PATH, {"pexels_ids_used": [], "history": []})
    used_ids = set(used_data.get("pexels_ids_used", []))

    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    fetched_clips = []

    for i, keyword in enumerate(episode["visual_keywords"]):
        result = search_pexels(keyword, api_key, used_ids)
        if result is None:
            print(f"⚠️  مفيش كليب جديد لكلمة '{keyword}' — هنتخطاها")
            continue

        dest_path = CLIPS_DIR / f"clip_{i:02d}.mp4"
        download_clip(result["url"], dest_path)
        used_ids.add(result["id"])

        fetched_clips.append({
            "file": str(dest_path),
            "pexels_id": result["id"],
            "keyword": keyword,
        })
        print(f"✅ اتنزل كليب لـ '{keyword}' (Pexels ID: {result['id']})")

    if not fetched_clips:
        sys.exit("خطأ: مفيش ولا كليب واحد اتنزل — راجع الكلمات المفتاحية أو رصيد الـ API")

    # حدّث ملف التتبع
    used_data["pexels_ids_used"] = list(used_ids)
    used_data["history"].append({
        "title": episode["title"],
        "clips": [c["pexels_id"] for c in fetched_clips],
    })
    # خلي الهيستوري آخر 100 حلقة بس عشان الملف مايكبرش أوي
    used_data["history"] = used_data["history"][-100:]
    USED_CLIPS_PATH.write_text(
        json.dumps(used_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # سجّل قائمة الكليبات عشان assemble_video.py يستخدمها
    (STATE_DIR / "fetched_clips.json").write_text(
        json.dumps(fetched_clips, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n🎬 إجمالي الكليبات الجاهزة: {len(fetched_clips)}")


if __name__ == "__main__":
    main()
