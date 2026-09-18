"""
generate_voice.py

يحوّل narration إلى صوت عربي باستخدام edge-tts، مع دعم:
1) نص التوليّد الأساسي (والترجمة/الكابشن) بدون أي تشكيل تمامًا — عشان
   الرسم على الشاشة ميحصل فيه مشاكل (تفكك حروف/رموز غريبة).
2) *جديد*: تشكيل خفيف جداً يُضاف فقط للكلمات الصعبة النطق (من قاموس صغير
   قابل للتوسعة تحت)، ويُستخدم فقط في النص المُرسل لمحرك الصوت (TTS)
   عشان يحسّن نُطق edge-tts لهذه الكلمات بالذات — بينما الترجمة (SRT)
   والكابشن يفضلوا تمامًا بدون تشكيل زي ما طلبت، لأن التشكيل ده مالوش
   لازمة بصرية وبيسبب مشاكل رسم.
3) ترجمة SRT متزامنة مع توقيت الكلمات.
4) خلط موسيقى الرعب داخل ملف صوت واحد بنسبة 15%.
5) نهاية "هوك" قوية ومتنوعة تضمن اكتمال القصة بشكل شيّق ومناسب للترند.
6) تقسيم تلقائي للقصة إلى جزئين (ريلز) بحد أقصى 90 ثانية (دقيقة ونصف)
   لكل جزء عشان يصلح لريلز فيسبوك/انستجرام، مع تنويه واضح في نهاية الجزء
   الأول (كليف هانجر) وبداية الجزء الثاني (استكمال).
7) سكتات حقيقية بين الجمل أثناء الرواية (مش مجرد فاصلة في النص) لتحقيق
   إحساس رعب وتشويق فعلي، بمدد مختلفة حسب نوع نهاية الجملة (نقطة/سؤال/تعجب/...).

⚠️ ملاحظة عن اللهجة: التزام النص بالفصحى (وعدم استخدام العامية) بيتحكم
فيه على مستوى توليد النص نفسه في generate_script.py (والـ system prompt
في prompts/horror_system_prompt.md)، مش في هذا الملف — هذا الملف بياخد
narration جاهز ويحوّله لصوت فقط.

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
# إعدادات الصوت
# ---------------------------------------------------------------------------
VOICE = "ar-EG-ShakirNeural"
RATE = "-15%"
PITCH = "-9Hz"
VOLUME = "+0%"

MUSIC_VOLUME = 0.15

WORDS_PER_CAPTION_CHUNK = 4

AVG_WORDS_PER_SECOND = 2.1

# أقصى مدة مقترحة للريلز الواحد (بالثانية) قبل ما نلجأ للتقسيم لجزئين.
# ريلز فيسبوك/انستجرام أقصاه دقيقة ونصف (90 ثانية) — بنستخدم 80 هنا كهامش
# أمان، والحد الفاصل النهائي (الصلب) موجود في assemble_video.py أيضاً.
MAX_SINGLE_REEL_SECONDS = 80

# سكتات حقيقية بين الجمل
PAUSE_AFTER_ELLIPSIS = 1.3
PAUSE_AFTER_QUESTION_EXCLAIM = 0.75
PAUSE_AFTER_PERIOD = 0.45
DEFAULT_PAUSE = 0.5

HOOK_ENDINGS = [
    "وهنا تنتهي القصة... لكن هل كنت ستفتح الباب لو كنت مكانه؟",
    "والسؤال الذي سيبقى عالقًا في ذهنك: هل كنت ستكمل أم ستهرب؟",
    "حتى الآن لا أحد يعرف حقيقة ما حدث... فما رأيك أنت فيما جرى؟",
    "والقصة انتهت هنا... فهل تصدق أن هذا كان مجرد صدفة؟",
]

PART_ONE_CLIFFHANGER = (
    "وفجأة توقف كل شيء عند هذه اللحظة بالضبط... "
    "تابعونا في الجزء القادم لتعرفوا ما حدث بعد ذلك."
)

PART_TWO_INTRO = (
    "إن لم تكونوا قد شاهدتم الجزء الأول من القصة، فشاهدوه أولًا لتفهموا كل "
    "شيء، وبعد ذلك نكمل معًا من هنا."
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


# حركات التشكيل العربي (فتحة/ضمة/كسرة/سكون/شدة/تنوين...) في نطاق يونيكود.
ARABIC_DIACRITICS_PATTERN = re.compile(
    r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED\u08D3-\u08E1\u08E3-\u08FF]"
)


def strip_diacritics(text: str) -> str:
    return ARABIC_DIACRITICS_PATTERN.sub("", text)


def normalize_text(text: str) -> str:
    """
    بدون أي تشكيل مضاف أو موجود على الإطلاق. يُستخدم لبناء النص "النظيف"
    الأساسي (اللي منه بنبني الترجمة/الكابشن)، وأي تشكيل خفيف للنطق
    بيتضاف بعد كده فقط على نسخة منفصلة خاصة بالصوت (انظر
    apply_light_diacritics تحت).
    """
    text = strip_diacritics(text)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# تشكيل خفيف لكلمات صعبة النطق (خاص بالصوت فقط، مش بالترجمة/الكابشن)
# ---------------------------------------------------------------------------
# قاموس صغير قابل للتوسعة: المفتاح هو الكلمة بدون تشكيل (لازم تطابق شكل
# الكلمة في النص بعد normalize_text تمامًا)، والقيمة هي نفس الكلمة
# بتشكيل جزئي يوضّح النطق الصحيح فقط عند الالتباس (مش تشكيل كامل).
# وسّع القاموس ده بأي كلمة لاحظت إن edge-tts بينطقها غلط في حلقاتك.
HARD_WORDS_DIACRITICS: dict[str, str] = {
    "عدة": "عِدّة",
    "قلبه": "قَلْبه",
    "لعنة": "لَعنة",
    "مسكون": "مَسكون",
    "جثة": "جُثّة",
    "همس": "هَمْس",
    "أشباح": "أَشباح",
    "ظل": "ظِلّ",
    "رعب": "رُعب",
    "صرخة": "صَرخة",
}

_WORD_TOKEN_PATTERN = re.compile(r"[\w\u0600-\u06FF]+", re.UNICODE)


def apply_light_diacritics(text: str) -> str:
    """
    يرجّع نسخة من النص (المفروض تكون داخلة نظيفة بدون تشكيل) بعد إضافة
    تشكيل خفيف فقط للكلمات الموجودة في HARD_WORDS_DIACRITICS، مع الحفاظ
    على باقي النص وعلامات الترقيم كما هي. تُستخدم هذه النسخة في توليد
    الصوت فقط (TTS)، ولا تُستخدم أبدًا في بناء الترجمة أو الكابشن.
    """
    def replace(match: re.Match) -> str:
        word = match.group(0)
        return HARD_WORDS_DIACRITICS.get(word, word)

    return _WORD_TOKEN_PATTERN.sub(replace, text)


def srt_time(seconds: float) -> str:
    milliseconds = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


RTL_MARK = "\u200F"  # Right-to-Left Mark: يفرض ترتيب الكلمات العربية صح


def two_lines(words: list[str]) -> str:
    """
    يقسّم مجموعة الكلمات على سطرين متوازنين قدر الإمكان. نضيف RTL_MARK
    في أول كل سطر عشان نضمن إن libass يرتّب الكلمات العربية من اليمين
    لليسار صح، حتى لو حصل التباس بسبب أرقام أو علامات ترقيم لاتينية.
    """
    words = [word.strip() for word in words if word.strip()]
    if not words:
        return ""
    if len(words) <= 2:
        return RTL_MARK + " ".join(words)
    midpoint = (len(words) + 1) // 2
    line_one = RTL_MARK + " ".join(words[:midpoint])
    line_two = RTL_MARK + " ".join(words[midpoint:])
    return line_one + r"\N" + line_two


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
    يجهز نص/نصوص القصة النهائية (نسخة نظيفة بدون تشكيل):
    - لو القصة قصيرة بما يكفي لريلز واحد (≤ MAX_SINGLE_REEL_SECONDS)
      -> يرجّع عنصر واحد بس، ينتهي بهوك قوي.
    - لو القصة طويلة -> يرجّع عنصرين، كل واحد منهم بحجم يقارب نصف القصة
      عشان يفضل الجزء الواحد جوه حد الـ 90 ثانية بتاع الريلز.
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
    كل جملة هنا مفروض تكون داخلة ومعاها التشكيل الخفيف (لو الكلمة موجودة
    في القاموس) — ده اللي بيتبعت فعليًا لـ edge-tts. الترجمة بعدين بتشيل
    أي تشكيل من نص الأحداث (WordBoundary) قبل ما تظهر على الشاشة.
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


def synthesize_voice(voice_text: str, voice_audio: Path, subtitles: Path, work_prefix: str):
    """
    voice_text: النص المُرسل فعليًا للصوت — قد يحتوي تشكيلًا خفيفًا على
    كلمات صعبة (من apply_light_diacritics). الترجمة الناتجة (subtitles)
    بتُبنى دايمًا من نص الأحداث بعد تجريده من أي تشكيل (strip_diacritics)،
    فتفضل الترجمة نظيفة 100% زي ما طلبت.
    """
    sentences = split_sentences(voice_text)
    if not sentences:
        sys.exit("❌ النص فارغ ولا يمكن إنشاء صوت.")

    segments = asyncio.run(synthesize_sentences(sentences, work_prefix))

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
                    # نجرّد التشكيل الخفيف فورًا هنا عشان الترجمة تفضل نظيفة.
                    "text": strip_diacritics(event["text"]),
                })
        else:
            print("⚠️ Edge TTS لم يرجع WordBoundary لجملة؛ سيتم استخدام توقيت تقريبي لها.")
            words = re.findall(r"\S+", segment["sentence"] or "")
            if words:
                per_word = segment["duration"] / len(words)
                for word_index, word in enumerate(words):
                    all_word_events.append({
                        "offset": int((cumulative_seconds + word_index * per_word) * 10_000_000),
                        "duration": int(per_word * 10_000_000),
                        "text": strip_diacritics(word),
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


def process_part(clean_text: str, suffix: str) -> dict:
    """clean_text: النص بدون أي تشكيل (المصدر الوحيد للحقيقة). يُبنى منه
    voice_text (بتشكيل خفيف لكلمات صعبة) فقط للاستخدام الداخلي في TTS."""
    voice_audio = CLIPS_DIR / f"narration_voice{suffix}.mp3"
    final_audio = CLIPS_DIR / f"narration_with_music{suffix}.mp3"
    subtitles = CLIPS_DIR / f"narration{suffix}.srt"

    voice_text = apply_light_diacritics(clean_text)
    synthesize_voice(voice_text, voice_audio, subtitles, work_prefix=suffix.strip("_") or "single")
    mix_music_into_voice(voice_audio, final_audio)

    return {
        "text": clean_text,
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
        print("⚠️ القصة طويلة عن حد الريلز الواحد (90 ثانية)، تم تقسيمها إلى جزئين")
        print("✅ تم إضافة تنويه استكمال في نهاية الجزء الأول وبداية الجزء الثاني.")

    EPISODE_PATH.write_text(
        json.dumps(episode, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"✅ مستوى الموسيقى: {int(MUSIC_VOLUME * 100)}%")
    print("✅ التشكيل مُزال بالكامل من الترجمة/الكابشن، ومُضاف بشكل خفيف فقط في صوت الكلمات الصعبة")
    print("✅ تم إضافة سكتات حقيقية بين الجمل داخل ملف الصوت")


if __name__ == "__main__":
    main()
