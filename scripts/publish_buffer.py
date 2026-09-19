"""
publish_buffer.py - نشر فيديو (أو فيديوهين لو القصة اتقسمت جزئين) إلى قنوات
Buffer مع عنوان وهاشتاجات، مع جدولة تلقائية للجزء الثاني.

=== تعديل جديد: لينك الجزء الأول جوه الجزء الثاني ===
بعد ما الجزء الأول ينشر فعليًا (مش بس يتجدول)، Buffer بيرجّع externalLink
(الرابط الحقيقي عند المنصة نفسها). بننتظره (polling، حد أقصى
LINK_WAIT_TIMEOUT_SECONDS ثانية، افتراضيًا 6 دقايق) وبعدين بنحطه في:
  - يوتيوب: جوه نص وصف فيديو الجزء الثاني نفسه (قابل للضغط عليه).
  - فيسبوك: كأول تعليق على بوست الجزء الثاني (firstComment، مدعوم رسميًا
    من Buffer API).
انستجرام مُستبعد عن قصد: حقل firstComment بتاعها فيها bug معروف ومُعلن
من Buffer نفسها (بيترفض بصمت)، وحتى لو اشتغل، انستجرام أصلاً مابيخليش
لينكات قابلة للضغط في الكابشن ولا التعليقات (بس في البايو/الستيكرز).
لو اللينك اتأخر أكتر من المهلة أو النشر فشل، الجزء الثاني بينشر عادي من
غير لينك لتلك القناة بس (مش هيوقف السكريبت كله).

=== إصلاح سابق (سبب مشكلة تضارب المواعيد بين المنصات) ===

المشكلة اللي كانت بتحصل: الجزء الأول كان بينشر بـ mode: addToQueue، وده
حسب توثيق Buffer الرسمي معناه "حطّه في أقرب سلوت فاضي في جدول القناة"،
مش "انشره دلوقتي فورًا". كل قناة (فيسبوك/يوتيوب/انستجرام) ليها جدول
سلوتات افتراضي خاص بيها جوه Buffer نفسه (زي 6 صباحًا أو 4 صباحًا لو كان
متظبط كده من قبل)، فكانت النتيجة إن نفس الحلقة بتتوزع على أوقات مختلفة
تمامًا في كل منصة، بدل ما تنشر الساعة 3 عصرًا مع بعض زي المطلوب.

الحل: الجزء الأول بقى بيستخدم customScheduled بوقت محدد (تقريبًا دلوقتي)
بدل addToQueue، بالظبط زي الجزء الثاني، عشان كل القنوات تاخد نفس اللحظة
بالضبط. الفرق الوحيد إن due_at للجزء الأول = "الآن" (offset=0) بينما
الجزء الثاني = "الآن + PART2_DELAY_HOURS ساعة".

⚠️ ملحوظة مهمة كمان: المشكلة التانية اللي سببت ظهور 3 حلقات مختلفة
متضاربة مع بعض ("الظل الغامض"، "النداء من الظلام"، "المنزل المهجور")
كانت في ملف الـ workflow نفسه (وليس هنا) — كان فيه trigger باسم
"on: push: branches: [main]" بيخلي أي push على main (حتى تعديل بسيط
في الكود وانت بتصلّح المشكلة) يشغّل الـ pipeline بالكامل من الصفر
ويولّد حلقة جديدة وينشرها فورًا. تم حذف الـ trigger ده من الـ workflow
المرفق هنا.

=== الإصلاحات السابقة (فيسبوك ويوتيوب) ===

1) فشل فيسبوك: metadata.facebook بتقبل بس حقل type ("reel")، مش title.
   العنوان أصلاً موجود في نص البوست نفسه (build_post_text).

2) Scheduled posts limit reached (10/10): Buffer بتحسب أي بوست لسه
   ماتنشرش (مجدول لوقت محدد) كـ "scheduled" ضد نفس الحد (10 لكل قناة).
   ضفنا preflight check (count_pending_posts) بيسأل Buffer قبل كل
   محاولة نشر، ولو وصل للحد بيتخطى المحاولة برسالة واضحة.
"""

import json
import os
import re
import sys
import time
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

# الفرق بين نشر الجزء الأول (٣ عصرًا) والجزء الثاني (٧ مساءً) = 4 ساعات
# بالضبط. غيّره من GitHub Secrets/Variables باسم PART2_DELAY_HOURS لو
# غيّرت مواعيد الكرون في الـ workflow.
PART_DELAY_HOURS = float(os.environ.get("PART2_DELAY_HOURS", "4"))

