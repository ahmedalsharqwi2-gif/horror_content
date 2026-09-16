"""
generate_voice.py

يحوّل narration إلى صوت عربي باستخدام edge-tts، مع دعم:
1) تشكيل خفيف جداً في الكلمات التي قد يخطئ Edge TTS في نطقها فقط (وليس كل النص).
2) ترجمة SRT متزامنة مع توقيت الكلمات.
3) خلط موسيقى الرعب داخل ملف صوت واحد بنسبة 15%.
4) نهاية "هوك" قوية ومتنوعة تضمن اكتمال القصة بشكل شيّق ومناسب للترند.
5) تقسيم تلقائي للقصة إلى جزئين (ريلز) لو كانت طويلة عن حد الريلز الواحد،
   مع تنويه واضح في نهاية الجزء الأول (كليف هانجر) وبداية الجزء الثاني (استكمال).

الملفات الناتجة (لو القصة اتعملت في ريلز واحد):
- downloaded_clips/narration_voice.mp3
- downloaded_clips/narration_with_music.mp3
- downloaded_clips/narration.srt

الملفات الناتجة (لو القصة اتقسمت جزئين):
- downloaded_clips/narration_voice_part1.mp3 / narration_voice_part2.mp3
- downloaded_clips/narration_with_music_part1.mp3 / narration_with_music_part2.mp3
- downloaded_clips/narration_part1.srt / narration_part2.srt

الموسيقى المطلوبة:
- assets/background_music.mp3
"""

import asyncio
import json
import random
import re
import subprocess
import sys
from pathlib import Path

import edge_tts

SCRIPT_DIR = Path(__file__).parent
ROOT_DIR = SCRIPT_DIR.parent
STATE_DIR = ROOT_DIR / "state"
CLIPS_DIR = ROOT_DIR / "downloaded_clips"
ASSETS_DIR = ROOT_DIR / "assets"

EPISODE_PATH = STATE_DIR / "current_episode.json"
BACKGROUND_MUSIC = ASSETS_DIR / "background_music.mp3"

# ---------------------------------------------------------------------------
# إعدادات الصوت: إيقاع أبطأ وطبقة صوت أعمق قليلاً لإحساس رعب أكبر.
# ملاحظة مهمة وصادقة: أصوات edge-tts العربية (زي ar-EG-ShakirNeural) لسه
# مبتدعمش وسوم "style" العاطفية (زي cheerful/sad/terrified) المتاحة في بعض
# الأصوات الإنجليزية. يعني أقصى تحكم متاح فعلياً هو rate و pitch و volume،
# وهو اللي اتضبط هنا (أبطأ وأعمق من الإعداد الافتراضي) عشان يديك إحساس
# ترقّب وتوجّس. لو عايز رعب أقوى في الأداء نفسه، أفضل حل عملي إضافي هو
# إضافة "..." أو فواصل في نص الـ narration نفسه عند لحظات التشويق، لأن
# ده بيأثر على طول السكتة الطبيعية أثناء النطق.
# ---------------------------------------------------------------------------
VOICE = "ar-EG-ShakirNeural"
RATE = "-15%"
PITCH = "-9Hz"
VOLUME = "+0%"

MUSIC_VOLUME = 0.15
WORDS_PER_CAPTION_CHUNK = 6

# متوسط تقديري لعدد الكلمات في الثانية بعد تبطيء الإيقاع (RATE أعلاه)،
# يُستخدم فقط لتقدير مدة القصة قبل التوليد الفعلي، لتحديد هل نقسمها جزئين أو لا.
# لو حسّيت إن التقدير مش دقيق مع صوتك، غيّر الرقم ده على حسب التجربة الفعلية.
AVG_WORDS_PER_SECOND = 2.1

# أقصى مدة مقترحة للريلز الواحد (بالثانية) قبل ما نلجأ للتقسيم لجزئين.
# غيّرها لو عايز الريلز يكون أطول أو أقصر.
MAX_SINGLE_REEL_SECONDS = 75

