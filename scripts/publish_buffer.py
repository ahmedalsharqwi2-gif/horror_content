"""
publish_buffer.py
بينشر الفيديو النهائي عبر Buffer API بعد ما توافق على الحلقة بـ /approve.

يحتاج: BUFFER_ACCESS_TOKEN و BUFFER_CHANNEL_ID (من إعدادات Buffer)
"""
import os
import json
import sys
import requests
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
EPISODE_PATH = SCRIPT_DIR.parent / "state" / "current_episode.json"
FINAL_VIDEO = SCRIPT_DIR.parent / "output" / "final_video.mp4"

BUFFER_API = "https://api.bufferapp.com/1"
GITHUB_API = "https://api.github.com"
RELEASE_TAG = "media-assets"  # نفس الـ release بيتعاد استخدامه كل مرة كـ "استضافة"


def _get_or_create_release(repo: str, github_token: str) -> dict:
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
    }
    resp = requests.get(
        f"{GITHUB_API}/repos/{repo}/releases/tags/{RELEASE_TAG}", headers=headers, timeout=20
    )
    if resp.status_code == 200:
        return resp.json()

    resp = requests.post(
        f"{GITHUB_API}/repos/{repo}/releases",
        headers=headers,
        json={
            "tag_name": RELEASE_TAG,
            "name": "Media Assets (auto-hosted videos for Buffer)",
            "body": "Release مستخدم كاستضافة مؤقتة للفيديوهات عشان Buffer يقدر يوصلها برابط عام.",
            "prerelease": True,
        },
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def upload_media(video_path: Path, github_token: str) -> str:
    """يرفع الفيديو كـ asset في GitHub Release ويرجّع رابط تحميل عام مباشر.
    ده حل مجاني بالكامل وبيشتغل من غير أي حساب استضافة خارجي إضافي."""
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        sys.exit("خطأ: GITHUB_REPOSITORY مش متوفر — لازم يشتغل جوه GitHub Actions")

    release = _get_or_create_release(repo, github_token)
    upload_url = release["upload_url"].split("{")[0]  # شيل الـ template زي {?name,label}

    asset_name = f"video_{video_path.stem}_{os.environ.get('GITHUB_RUN_ID', 'local')}.mp4"
    with open(video_path, "rb") as f:
        resp = requests.post(
            upload_url,
            headers={
                "Authorization": f"Bearer {github_token}",
                "Content-Type": "video/mp4",
            },
            params={"name": asset_name},
            data=f,
            timeout=120,
        )
    resp.raise_for_status()
    return resp.json()["browser_download_url"]


def create_buffer_post(video_url: str, caption: str, channel_id: str, access_token: str):
    resp = requests.post(
        f"{BUFFER_API}/updates/create.json",
        data={
            "access_token": access_token,
            "profile_ids[]": channel_id,
            "text": caption,
            "media[video]": video_url,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def main():
    access_token = os.environ.get("BUFFER_ACCESS_TOKEN")
    channel_id = os.environ.get("BUFFER_CHANNEL_ID")
    github_token = os.environ.get("GITHUB_TOKEN")

    if not access_token or not channel_id:
        sys.exit("خطأ: لازم BUFFER_ACCESS_TOKEN و BUFFER_CHANNEL_ID في GitHub Secrets")
    if not github_token:
        sys.exit("خطأ: GITHUB_TOKEN مش متوفر (بيبقى تلقائي جوه Actions)")

    if not FINAL_VIDEO.exists():
        sys.exit("خطأ: مفيش final_video.mp4 — شغّل assemble_video.py الأول")

    episode = json.loads(EPISODE_PATH.read_text(encoding="utf-8"))

    print("⬆️  بترفع الفيديو كرابط عام مؤقت (GitHub Release)...")
    video_url = upload_media(FINAL_VIDEO, github_token)
    print(f"   الرابط العام: {video_url}")

    result = create_buffer_post(video_url, episode["caption"], channel_id, access_token)
    print(f"✅ اتنشر البوست عبر Buffer: {result}")


if __name__ == "__main__":
    main()