SCHEDULE_LIMIT_MARKER = "Scheduled posts limit"

CHANNEL_PENDING_LIMIT = int(os.environ.get("CHANNEL_PENDING_LIMIT", "10"))
ENABLE_PREFLIGHT_CHECK = os.environ.get("ENABLE_PREFLIGHT_CHECK", "true").lower() != "false"

# === تعديل جديد: لينك الجزء الأول جوه الجزء الثاني ===
# بعد نشر الجزء الأول، بنستنى Buffer يرجّع externalLink الحقيقي بتاعه
# (رابط الفيديو/البوست الفعلي عند المنصة نفسها — مش متاح غير بعد ما
# البوست يتنشر فعليًا مش بس يتجدول)، وبعدين بنحطه في:
#   - يوتيوب: نص وصف الفيديو (نفس متغيّر النص العادي بيتحط في description)
#   - فيسبوك: أول تعليق (firstComment) — مدعوم رسميًا من Buffer.
# انستجرام مُستبعد عن قصد: حقل firstComment بتاعها فيه bug معروف من
# Buffer نفسها (بيترفض بصمت)، وحتى لو اشتغل، انستجرام أصلاً مابيخليش
# لينكات قابلة للضغط في الكابشن ولا التعليقات (بس في البايو/الستيكرز).
LINKABLE_SERVICES = {"facebook", "youtube"}

# أقصى وقت (بالثانية) بنستناه بعد نشر الجزء الأول لحد ما نجيب الينك
# الحقيقي بتاعه، قبل ما نكمل وننشئ بوست الجزء الثاني من غيره. يوتيوب
# ممكن ياخد وقت أطول من فيسبوك بسبب معالجة الفيديو بعد الرفع.
LINK_WAIT_TIMEOUT_SECONDS = int(os.environ.get("LINK_WAIT_TIMEOUT_SECONDS", "360"))
LINK_WAIT_POLL_INTERVAL_SECONDS = int(os.environ.get("LINK_WAIT_POLL_INTERVAL_SECONDS", "15"))

GET_POST_STATUS_QUERY = """
query GetPostStatus($postId: PostId!) {
  post(id: $postId) {
    id
    status
    externalLink
  }
}
"""

GET_ORGANIZATIONS_QUERY = """
query GetOrganizations {
  account { organizations { id name } }
}
"""

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

# خريطة الخدمات ديناميكية عن طريق GitHub Secrets: أضف/تأكد من الـ
# Secrets دي في GitHub (Settings → Secrets → Actions):
#   BUFFER_YOUTUBE_CHANNEL_ID
#   BUFFER_FACEBOOK_CHANNEL_ID
#   BUFFER_INSTAGRAM_CHANNEL_ID
# ولازم BUFFER_CHANNEL_ID (المُستخدم فعليًا في النشر) يحتوي على نفس
# المعرّفات دي (مفصولة بفواصل)، عشان القناة تتعرّف صح.
def build_channel_services() -> dict[str, str]:
    mapping: dict[str, str] = {}
    env_map = {
        "BUFFER_YOUTUBE_CHANNEL_ID": "youtube",
        "BUFFER_FACEBOOK_CHANNEL_ID": "facebook",
        "BUFFER_INSTAGRAM_CHANNEL_ID": "instagram",
    }
    for env_name, service in env_map.items():
        channel_id = os.environ.get(env_name, "").strip()
        if channel_id:
            mapping[channel_id] = service

    # توافق مع الإعداد القديم لو الـ Secrets الجديدة لسه متضافتش.
    mapping.setdefault("6aaa8778ea19ca0bde57da16", "youtube")
    mapping.setdefault("6aaa853fea19ca0bde57b5f7", "facebook")
    return mapping


CHANNEL_SERVICES = build_channel_services()

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


