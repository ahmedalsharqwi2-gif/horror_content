"""
publish_buffer.py - نشر فيديو (أو فيديوهين لو القصة اتقسمت جزئين) إلى قنوات
Buffer مع عنوان وهاشتاجات، مع جدولة تلقائية للجزء الثاني.

=== آخر تعديلين (إصلاح فشل فيسبوك ويوتيوب) ===

1) فشل فيسبوك في الجزأين:
   Buffer بترجع "Field 'title' is not defined by type FacebookPostMetadataInput".
   يعني metadata.facebook بتقبل بس حقل type ("reel")، مش title. كنا
   بنبعت title جوه metadata.facebook غلط. العنوان أصلاً موجود في نص
   البوست نفسه (build_post_text)، فمفيش داعي نكرره في الـ metadata.
   → metadata_for() بقت ترجع {"facebook": {"type": "reel"}} بس.

2) Scheduled posts limit reached (10/10) — ظهر أول مرة بس في الجزء
   المجدول، وبعدين بقى بيظهر حتى في addToQueue (الجزء الفوري) كمان.
   ده معناه إن Buffer بتحسب أي بوست لسه ماتنشرش (فوري في الطابور أو
   مجدول لوقت محدد) كـ "scheduled" ضد نفس الحد (10 لكل قناة). يعني
   لو القناة فيها 10 بوستات معلّقة أصلاً، مفيش أي بوست جديد هينفع
   يتضاف — سواء فوري أو مجدول. ده حد حساب حقيقي مش حاجة نلتف عليها
   بالكود؛ ضفنا preflight check (count_pending_posts) بيسأل Buffer
   قبل كل محاولة نشر "كام بوست معلّق على القناة دي؟" ولو وصل للحد
   (CHANNEL_PENDING_LIMIT، افتراضي 10) بيتخطى المحاولة برسالة واضحة
   بدل ما يحاول ويفشل. الحل الحقيقي: تنشر/تمسح بعض البوستات المعلّقة
   يدويًا من Buffer Dashboard، أو تقلل معدل تشغيل الـ workflow، أو
   ترفّع خطة Buffer.

=== ليه اتعدل الملف قبل كده ===

- generate_voice.py بقى يقسم القصص الطويلة جزئين، وبيكتب ملفات الصوت
  بلاحقة (narration_with_music_part1.mp3 / _part2.mp3) بدل الاسم الثابت
  القديم.
- كل جزء بيترفع لوحده على GitHub Release، وبينشر بعنوان/كابشن مخصص ليه
  (تنويه "الجزء 1/2").
- الجزء الأول ينشر فورًا (mode: addToQueue)، والجزء الثاني يتجدول فعليًا
  جوه Buffer نفسها (mode: customScheduled + dueAt). الافتراضي 24 ساعة،
  غيّرها بمتغير بيئة PART2_DELAY_HOURS.
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

SCRIPT_DIR = Path(__file__).parent
ROOT_DIR = SCRIPT_DIR.parent
EPISODE_PATH = ROOT_DIR / "state" / "current_episode.json"
OUTPUT_DIR = ROOT_DIR / "output"
BUFFER_GRAPHQL_API = "https://api.buffer.com"
GITHUB_API = "https://api.github.com"
RELEASE_TAG = "media-assets"

# كل جزء بينشر بعد اللي قبله بالمدة دي (ساعات)، مضروبة في (رقم الجزء - 1).
# بافتراض جزئين: جزء 2 = فورًا + 24 ساعة.
PART_DELAY_HOURS = float(os.environ.get("PART2_DELAY_HOURS", "24"))

# نص الخطأ اللي Buffer بيرجعه لما حد الجدولة يخلص (10 بوستات مجدولة).
SCHEDULE_LIMIT_MARKER = "Scheduled posts limit"

# أقصى عدد بوستات "معلّقة" (queued أو scheduled، الاتنين بيتحسبوا "scheduled"
# في نظر Buffer) مسموح بيها لكل قناة قبل ما تتوقف عن المحاولة.
CHANNEL_PENDING_LIMIT = int(os.environ.get("CHANNEL_PENDING_LIMIT", "10"))
# لو True (الافتراضي)، هنسأل Buffer الأول كام بوست معلّق على كل قناة قبل
# أي محاولة نشر — بدل ما نحاول ونفشل بعد ما نكون رفعنا الفيديو بالفعل.
ENABLE_PREFLIGHT_CHECK = os.environ.get("ENABLE_PREFLIGHT_CHECK", "true").lower() != "false"

GET_ORGANIZATIONS_QUERY = """
query GetOrganizations {
  account { organizations { id name } }
}
"""

# بيرجع أول 10 بوستات "scheduled" (فيها القيّم الفوري + المجدول) لقناة معيّنة.
# مش محتاجين نعدّ أكتر من 10 أصلاً لأن ده أقصى حد Buffer بيسمح بيه.
GET_PENDING_POSTS_QUERY = """
query GetPendingPosts($organizationId: OrganizationId!, $channelId: ChannelId!) {
  posts(
    first: 10
    input: {
      organizationId: $organizationId
      filter: { status: [scheduled], channelIds: [$channelId] }
    }
  ) {
    edges { node { id } }
  }
}
"""

CHANNEL_SERVICES = {
    "6aaa8778ea19ca0bde57da16": "youtube",
    "6aaa8700ea19ca0bde57d3fc": "tiktok",
    "6aaa853fea19ca0bde57b5f7": "facebook",
}

# نشر فوري (زي ما كان بالظبط) — بيدخل طابور Buffer العادي.
CREATE_POST_MUTATION_QUEUE = """
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

