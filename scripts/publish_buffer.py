"""
publish_buffer.py
ينشر الفيديو النهائي عبر Buffer GraphQL API إلى قناة أو عدة قنوات.

GitHub Secrets المطلوبة:
- BUFFER_API_KEY
- BUFFER_CHANNEL_ID

يمكن أن تكون BUFFER_CHANNEL_ID قيمة واحدة أو عدة قيم مفصولة بفواصل:
6aaa8778ea19ca0bde57da16,6aaa8700ea19ca0bde57d3fc,6aaa853fea19ca0bde57b5f7
"""

import json
import os
import sys
from pathlib import Path

import requests

SCRIPT_DIR = Path(__file__).parent
EPISODE_PATH = SCRIPT_DIR.parent / "state" / "current_episode.json"
FINAL_VIDEO = SCRIPT_DIR.parent / "output" / "final_video.mp4"

BUFFER_GRAPHQL_API = "https://api.buffer.com"
GITHUB_API = "https://api.github.com"
RELEASE_TAG = "media-assets"

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
        "X-GitHub-Api-Version": "2022-11-28",
    }

    response = requests.get(
        f"{GITHUB_API}/repos/{repo}/releases/tags/{RELEASE_TAG}",
        headers=headers,
        timeout=20,
    )
    if response.status_code == 200:
        return response.json()

    response = requests.post(
        f"{GITHUB_API}/repos/{repo}/releases",
        headers=headers,
        json={
            "tag_name": RELEASE_TAG,
            "name": "Media Assets (auto-hosted videos for Buffer)",
            "body": "Temporary public hosting for Buffer video posts.",
            "prerelease": True,
        },
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def upload_media(video_path: Path, github_token: str) -> str:
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        raise RuntimeError("GITHUB_REPOSITORY is not available; run inside GitHub Actions.")

    release = _get_or_create_release(repo, github_token)
    upload_url = release["upload_url"].split("{")[0]
    asset_name = f"video_{video_path.stem}_{os.environ.get('GITHUB_RUN_ID', 'local')}.mp4"

    with video_path.open("rb") as video_file:
        response = requests.post(
            upload_url,
            headers={
                "Authorization": f"Bearer {github_token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "video/mp4",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            params={"name": asset_name},
            data=video_file,
            timeout=180,
        )

    response.raise_for_status()
    return response.json()["browser_download_url"]


def create_buffer_post(video_url: str, caption: str, channel_id: str, api_key: str) -> dict:
    response = requests.post(
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
    response.raise_for_status()
    data = response.json()

    if data.get("errors"):
        raise RuntimeError(f"Buffer GraphQL error: {data['errors']}")

    result = data.get("data", {}).get("createPost") or {}
    if result.get("message"):
        raise RuntimeError(f"Buffer rejected channel {channel_id}: {result['message']}")

    if not result.get("post"):
        raise RuntimeError(f"Buffer returned no post for channel {channel_id}: {result}")

    return result


def parse_channel_ids(raw_value: str) -> list[str]:
    # Accept comma-separated values and also tolerate newlines/semicolons.
    normalized = raw_value.replace(";", ",").replace("\n", ",")
    ids = [part.strip() for part in normalized.split(",") if part.strip()]

    # Remove accidental surrounding quotes without exposing secrets in logs.
    ids = [channel_id.strip('"\' ') for channel_id in ids]
    ids = list(dict.fromkeys(ids))

    if not ids:
        raise RuntimeError("BUFFER_CHANNEL_ID is empty.")

    return ids


def main() -> None:
    api_key = os.environ.get("BUFFER_API_KEY", "").strip()
    raw_channel_ids = os.environ.get("BUFFER_CHANNEL_ID", "")
    github_token = os.environ.get("GITHUB_TOKEN", "").strip()

    if not api_key:
        sys.exit("Error: BUFFER_API_KEY is missing.")
    if not raw_channel_ids.strip():
        sys.exit("Error: BUFFER_CHANNEL_ID is missing.")
    if not github_token:
        sys.exit("Error: GITHUB_TOKEN is missing.")
    if not FINAL_VIDEO.exists() or FINAL_VIDEO.stat().st_size == 0:
        sys.exit("Error: output/final_video.mp4 is missing or empty.")
    if not EPISODE_PATH.exists():
        sys.exit("Error: state/current_episode.json is missing.")

    channel_ids = parse_channel_ids(raw_channel_ids)
    episode = json.loads(EPISODE_PATH.read_text(encoding="utf-8"))
    caption = str(episode.get("caption", "")).strip()
    if not caption:
        sys.exit("Error: current_episode.json has no caption.")

    print(f"Configured Buffer channels: {len(channel_ids)}")
    print("Uploading video to a public GitHub Release URL...")
    video_url = upload_media(FINAL_VIDEO, github_token)
    print("Public video URL created successfully.")

    successes = []
    failures = []

    for index, channel_id in enumerate(channel_ids, start=1):
        try:
            print(f"Publishing channel {index}/{len(channel_ids)}...")
            result = create_buffer_post(video_url, caption, channel_id, api_key)
            post = result.get("post", {})
            successes.append(channel_id)
            print(f"Published channel {index}/{len(channel_ids)}; post id: {post.get('id', 'unknown')}")
        except Exception as error:
            failures.append((channel_id, str(error)))
            print(f"Channel {index}/{len(channel_ids)} failed: {error}")

    print(f"Successful Buffer posts: {len(successes)}/{len(channel_ids)}")

    if failures:
        print(f"Failed Buffer posts: {len(failures)}")

    if not successes:
        sys.exit("No Buffer channel was published successfully.")

    if failures:
        print("Warning: at least one channel failed, but at least one post succeeded.")


if __name__ == "__main__":
    main()