def metadata_for(channel_id: str, title: str, first_comment: str | None = None) -> dict | None:
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
        # firstComment مدعوم فعليًا من Buffer لفيسبوك (بعكس انستجرام اللي
        # فيها bug معروف). بيُستخدم هنا لحط لينك الجزء الأول كأول تعليق
        # على بوست الجزء الثاني.
        facebook_metadata: dict = {"type": "reel"}
        if first_comment:
            facebook_metadata["firstComment"] = first_comment
        return {"facebook": facebook_metadata}
    if service == "instagram":
        return {"instagram": {"type": "reel", "shouldShareToFeed": True}}
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
    api_key: str, due_at: str, first_comment: str | None = None,
) -> dict:
    """
    due_at إلزامي دلوقتي (مش Optional). كل الأجزاء (الأول والتاني) بتتنشر
    بـ customScheduled بوقت محدد صراحةً، عشان كل القنوات تنشر في نفس
    اللحظة بالظبط بدل ما تعتمد على جدول Buffer الداخلي الخاص بكل قناة.
    first_comment (اختياري): بيتحط كأول تعليق — مدعوم فعليًا لفيسبوك بس
    (شوف metadata_for).
    """
    metadata = metadata_for(channel_id, title, first_comment=first_comment)

    variables = {
        "text": post_text, "channelId": channel_id, "videoUrl": video_url,
        "metadata": metadata, "dueAt": due_at,
    }
    try:
        return _send_create_post(CREATE_POST_MUTATION_SCHEDULED, variables, api_key)
    except RuntimeError as error:
        if SCHEDULE_LIMIT_MARKER not in str(error):
            raise
        # حالة استثنائية فقط: لو وصلنا لحد المجدولين عند Buffer، ننشر عن
        # طريق addToQueue كحل بديل أخير (وده قد يهبط في وقت مختلف حسب
        # جدول القناة الداخلي عند Buffer، لكنه أفضل من فشل النشر تمامًا).
        print(f"    ⚠️ {SCHEDULE_LIMIT_MARKER} — هنشر عن طريق addToQueue كحل بديل (قد يهبط في وقت مختلف حسب جدول Buffer الداخلي).")
        fallback_variables = {
            "text": post_text, "channelId": channel_id,
            "videoUrl": video_url, "metadata": metadata,
        }
        return _send_create_post(CREATE_POST_MUTATION_QUEUE, fallback_variables, api_key)


def get_organization_id(api_key: str) -> str:
    result = _send_graphql(GET_ORGANIZATIONS_QUERY, {}, api_key)
    organizations = result.get("account", {}).get("organizations", [])
    if not organizations:
        raise RuntimeError("No Buffer organization found for this API key.")
    return organizations[0]["id"]


def count_pending_posts(organization_id: str, channel_id: str, api_key: str) -> int:
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


def get_post_external_link(post_id: str, api_key: str) -> tuple[str, str | None]:
    """يرجّع (status, externalLink) لبوست معيّن. externalLink بيفضل None
    لحد ما البوست ينشر فعليًا عند المنصة (مش بس يتجدول عند Buffer)."""
    result = _send_graphql(GET_POST_STATUS_QUERY, {"postId": post_id}, api_key)
    post = result.get("post") or {}
    return str(post.get("status", "")).lower(), post.get("externalLink")


def wait_for_external_links(post_ids_by_channel: dict[str, str], api_key: str) -> dict[str, str | None]:
    """
    بتستنى لحد LINK_WAIT_TIMEOUT_SECONDS ثانية لحد ما تجيب externalLink
    الحقيقي لكل قناة من قنوات LINKABLE_SERVICES اللي نشرنا فيها الجزء
    الأول. أي قناة يتأخر أو يفشل نشرها الفعلي (تايم آوت، أو status
    error/failed) بترجع None ليها بدل ما توقف السكريبت كله — الجزء
    الثاني هينشر من غير لينك لتلك القناة بس، مش هيفشل بالكامل.
    """
    pending = dict(post_ids_by_channel)
    links: dict[str, str | None] = {channel_id: None for channel_id in pending}
    if not pending:
        return links

    deadline = time.monotonic() + LINK_WAIT_TIMEOUT_SECONDS
    print(f"⏳ بستنى لينكات الجزء الأول الحقيقية لـ {len(pending)} قناة (حد أقصى {LINK_WAIT_TIMEOUT_SECONDS}s)...")

    while pending and time.monotonic() < deadline:
        for channel_id in list(pending.keys()):
            post_id = pending[channel_id]
            service = CHANNEL_SERVICES.get(channel_id, "unknown")
            try:
                status, external_link = get_post_external_link(post_id, api_key)
            except Exception as error:
                print(f"  ⚠️ تعذّر فحص حالة بوست الجزء الأول لقناة {service}: {error}")
                continue

            if external_link:
                links[channel_id] = external_link
                print(f"  ✅ لينك الجزء الأول جاهز لـ {service}: {external_link}")
                del pending[channel_id]
            elif status in ("error", "failed"):
                print(f"  ❌ بوست الجزء الأول فشل عند Buffer لقناة {service} (status={status}) — مفيش لينك هيتاخد.")
                del pending[channel_id]

        if pending:
            time.sleep(LINK_WAIT_POLL_INTERVAL_SECONDS)

    for channel_id in pending:
        service = CHANNEL_SERVICES.get(channel_id, "unknown")
        print(
            f"  ⚠️ اتخطى وقت الانتظار ({LINK_WAIT_TIMEOUT_SECONDS}s) قبل ما نجيب لينك "
            f"الجزء الأول لقناة {service} — الجزء الثاني هيتنشر من غيره."
        )

    return links