# نهايات "هوك" متنوعة تضمن اكتمال القصة بشكل شيّق ومناسب للترند،
# بيتم اختيار واحدة عشوائياً في كل مرة عشان النهاية متتكررش بنفس الشكل
# في كل حلقة.
HOOK_ENDINGS = [
    "وهنا تنتهي القصة... لكن هل كنت ستفتح الباب لو كنت مكانه؟",
    "والسؤال اللي هيفضل عالق في دماغك: هل كنت هتكمل ولا هتهرب؟",
    "لحد دلوقتي محدش عارف حقيقة اللي حصل... إيه رأيك انت في اللي حصل؟",
    "والقصة خلصت هنا... بس هل تصدق إن ده كان مجرد صدفة؟",
]

# تنويه نهاية الجزء الأول عند التقسيم (كليف هانجر + دعوة صريحة لمتابعة الجزء الثاني).
PART_ONE_CLIFFHANGER = (
    "وفجأة توقف كل حاجة عند اللحظة دي بالظبط... "
    "تابعوني في الجزء القادم عشان تعرفوا اللي حصل بعد كده."
)

# مقدمة الجزء الثاني عند التقسيم (تنويه واضح إن ده استكمال للجزء الأول).
PART_TWO_INTRO = (
    "لو لسه ما شفتش الجزء الأول من القصة، شوفه الأول عشان تفهم كل حاجة، "
    "وبعدين نكمل مع بعض من هنا."
)

VOICE_AUDIO = CLIPS_DIR / "narration_voice.mp3"
FINAL_AUDIO = CLIPS_DIR / "narration_with_music.mp3"
SUBTITLES = CLIPS_DIR / "narration.srt"


def run(command: list[str]):
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(
            "❌ فشل الأمر:\n"
            + " ".join(command)
            + "\n\n"
            + result.stderr
        )
    return result


def light_diacritics(text: str) -> str:
    """
    تشكيل انتقائي جداً - فقط للكلمات التي غالباً هيخطئ Edge TTS في نطقها،
    وليس تشكيلاً كاملاً للنص. الهدف الوحيد مساعدة محرك النطق على النطق
    الصحيح، مش تغيير شكل النص المكتوب أو المبالغة في التشكيل.
    """
    text = re.sub(r"\s+", " ", text).strip()
    replacements = [
        ("إن الله", "إِنَّ اللّٰه"),
        ("أن الله", "أَنَّ اللّٰه"),
        ("الله", "اللّٰه"),
        ("اطمئن", "اِطْمَئِنّ"),
        ("اطمئني", "اِطْمَئِنِّي"),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    return text


def srt_time(seconds: float) -> str:
    milliseconds = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def two_lines(words: list[str]) -> str:
    """
    يقسّم مجموعة الكلمات على سطرين متوازنين قدر الإمكان.
    \\N يفهمها libass كسطر جديد، وكل سطر بيتمركز لوحده في المنتصف
    (Alignment=8 في ملف الترجمة في assemble_video.py) مش يمين ولا شمال.
    """
    words = [word.strip() for word in words if word.strip()]
    if len(words) <= 3:
        return " ".join(words)
    midpoint = (len(words) + 1) // 2
    return " ".join(words[:midpoint]) + r"\N" + " ".join(words[midpoint:])


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!؟…])\s+", text.strip())
    return [part.strip() for part in parts if part.strip()]


