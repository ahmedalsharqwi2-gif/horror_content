"""
generate_voice.py

يحوّل narration إلى صوت عربي باستخدام edge-tts، مع دعم:
1) بدون أي تشكيل مضاف على النص (اتلغى بناءً على طلبك لأنه كان غير مظبوط).
2) ترجمة SRT متزامنة مع توقيت الكلمات.
3) خلط موسيقى الرعب داخل ملف صوت واحد بنسبة 15%.
4) نهاية "هوك" قوية ومتنوعة تضمن اكتمال القصة بشكل شيّق ومناسب للترند.
5) تقسيم تلقائي للقصة إلى جزئين (ريلز) لو كانت طويلة عن حد الريلز الواحد،
   مع تنويه واضح في نهاية الجزء الأول (كليف هانجر) وبداية الجزء الثاني (استكمال).
6) سكتات حقيقية بين الجمل أثناء الرواية (مش مجرد فاصلة في النص) لتحقيق
   إحساس رعب وتشويق فعلي، بمدد مختلفة حسب نوع نهاية الجملة (نقطة/سؤال/تعجب/...).

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

ملاحظة مهمة: السكريبت ده بياخد نص القصة (narration) جاهز من
state/current_episode.json. توليد نص القصة نفسه (اللي حاليًا بيبقى "هبل"
على حد وصفك) مش جزء من السكريبت ده ولا من assemble_video.py - لو عايز
تحسين جودة/واقعية القصص، ابعتلي السكريبت اللي بيكتب narration وهساعدك فيه.
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
# ملاحظة صادقة: أصوات edge-tts العربية (زي ar-EG-ShakirNeural) لسه مبتدعمش
# وسوم "style" العاطفية (زي cheerful/sad/terrified) المتاحة في بعض الأصوات
# الإنجليزية. يعني أقصى تحكم متاح فعليًا هو rate و pitch و volume + السكتات
# الحقيقية بين الجمل اللي بنضيفها دلوقتي تحت (ده اللي فعليًا بيفرق في
# إحساس الرعب أكتر من أي إعداد تاني).
# ---------------------------------------------------------------------------
VOICE = "ar-EG-ShakirNeural"
RATE = "-15%"
PITCH = "-9Hz"
VOLUME = "+0%"

MUSIC_VOLUME = 0.15

# قللنا عدد الكلمات في الكابشن الواحد عشان يبقى مناسب لحجم الخط الكبير
# (FontSize=56 Bold) في assemble_video.py من غير ما يحصل تكدّس أو تجاوز
# للشاشة.
WORDS_PER_CAPTION_CHUNK = 4

# متوسط تقديري لعدد الكلمات في الثانية بعد تبطيء الإيقاع (RATE أعلاه)،
# يُستخدم فقط لتقدير مدة القصة قبل التوليد الفعلي، لتحديد هل نقسمها جزئين أو لا.
AVG_WORDS_PER_SECOND = 2.1

# أقصى مدة مقترحة للريلز الواحد (بالثانية) قبل ما نلجأ للتقسيم لجزئين.
MAX_SINGLE_REEL_SECONDS = 75

# مدد السكتات الحقيقية (بالثانية) بين الجمل، حسب نوع نهاية الجملة.
# دي سكتات فعلية في ملف الصوت نفسه (مش مجرد علامة ترقيم)، وده اللي بيدي
# إحساس الترقّب والرعب أثناء الرواية.
PAUSE_AFTER_ELLIPSIS = 1.3   # بعد "..." أو "…" -> سكتة طويلة للتشويق
PAUSE_AFTER_QUESTION_EXCLAIM = 0.75  # بعد "؟" أو "!"
PAUSE_AFTER_PERIOD = 0.45    # بعد "."
DEFAULT_PAUSE = 0.5

# نهايات "هوك" متنوعة تضمن اكتمال القصة بشكل شيّق ومناسب للترند،
# بيتم اختيار واحدة عشوائياً في كل مرة عشان النهاية متتكررش بنفس الشكل.
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


def normalize_text(text: str) -> str:
    """
    بدون أي تشكيل مضاف - التشكيل اتلغى بالكامل بناءً على طلبك لأن الناتج
    كان غير مظبوط. الدالة دي بتعمل بس تنظيف مسافات، من غير إضافة أي حركات.
    """
    return re.sub(r"\s+", " ", text).strip()


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
    if len(words) <= 2:
        return " ".join(words)
    midpoint = (len(words) + 1) // 2
    return " ".join(words[:midpoint]) + r"\N" + " ".join(words[midpoint:])


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!؟…])\s+", text.strip())
    return [part.strip() for part in parts if part.strip()]


def pause_duration_for(sentence: str) -> float:
    stripped = sentence.strip()
    if stripped.endswith("…") or stripped.endswith("..."):
        return PAUSE_AFTER_ELLIPSIS
    if stripped.endswith("؟") or stripped.endswith("!"):
        return PAUSE_AFTER_QUESTION_EXCLAIM
    if stripped.endswith("."):
        return PAUSE_AFTER_PERIOD
    return DEFAULT_PAUSE


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


def probe_duration(path: Path) -> float:
    result = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ], capture_output=True, text=True)
    if result.returncode != 0 or not result.stdout.strip():
        sys.exit(f"❌ تعذر قراءة مدة الملف: {path}")
    return float(result.stdout.strip())


def build_silence_clip(duration: float, path: Path):
    run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
        "-t", f"{duration:.3f}",
        "-c:a", "libmp3lame", "-b:a", "192k",
        str(path),
    ])


async def synthesize_sentences(sentences: list[str], work_prefix: str) -> list[dict]:
    """
    بيولّد كل جملة في القصة كملف صوت منفصل، وبعد كل جملة (غير الأخيرة)
    بيولّد ملف سكتة حقيقي بمدة تناسب نوع نهاية الجملة. ده اللي بيحقق
    سكتات فعلية أثناء الرواية بدل الاعتماد على علامات الترقيم بس.
    """
    segments = []
    for index, sentence in enumerate(sentences):
        seg_path = CLIPS_DIR / f"_seg_{work_prefix}_{index:03d}.mp3"
        events = []
        communicate = edge_tts.Communicate(
            sentence, VOICE, rate=RATE, pitch=PITCH, volume=VOLUME,
        )
        with seg_path.open("wb") as audio_file:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_file.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    events.append(chunk)

        duration = probe_duration(seg_path)
        segments.append({
            "path": seg_path,
            "duration": duration,
            "events": events,
            "sentence": sentence,
            "is_silence": False,
        })

        if index < len(sentences) - 1:
            pause = pause_duration_for(sentence)
            pause_path = CLIPS_DIR / f"_pause_{work_prefix}_{index:03d}.mp3"
            build_silence_clip(pause, pause_path)
            segments.append({
                "path": pause_path,
                "duration": pause,
                "events": None,
                "sentence": None,
                "is_silence": True,
            })

    return segments


def synthesize_voice(text: str, voice_audio: Path, subtitles: Path, work_prefix: str):
    sentences = split_sentences(text)
    if not sentences:
        sys.exit("❌ النص فارغ ولا يمكن إنشاء صوت.")

    segments = asyncio.run(synthesize_sentences(sentences, work_prefix))

    # دمج كل المقاطع (صوت + سكتات) في ملف صوت واحد متصل، عشان السكتات
    # تتحس فعلياً أثناء رواية القصة.
    inputs = []
    for segment in segments:
        inputs += ["-i", str(segment["path"])]
    concat_filter = (
        "".join(f"[{i}:a]" for i in range(len(segments)))
        + f"concat=n={len(segments)}:v=0:a=1[aout]"
    )
    run([
        "ffmpeg", "-y", *inputs,
        "-filter_complex", concat_filter,
        "-map", "[aout]",
        "-c:a", "libmp3lame", "-b:a", "192k",
        str(voice_audio),
    ])

    # بناء توقيت الترجمة على تايم لاين الملف المدموج بالكامل (صوت + سكتات).
    all_word_events = []
    cumulative_seconds = 0.0
    for segment in segments:
        if segment["is_silence"]:
            cumulative_seconds += segment["duration"]
            continue

        if segment["events"]:
            for event in segment["events"]:
                all_word_events.append({
                    "offset": event["offset"] + int(cumulative_seconds * 10_000_000),
                    "duration": event["duration"],
                    "text": event["text"],
                })
        else:
            # بعض إصدارات/أصوات Edge TTS لا ترسل WordBoundary لجملة معينة.
            # نوزّع توقيت تقريبي على كلمات الجملة دي فقط، باستخدام مدتها الفعلية.
            print("⚠️ Edge TTS لم يرجع WordBoundary لجملة؛ سيتم استخدام توقيت تقريبي لها.")
            words = re.findall(r"\S+", segment["sentence"] or "")
            if words:
                per_word = segment["duration"] / len(words)
                for word_index, word in enumerate(words):
                    all_word_events.append({
                        "offset": int((cumulative_seconds + word_index * per_word) * 10_000_000),
                        "duration": int(per_word * 10_000_000),
                        "text": word,
                    })

        cumulative_seconds += segment["duration"]

    if not all_word_events:
        sys.exit("❌ تعذر إنشاء توقيت الترجمة.")

    subtitle_blocks = []
    for index in range(0, len(all_word_events), WORDS_PER_CAPTION_CHUNK):
        group = all_word_events[index:index + WORDS_PER_CAPTION_CHUNK]
        start = group[0]["offset"] / 10_000_000
        end = (group[-1]["offset"] + group[-1]["duration"]) / 10_000_000
        content = two_lines([event["text"] for event in group])
        subtitle_blocks.append((start, max(end, start + 0.25), content))

    srt_lines = []
    for number, (start, end, content) in enumerate(subtitle_blocks, 1):
        srt_lines.extend([
            str(number),
            f"{srt_time(start)} --> {srt_time(end)}",
            content,
            "",
        ])
    subtitles.write_text("\n".join(srt_lines), encoding="utf-8")

    for segment in segments:
        Path(segment["path"]).unlink(missing_ok=True)


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

    synthesize_voice(text, voice_audio, subtitles, work_prefix=suffix.strip("_") or "single")
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
    parts_text = [normalize_text(part) for part in parts_text]

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
    print("✅ تم إلغاء التشكيل بالكامل من النص")
    print("✅ تم إضافة سكتات حقيقية بين الجمل داخل ملف الصوت")


if __name__ == "__main__":
    main()