def parse_channel_ids(raw: str) -> list[str]:
    raw = raw.replace(";", ",").replace("\n", ",")
    ids = [value.strip().strip("\"'") for value in raw.split(",") if value.strip()]
    return list(dict.fromkeys(ids))


def build_post_text(caption: str, title: str) -> str:
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
    """توقيت UTC بصيغة تقبلها Buffer، مثال: 2026-09-17T18:30:00.000Z.

    offset=0 للجزء الأول (يعني "الآن" تقريبًا -> نشر فوري فعليًا)،
    و offset=PART_DELAY_HOURS للجزء الثاني. بنضيف دقيقة أمان بسيطة لأول
    جزء عشان نضمن إن الوقت دايمًا في المستقبل (Buffer بيرفض dueAt في
    الماضي).
    """
    safety_buffer_minutes = 1 if hours_from_now == 0 else 0
    due = datetime.now(timezone.utc) + timedelta(
        hours=hours_from_now, minutes=safety_buffer_minutes
    )
    return due.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def resolve_parts(episode: dict) -> list[dict]:
    raw_parts = episode.get("parts") or [{}]
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
    if total <= 1:
        return title, caption

    part_title = f"{title} (الجزء {index})"
    if index < total:
        note = "🔻 الجزء الثاني ينشر اليوم الساعة 7 مساءً... تابعونا 👀"
        part_caption = f"{caption}\n\n{note}"
    else:
        note = "🎬 استكمال الجزء الأول ⬆️"
        part_caption = f"{note}\n\n{caption}"
    return part_title, part_caption