# نشر مجدول لوقت محدد (للجزء الثاني وما بعده).
# ⚠️ لو Buffer رفض $dueAt: DateTime! بخطأ نوع، جرّب "String!" بدالها —
# نفس القيمة (ISO 8601) بتتبعت زي ما هي.
CREATE_POST_MUTATION_SCHEDULED = """
mutation CreateScheduledPost(
  $text: String!
  $channelId: ChannelId!
  $videoUrl: String!
  $metadata: PostInputMetaData
  $dueAt: DateTime!
) {
  createPost(
    input: {
      text: $text
      channelId: $channelId
      schedulingType: automatic
      mode: customScheduled
      dueAt: $dueAt
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
        # FacebookPostMetadataInput بيقبل بس "type" — مفيش title هنا.
        # العنوان موجود أصلاً جوه نص البوست (build_post_text).
        return {"facebook": {"type": "reel"}}
    if service == "tiktok":
        return {"tiktok": {"isAiGenerated": True}}
    return None


def _send_create_post(query: str, variables: dict, api_key: str) -> dict:
    response = requests.post(
        BUFFER_GRAPHQL_API,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        json={"query": query, "variables": variables},
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


def create_buffer_post(
    video_url: str, post_text: str, title: str, channel_id: str,
    api_key: str, due_at: str | None = None,
) -> dict:
    metadata = metadata_for(channel_id, title)

    if due_at:
        variables = {
            "text": post_text, "channelId": channel_id, "videoUrl": video_url,
            "metadata": metadata, "dueAt": due_at,
        }
        try:
            return _send_create_post(CREATE_POST_MUTATION_SCHEDULED, variables, api_key)
        except RuntimeError as error:
            if SCHEDULE_LIMIT_MARKER not in str(error):
                raise
            # حد الجدولة في Buffer خلص (10 بوستات مجدولة) — بدل ما الجزء
            # يفشل، ننشره فورًا في الطابور العادي بدل الجدولة.
            print(f"    ⚠️ {SCHEDULE_LIMIT_MARKER} — هنشر فورًا (addToQueue) بدل الجدولة.")
            variables = {
                "text": post_text, "channelId": channel_id,
                "videoUrl": video_url, "metadata": metadata,
            }
            return _send_create_post(CREATE_POST_MUTATION_QUEUE, variables, api_key)

    variables = {
        "text": post_text, "channelId": channel_id,
        "videoUrl": video_url, "metadata": metadata,
    }
    return _send_create_post(CREATE_POST_MUTATION_QUEUE, variables, api_key)


def get_organization_id(api_key: str) -> str:
    """أول Organization ID متاح في الحساب — كافي هنا لأننا مش محتاجين نفرّق
    بين منظمات متعددة، بس Buffer بيتطلبه كباراميتر إلزامي في posts query."""
    result = _send_graphql(GET_ORGANIZATIONS_QUERY, {}, api_key)
    organizations = result.get("account", {}).get("organizations", [])
    if not organizations:
        raise RuntimeError("No Buffer organization found for this API key.")
    return organizations[0]["id"]


def count_pending_posts(organization_id: str, channel_id: str, api_key: str) -> int:
    """عدد البوستات المعلّقة (queued أو scheduled) على القناة دي دلوقتي،
    مقفول عند 10 لأن ده أقصى حاجة إحنا محتاجينها (حد Buffer)."""
    result = _send_graphql(
        GET_PENDING_POSTS_QUERY,
        {"organizationId": organization_id, "channelId": channel_id},
        api_key,
    )
    return len(result.get("posts", {}).get("edges", []))


def _send_graphql(query: str, variables: dict, api_key: str) -> dict:
    response = requests.post(
        BUFFER_GRAPHQL_API,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        json={"query": query, "variables": variables},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("errors"):
        raise RuntimeError(str(data["errors"]))
    return data.get("data", {})


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


def schedule_iso(hours_from_now: float) -> str:
    """توقيت UTC بصيغة تقبلها Buffer، مثال: 2026-09-17T18:30:00.000Z"""
    due = datetime.now(timezone.utc) + timedelta(hours=hours_from_now)
    return due.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def resolve_parts(episode: dict) -> list[dict]:
    """
    يحدد أجزاء الحلقة (جزء واحد أو جزئين) اعتمادًا على episode["parts"]
    اللي بيكتبها generate_voice.py، وبيبني مسار الفيديو المتوقع لكل جزء.

    الاصطلاح: نفس اللاحقة اللي بيستخدمها generate_voice.py لملفات الصوت
    (فاضية للحلقة الواحدة، _part1/_part2 للتقسيم) — بافتراض إن
    assemble_video.py بيطلّع الفيديو بنفس اللاحقة بالظبط.
    """
    raw_parts = episode.get("parts") or [{}]  # توافق مع حلقات قديمة من غير "parts"
    total = len(raw_parts)
    parts = []
    for index in range(1, total + 1):
        suffix = "" if total == 1 else f"_part{index}"
        parts.append({
            "index": index,
            "total": total,
            "video_path": OUTPUT_DIR / f"final_video{suffix}.mp4",
        })
    return parts


def augment_for_part(title: str, caption: str, index: int, total: int) -> tuple[str, str]:
    """يضيف تنويه الجزء (1/2) للعنوان والكابشن لو الحلقة متقسمة."""
    if total <= 1:
        return title, caption

    part_title = f"{title} (الجزء {index})"
    if index < total:
        note = "🔻 الجزء التاني جاي قريب... تابعونا عشان متفوتوش النهاية 👀"
        part_caption = f"{caption}\n\n{note}"
    else:
        note = "🎬 استكمال الجزء اللي فات ⬆️"
        part_caption = f"{note}\n\n{caption}"
    return part_title, part_caption


def main() -> None:
    api_key = os.environ.get("BUFFER_API_KEY", "").strip()
    raw_ids = os.environ.get("BUFFER_CHANNEL_ID", "")
    github_token = os.environ.get("GITHUB_TOKEN", "").strip()

    if not api_key or not raw_ids.strip() or not github_token:
        sys.exit("BUFFER_API_KEY, BUFFER_CHANNEL_ID and GITHUB_TOKEN are required.")
    if not EPISODE_PATH.exists():
        sys.exit("state/current_episode.json is missing.")

    channel_ids = parse_channel_ids(raw_ids)

    organization_id = None
    if ENABLE_PREFLIGHT_CHECK:
        try:
            organization_id = get_organization_id(api_key)
        except Exception as error:
            print(f"⚠️ تعذّر جلب organizationId، هنتخطى preflight check: {error}")

    episode = json.loads(EPISODE_PATH.read_text(encoding="utf-8"))
    title = str(episode.get("title", "Horror Episode")).strip() or "Horror Episode"
    caption = str(episode.get("caption", "")).strip()
    if not caption:
        sys.exit("current_episode.json has no caption.")

    parts = resolve_parts(episode)

    # ── فحص كل الفيديوهات المطلوبة قبل أي نشر، برسالة تحدد الجزء الناقص بالظبط ──
    missing = [p for p in parts if not p["video_path"].exists() or p["video_path"].stat().st_size == 0]
    if missing:
        names = ", ".join(str(p["video_path"].relative_to(ROOT_DIR)) for p in missing)
        sys.exit(
            f"{names} is missing or empty. "
            f"(الحلقة مقسّمة لـ {len(parts)} جزء/أجزاء حسب current_episode.json؛ "
            f"تأكد إن assemble_video.py بيطلّع فيديو لكل جزء بنفس اللاحقة _part1/_part2)"
        )

    print(f"Configured Buffer channels: {len(channel_ids)}")
    print(f"Post title loaded: {title[:80]}")
    print(f"Episode parts: {len(parts)}")
    hashtag_count = len(re.findall(r"(?<!\w)#\S+", caption))
    print(f"Hashtags detected: {hashtag_count}")

    overall_successes = 0
    overall_failures = []

    for part in parts:
        index, total = part["index"], part["total"]
        part_title, part_caption = augment_for_part(title, caption, index, total)
        post_text = build_post_text(part_caption, part_title)

        due_at = None
        if index > 1:
            due_at = schedule_iso(PART_DELAY_HOURS * (index - 1))
            print(f"Part {index}/{total} scheduled for {due_at} (UTC).")
        else:
            print(f"Part {index}/{total} publishing now (queue).")

        video_url = upload_media(part["video_path"], github_token)
        print(f"Part {index}/{total}: public video URL created successfully.")

        for number, channel_id in enumerate(channel_ids, 1):
            service = CHANNEL_SERVICES.get(channel_id, "unknown")

            if organization_id:
                try:
                    pending = count_pending_posts(organization_id, channel_id, api_key)
                    if pending >= CHANNEL_PENDING_LIMIT:
                        reason = (
                            f"channel queue full ({pending}/{CHANNEL_PENDING_LIMIT}) — "
                            f"انشر يدويًا أو امسح بوستات معلّقة من Buffer Dashboard، "
                            f"أو قلّل معدل التشغيل، أو رفّع خطة Buffer"
                        )
                        overall_failures.append((f"part{index}/{service}", reason))
                        print(f"  ⏭️  Skipping channel {number}/{len(channel_ids)} ({service}): {reason}")
                        continue
                except Exception as error:
                    print(f"  ⚠️ تعذّر فحص عدد البوستات المعلّقة لقناة {service}: {error}")

            try:
                print(f"  Publishing channel {number}/{len(channel_ids)} ({service})...")
                result = create_buffer_post(
                    video_url, post_text, part_title, channel_id, api_key, due_at=due_at,
                )
                print(f"  Published {service}; post id: {result['post'].get('id', 'unknown')}")
                overall_successes += 1
            except Exception as error:
                overall_failures.append((f"part{index}/{service}", str(error)))
                print(f"  Channel {number}/{len(channel_ids)} failed ({service}): {error}")

    total_attempts = len(parts) * len(channel_ids)
    print(f"Successful Buffer posts: {overall_successes}/{total_attempts}")
    for label, error in overall_failures:
        print(f"Failure summary ({label}): {error}")

    if overall_successes == 0:
        sys.exit("No Buffer channel was published successfully.")
    if overall_failures:
        print("Warning: some channels failed, but at least one post succeeded.")


if __name__ == "__main__":
    main()
