"""
publish_buffer.py - نشر فيديو إلى قنوات Buffer مع عنوان وهاشتاجات.
"""

import json
import os
import re
import sys
from pathlib import Path

import requests

SCRIPT_DIR = Path(__file__).parent
EPISODE_PATH = SCRIPT_DIR.parent / "state" / "current_episode.json"
FINAL_VIDEO = SCRIPT_DIR.parent / "output" / "final_video.mp4"
BUFFER_GRAPHQL_API = "https://api.buffer.com"
GITHUB_API = "https://api.github.com"
RELEASE_TAG = "media-assets"

CHANNEL_SERVICES = {
    "6aaa8778ea19ca0bde57da16": "youtube",
    "6aaa8700ea19ca0bde57d3fc": "tiktok",
    "6aaa853fea19ca0bde57b5f7": "facebook",
}

CREATE_POST_MUTATION = """
mutation CreatePost(
  $text: String!
  $channelId: ChannelId!
  $videoUrl: String!
  $metadata: PostInputMetaData
) {
  createPost(
    input: {
      text: $text
      channelId: $channelId
      schedulingType: automatic
      mode: addToQueue
      metadata: $metadata
      assets: [{ video: { url: $videoUrl } }]
    }
  ) {
    ... on PostActionSuccess { post { id text dueAt } }
    ... on MutationError { message }
  }
}
"""


def github_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def get_or_create_release(repo: str, token: str) -> dict:
    response = requests.get(
        f"{GITHUB_API}/repos/{repo}/releases/tags/{RELEASE_TAG}",
        headers=github_headers(token), timeout=20,
    )
    if response.status_code == 200:
        return response.json()

    response = requests.post(
        f"{GITHUB_API}/repos/{repo}/releases",
        headers=github_headers(token),
        json={
            "tag_name": RELEASE_TAG,
            "name": "Media Assets",
            "body": "Temporary public hosting for Buffer video posts.",
            "prerelease": True,
        },
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def upload_media(video_path: Path, token: str) -> str:
    repo = os.environ.get("MEDIA_REPOSITORY") or os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        raise RuntimeError("MEDIA_REPOSITORY or GITHUB_REPOSITORY is missing.")

    release = get_or_create_release(repo, token)
    upload_url = release["upload_url"].split("{")[0]
    asset_name = f"video_{video_path.stem}_{os.environ.get('GITHUB_RUN_ID', 'local')}.mp4"

    with video_path.open("rb") as file_handle:
        response = requests.post(
            upload_url,
            headers={**github_headers(token), "Content-Type": "video/mp4"},
            params={"name": asset_name}, data=file_handle, timeout=180,
        )
    response.raise_for_status()
    return response.json()["browser_download_url"]


def metadata_for(channel_id: str, title: str) -> dict | None:
    service = CHANNEL_SERVICES.get(channel_id)
    if service == "youtube":
        return {"youtube": {
            "title": title[:100] or "Horror Episode",
            "categoryId": "24",
            "privacy": "public",
            "madeForKids": False,
            "notifySubscribers": True,
            "isAiGenerated": True,
        }}
    if service == "facebook":
        return {"facebook": {"type": "reel", "title": title[:255]}}
    if service == "tiktok":
        return {"tiktok": {"isAiGenerated": True}}
    return None


def create_buffer_post(video_url: str, post_text: str, title: str, channel_id: str, api_key: str) -> dict:
    response = requests.post(
        BUFFER_GRAPHQL_API,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        json={"query": CREATE_POST_MUTATION, "variables": {
            "text": post_text,
            "channelId": channel_id,
            "videoUrl": video_url,
            "metadata": metadata_for(channel_id, title),
        }},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("errors"):
        raise RuntimeError(str(data["errors"]))
    result = data.get("data", {}).get("createPost") or {}
    if result.get("message"):
        raise RuntimeError(result["message"])
    if not result.get("post"):
        raise RuntimeError(f"No post returned: {result}")
    return result


def parse_channel_ids(raw: str) -> list[str]:
    raw = raw.replace(";", ",").replace("\n", ",")
    ids = [value.strip().strip("\"'") for value in raw.split(",") if value.strip()]
    return list(dict.fromkeys(ids))


def build_post_text(caption: str, title: str) -> str:
    """يحافظ على نص المنشور ويضمن وجود العنوان والهاشتاجات دون تكرار."""
    caption = re.sub(r"^\s*=\s*", "", caption).strip()
    title = re.sub(r"^\s*=\s*", "", title).strip()

    hashtags = re.findall(r"(?<!\w)#\S+", caption)
    hashtags = list(dict.fromkeys(hashtags))
    hashtags_text = " ".join(hashtags)

    parts = []
    if title and title.casefold() not in caption.casefold():
        parts.append(title)
    if caption:
        parts.append(caption)
    if hashtags_text and hashtags_text not in caption:
        parts.append(hashtags_text)

    return "\n\n".join(parts).strip()


def main() -> None:
    api_key = os.environ.get("BUFFER_API_KEY", "").strip()
    raw_ids = os.environ.get("BUFFER_CHANNEL_ID", "")
    github_token = os.environ.get("GITHUB_TOKEN", "").strip()

    if not api_key or not raw_ids.strip() or not github_token:
        sys.exit("BUFFER_API_KEY, BUFFER_CHANNEL_ID and GITHUB_TOKEN are required.")
    if not FINAL_VIDEO.exists() or FINAL_VIDEO.stat().st_size == 0:
        sys.exit("output/final_video.mp4 is missing or empty.")
    if not EPISODE_PATH.exists():
        sys.exit("state/current_episode.json is missing.")

    channel_ids = parse_channel_ids(raw_ids)
    episode = json.loads(EPISODE_PATH.read_text(encoding="utf-8"))
    title = str(episode.get("title", "Horror Episode")).strip() or "Horror Episode"
    caption = str(episode.get("caption", "")).strip()
    if not caption:
        sys.exit("current_episode.json has no caption.")

    post_text = build_post_text(caption, title)
    print(f"Configured Buffer channels: {len(channel_ids)}")
    print(f"Post title loaded: {title[:80]}")
    print(f"Hashtags detected: {len(re.findall(r'(?<!\\w)#\\S+', caption))}")

    video_url = upload_media(FINAL_VIDEO, github_token)
    print("Public video URL created successfully.")

    successes = 0
    failures = []
    for number, channel_id in enumerate(channel_ids, 1):
        service = CHANNEL_SERVICES.get(channel_id, "unknown")
        try:
            print(f"Publishing channel {number}/{len(channel_ids)} ({service})...")
            result = create_buffer_post(video_url, post_text, title, channel_id, api_key)
            print(f"Published {service}; post id: {result['post'].get('id', 'unknown')}")
            successes += 1
        except Exception as error:
            failures.append((service, str(error)))
            print(f"Channel {number}/{len(channel_ids)} failed ({service}): {error}")

    print(f"Successful Buffer posts: {successes}/{len(channel_ids)}")
    for service, error in failures:
        print(f"Failure summary ({service}): {error}")

    if successes == 0:
        sys.exit("No Buffer channel was published successfully.")
    if failures:
        print("Warning: some channels failed, but at least one post succeeded.")


if __name__ == "__main__":
    main()