def main() -> None:
    api_key = os.environ.get("BUFFER_API_KEY", "").strip()
    raw_ids = os.environ.get("BUFFER_CHANNEL_ID", "")
    github_token = os.environ.get("GITHUB_TOKEN", "").strip()

    if not api_key or not github_token:
        sys.exit("BUFFER_API_KEY and GITHUB_TOKEN are required.")
    if not EPISODE_PATH.exists():
        sys.exit("state/current_episode.json is missing.")

    if raw_ids.strip():
        channel_ids = parse_channel_ids(raw_ids)
    else:
        channel_ids = list(CHANNEL_SERVICES.keys())
        if not channel_ids:
            sys.exit(
                "لا يوجد أي قناة: لازم BUFFER_CHANNEL_ID أو واحد على الأقل من "
                "BUFFER_YOUTUBE_CHANNEL_ID / BUFFER_FACEBOOK_CHANNEL_ID / "
                "BUFFER_INSTAGRAM_CHANNEL_ID يكون معرّف."
            )
        print(f"ℹ️ BUFFER_CHANNEL_ID فاضي — استخدمنا القنوات من الـ Secrets المنفصلة: {channel_ids}")

    unknown_channels = [cid for cid in channel_ids if cid not in CHANNEL_SERVICES]
    if unknown_channels:
        print(
            "⚠️ القنوات دي معرّفة في BUFFER_CHANNEL_ID لكن مش متعرّف على "
            "خدمتها (هتفشل غالبًا زي 'Instagram posts require a type'): "
            + ", ".join(unknown_channels)
            + " — تأكد إن نفس المعرّف موجود في BUFFER_INSTAGRAM_CHANNEL_ID "
              "(أو YOUTUBE/FACEBOOK حسب الحالة) في GitHub Secrets."
        )

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

    missing = [p for p in parts if not p["video_path"].exists() or p["video_path"].stat().st_size == 0]
    if missing:
        names = ", ".join(str(p["video_path"].relative_to(ROOT_DIR)) for p in missing)
        sys.exit(
            f"{names} is missing or empty. "
            f"(الحلقة مقسّمة لـ {len(parts)} جزء/أجزاء حسب current_episode.json؛ "
            f"تأكد إن assemble_video.py بيطلّع فيديو لكل جزء بنفس اللاحقة _part1/_part2)"
        )

    print(f"Channel service map resolved: {CHANNEL_SERVICES}")
    print(f"Configured Buffer channels: {len(channel_ids)}")
    print(f"Post title loaded: {title[:80]}")
    print(f"Episode parts: {len(parts)}")
    print(f"Part 2 delay: {PART_DELAY_HOURS}h (الجزء 1: ~الآن ← الجزء 2: +{PART_DELAY_HOURS}h) — كلاهما customScheduled بنفس اللحظة لكل القنوات.")
    hashtag_count = len(re.findall(r"(?<!\w)#\S+", caption))
    print(f"Hashtags detected: {hashtag_count}")

    overall_successes = 0
    overall_failures = []

    # هتتملى بـ {channel_id: buffer_post_id} لقنوات LINKABLE_SERVICES بس،
    # بعد نشر الجزء الأول. بعدين wait_for_external_links() بتحوّلها لـ
    # {channel_id: الرابط الحقيقي أو None}.
    part1_post_ids: dict[str, str] = {}
    part1_links: dict[str, str | None] = {}

    for part in parts:
        index, total = part["index"], part["total"]
        part_title, part_caption = augment_for_part(title, caption, index, total)
        post_text = build_post_text(part_caption, part_title)

        # كل الأجزاء بقت customScheduled بوقت محدد صراحةً. الجزء الأول
        # offset=0 (يعني فورًا تقريبًا)، والجزء الثاني offset=PART_DELAY_HOURS.
        # ده بيضمن إن كل القنوات (فيسبوك/يوتيوب/انستجرام) تنشر في نفس
        # اللحظة بالظبط، بدل ما تعتمد على جدول addToQueue الداخلي المختلف
        # لكل قناة (اللي كان سبب تضارب المواعيد).
        due_at = schedule_iso(PART_DELAY_HOURS * (index - 1))
        if index == 1:
            print(f"Part {index}/{total} scheduled for {due_at} (UTC) — ≈ الآن (٣ عصرًا بتوقيت القاهرة وقت تشغيل الكرون).")
        else:
            print(f"Part {index}/{total} scheduled for {due_at} (UTC) — ≈ ٧ مساءً بتوقيت القاهرة.")

        video_url = upload_media(part["video_path"], github_token)
        print(f"Part {index}/{total}: public video URL created successfully.")

        for number, channel_id in enumerate(channel_ids, 1):
            service = CHANNEL_SERVICES.get(channel_id, "unknown")

            # === تعديل جديد: حقن لينك الجزء الأول جوه الجزء الثاني ===
            # يوتيوب: اللينك بيتحط في نص الوصف نفسه (channel_post_text).
            # فيسبوك: اللينك بيتحط كأول تعليق (first_comment)، مش في النص.
            # أي قناة تانية (انستجرام مثلاً) تفضل زي ما هي من غير لينك.
            channel_post_text = post_text
            first_comment = None
            if index == 2 and service in LINKABLE_SERVICES:
                part1_link = part1_links.get(channel_id)
                if part1_link:
                    if service == "youtube":
                        channel_post_text = f"{post_text}\n\n🔗 شاهد الجزء الأول من هنا: {part1_link}"
                    elif service == "facebook":
                        first_comment = f"🔗 رابط الجزء الأول: {part1_link}"
                else:
                    print(f"  ℹ️ مفيش لينك جزء أول متاح لقناة {service} — الجزء الثاني هيتنشر من غيره.")

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
                    video_url, channel_post_text, part_title, channel_id, api_key,
                    due_at=due_at, first_comment=first_comment,
                )
                post_id = result.get("post", {}).get("id", "unknown")
                print(f"  Published {service}; post id: {post_id}")
                overall_successes += 1
                if index == 1 and service in LINKABLE_SERVICES and post_id != "unknown":
                    part1_post_ids[channel_id] = post_id
            except Exception as error:
                overall_failures.append((f"part{index}/{service}", str(error)))
                print(f"  Channel {number}/{len(channel_ids)} failed ({service}): {error}")

        # بعد ما ننشر الجزء الأول لكل القنوات، نستنى اللينكات الحقيقية
        # بتاعته قبل ما نبدأ نجهّز الجزء الثاني (لو هيكون فيه جزء ثاني).
        if index == 1 and total > 1:
            part1_links = wait_for_external_links(part1_post_ids, api_key)

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
