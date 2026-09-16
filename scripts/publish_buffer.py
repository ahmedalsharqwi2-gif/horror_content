"""
publish_buffer.py
بينشر الفيديو النهائي عبر Buffer GraphQL API بعد التجميع.

ملحوظة مهمة: Buffer قفلت الـ REST API القديمة (api.bufferapp.com/1) قدام
العملاء الجدد، واستبدلتها بـ GraphQL API جديدة (api.buffer.com) بمصادقة
عن طريق API Key بسيط بدل OAuth access token.

يحتاج:
- BUFFER_API_KEY  (من https://publish.buffer.com/settings/api — API Key وليس OAuth token)
- BUFFER_CHANNEL_ID (نفس معرف القناة، دلوقتي اسمه channelId في الـ API)
"""
import os
import json
import sys
import requests
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
EPISODE_PATH = SCRIPT_DIR.parent / "state" / "current_episode.json"
FINAL_VIDEO = SCRIPT_DIR.parent / "output" / "final_video.mp4"

BUFFER_GRAPHQL_API = "https://api.buffer.com"
GITHUB_API = "https://api.github.com"
RELEASE_TAG = "media-assets"  # نفس الـ release بيتعاد استخدامه كل مرة كـ "استضافة"

CREATE_POST_MUTATION = """
mutation CreatePost($text: String!, $channelId: ChannelId!, $videoUrl: String!) {
  createPost(
    input: {
      text: $text
      channelId: $channelId
      schedulingType: automatic
      mode: addToQueue
      assets: [{ video: { url: $videoUrl } }]
    }
  ) {
    ... on PostActionSuccess {
      post {
        id
        text
        dueAt
      }
    }
    ... on MutationError {
      message
    }
  }
}
"""


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


def create_buffer_post(video_url: str, caption: str, channel_id: str, api_key: str) -> dict:
    resp = requests.post(
        BUFFER_GRAPHQL_API,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        json={
            "query": CREATE_POST_MUTATION,
            "variables": {
                "text": caption,
                "channelId": channel_id,
                "videoUrl": video_url,
            },
        },
        timeout=30,
    )
    # الـ GraphQL API بترجع دايمًا 200 حتى لو حصل خطأ منطقي؛ الأخطاء بتبقى
    # جوه الـ body نفسه (errors[] أو MutationError)، مش في الـ status code.
    resp.raise_for_status()
    data = resp.json()

    if data.get("errors"):
        sys.exit(f"❌ خطأ من Buffer API (غير قابل للاسترجاع): {data['errors']}")

    result = data.get("data", {}).get("createPost", {})
    if "message" in result:  # ده شكل MutationError
        sys.exit(f"❌ فشل إنشاء البوست: {result['message']}")

    return result


def main():
    api_key = os.environ.get("BUFFER_API_KEY")
    channel_id = os.environ.get("BUFFER_CHANNEL_ID")
    github_token = os.environ.get("GITHUB_TOKEN")

    if not api_key or not channel_id:
        sys.exit("خطأ: لازم BUFFER_API_KEY و BUFFER_CHANNEL_ID في GitHub Secrets")
    if not github_token:
        sys.exit("خطأ: GITHUB_TOKEN مش متوفر (بيبقى تلقائي جوه Actions)")

    if not FINAL_VIDEO.exists():
        sys.exit("خطأ: مفيش final_video.mp4 — شغّل assemble_video.py الأول")

    episode = json.loads(EPISODE_PATH.read_text(encoding="utf-8"))

    print("⬆️  بترفع الفيديو كرابط عام مؤقت (GitHub Release)...")
    video_url = upload_media(FINAL_VIDEO, github_token)
    print(f"   الرابط العام: {video_url}")

    result = create_buffer_post(video_url, episode["caption"], channel_id, api_key)
    print(f"✅ اتنشر البوست عبر Buffer: {result}")


if __name__ == "__main__":
    main()