def build_final_narration(raw_narration: str) -> list[str]:
    """
    يجهز نص/نصوص القصة النهائية:
    - لو القصة قصيرة بما يكفي لريلز واحد -> يرجّع عنصر واحد بس، ينتهي
      بهوك قوي، وده اللي بيضمن إن القصة "تخلص" فعلاً في نفس الريلز.
    - لو القصة طويلة -> يرجّع عنصرين: جزء أول ينتهي بكليف هانجر + تنويه
      صريح إن فيه جزء تاني، وجزء ثاني يبدأ بتنويه استكمال وينتهي بالهوك
      النهائي. كده القصة بتخلص فعلياً، بس على ريلزين بدل ريلز واحد مقطوع.
    """
    text = raw_narration.strip()
    if not text.endswith((".", "؟", "!", "…")):
        text += "."

    sentences = split_sentences(text)
    total_words = sum(len(sentence.split()) for sentence in sentences)
    estimated_seconds = total_words / AVG_WORDS_PER_SECOND

    hook = random.choice(HOOK_ENDINGS)

    if estimated_seconds <= MAX_SINGLE_REEL_SECONDS or len(sentences) <= 1:
        if hook not in text:
            text = f"{text} {hook}"
        return [text]

    # تقسيم عند أقرب حد جملة لمنتصف عدد الكلمات، عشان القسمة تكون طبيعية
    # ومحدش يتقطع في نص كلامه.
    half_words = total_words / 2
    cumulative = 0
    split_index = len(sentences) - 1
    for index, sentence in enumerate(sentences):
        cumulative += len(sentence.split())
        if cumulative >= half_words:
            split_index = index
            break

    part_one_sentences = sentences[: split_index + 1]
    part_two_sentences = sentences[split_index + 1:]

    # لو القسمة طلعت مش متوازنة (جزء فاضي)، ارجع للقصة كجزء واحد بدل ما نكسرها.
    if not part_one_sentences or not part_two_sentences:
        if hook not in text:
            text = f"{text} {hook}"
        return [text]

    part_one = " ".join(part_one_sentences) + " " + PART_ONE_CLIFFHANGER
    part_two_body = " ".join(part_two_sentences)
    if hook not in part_two_body:
        part_two_body = f"{part_two_body} {hook}"
    part_two = f"{PART_TWO_INTRO} {part_two_body}"

    return [part_one, part_two]


async def synthesize_voice(text: str, voice_audio: Path, subtitles: Path):
    communicate = edge_tts.Communicate(
        text,
        VOICE,
        rate=RATE,
        pitch=PITCH,
        volume=VOLUME,
    )
    word_events = []
    with voice_audio.open("wb") as audio_file:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_file.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                word_events.append(chunk)

    if word_events:
        subtitle_blocks = []
        for index in range(0, len(word_events), WORDS_PER_CAPTION_CHUNK):
            group = word_events[index:index + WORDS_PER_CAPTION_CHUNK]
            start = group[0]["offset"] / 10_000_000
            end = (
                group[-1]["offset"] + group[-1]["duration"]
            ) / 10_000_000
            content = two_lines([event["text"] for event in group])
            subtitle_blocks.append((start, max(end, start + 0.25), content))
    else:
        # بعض إصدارات/أصوات Edge TTS لا ترسل WordBoundary.
        # نستخدم مدة ملف الصوت لتوليد SRT تقريبي بدلاً من إيقاف البناء.
        print("⚠️ Edge TTS لم يرجع WordBoundary؛ سيتم استخدام توقيت تقريبي.")
        probe = subprocess.run([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(voice_audio),
        ], capture_output=True, text=True)
        if probe.returncode != 0 or not probe.stdout.strip():
            sys.exit("❌ تعذر قراءة مدة ملف الصوت لإنشاء الترجمة.")
        audio_duration = float(probe.stdout.strip())
        words = re.findall(r"\S+", text)
        if not words:
            sys.exit("❌ النص فارغ ولا يمكن إنشاء ترجمة.")
        subtitle_blocks = []
        total_groups = (len(words) + WORDS_PER_CAPTION_CHUNK - 1) // WORDS_PER_CAPTION_CHUNK
        chunk_duration = audio_duration / total_groups
        for index in range(0, len(words), WORDS_PER_CAPTION_CHUNK):
            group = words[index:index + WORDS_PER_CAPTION_CHUNK]
            start = (index // WORDS_PER_CAPTION_CHUNK) * chunk_duration
            end = min(audio_duration, start + chunk_duration)
            subtitle_blocks.append((start, max(end, start + 0.25), two_lines(group)))

    srt_lines = []
    for number, (start, end, content) in enumerate(subtitle_blocks, 1):
        srt_lines.extend([
            str(number),
            f"{srt_time(start)} --> {srt_time(end)}",
            content,
            "",
        ])
    subtitles.write_text("\n".join(srt_lines), encoding="utf-8")


def mix_music_into_voice(voice_audio: Path, final_audio: Path):
    """ينتج ملفاً واحداً يحتوي على الصوت والموسيقى بنسبة 15%."""
    if not BACKGROUND_MUSIC.exists():
        print("⚠️ background_music.mp3 غير موجود؛ سيتم نسخ الصوت بدون موسيقى.")
        run([
            "ffmpeg", "-y", "-i", str(voice_audio),
            "-c:a", "libmp3lame", "-b:a", "192k", str(final_audio),
        ])
        return

    run([
        "ffmpeg", "-y",
        "-i", str(voice_audio),
        "-stream_loop", "-1", "-i", str(BACKGROUND_MUSIC),
        "-filter_complex",
        "[0:a]volume=1.0[voice];"
        "[1:a]volume=0.15[music];"
        "[voice][music]amix=inputs=2:duration=first:dropout_transition=3:normalize=0[aout]",
        "-map", "[aout]",
        "-c:a", "libmp3lame",
        "-b:a", "192k",
        "-shortest",
        str(final_audio),
    ])


def process_part(text: str, suffix: str) -> dict:
    voice_audio = CLIPS_DIR / f"narration_voice{suffix}.mp3"
    final_audio = CLIPS_DIR / f"narration_with_music{suffix}.mp3"
    subtitles = CLIPS_DIR / f"narration{suffix}.srt"

    asyncio.run(synthesize_voice(text, voice_audio, subtitles))
    mix_music_into_voice(voice_audio, final_audio)

    return {
        "text": text,
        "voice_audio": str(voice_audio),
        "final_audio": str(final_audio),
        "subtitles": str(subtitles),
    }


def main():
    if not EPISODE_PATH.exists():
        sys.exit("❌ state/current_episode.json غير موجود.")

    episode = json.loads(EPISODE_PATH.read_text(encoding="utf-8"))
    raw_narration = str(episode.get("narration", "")).strip()
    if not raw_narration:
        sys.exit("❌ حقل narration غير موجود أو فارغ.")

    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)

    parts_text = build_final_narration(raw_narration)
    parts_text = [light_diacritics(part) for part in parts_text]

    if len(parts_text) == 1:
        result = process_part(parts_text[0], suffix="")
        episode["narration"] = parts_text[0]
        episode["parts"] = [result]
        print(f"✅ صوت الراوي: {VOICE_AUDIO}")
        print(f"✅ الصوت النهائي مع الموسيقى: {FINAL_AUDIO}")
        print(f"✅ الترجمة المتزامنة: {SUBTITLES}")
    else:
        results = []
        for index, part_text in enumerate(parts_text, 1):
            result = process_part(part_text, suffix=f"_part{index}")
            results.append(result)
            print(f"✅ الجزء {index}: {result['final_audio']}")
        episode["narration"] = "\n\n---\n\n".join(parts_text)
        episode["parts"] = results
        print("⚠️ القصة طويلة عن حد الريلز الواحد، تم تقسيمها إلى جزئين (part1 / part2)")
        print("✅ تم إضافة تنويه استكمال في نهاية الجزء الأول وبداية الجزء الثاني.")

    EPISODE_PATH.write_text(
        json.dumps(episode, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"✅ مستوى الموسيقى: {int(MUSIC_VOLUME * 100)}%")


if __name__ == "__main__":
    main()
