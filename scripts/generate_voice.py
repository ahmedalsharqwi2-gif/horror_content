"""
generate_voice.py

يحوّل narration إلى صوت عربي باستخدام edge-tts، مع دعم:
1) نص التوليّد الأساسي (والترجمة/الكابشن) بدون أي تشكيل تمامًا — عشان
   الرسم على الشاشة ميحصل فيه مشاكل (تفكك حروف/رموز غريبة).
2) تشكيل خفيف جداً يُضاف فقط للكلمات الصعبة النطق (من قاموس صغير قابل
   للتوسعة تحت)، ويُستخدم فقط في النص المُرسل لمحرك الصوت (TTS) عشان
   يحسّن نُطق edge-tts لهذه الكلمات بالذات — بينما الترجمة (SRT) والكابشن
   يفضلوا تمامًا بدون تشكيل زي ما طلبت.
3) ترجمة SRT متزامنة مع توقيت الكلمات.
4) خلط موسيقى الرعب داخل ملف صوت واحد بنسبة 15%.
5) نهاية "هوك" قوية ومتنوعة تضمن اكتمال القصة بشكل شيّق ومناسب للترند.
6) === تعديل جديد مهم: التقسيم لجزئين بقى إلزاميًا دايمًا ===
   كان فيه شرط قديم: لو مدة القصة المقدّرة ≤ 80 ثانية، ترجع القصة كريلز
   واحد بس من غير تقسيم. ده كان بيكسر افتراض أساسي في publish_buffer.py
   (اللي مبني على وجود جزئين دايمًا: الجزء الأول ينشر الساعة 3 عصرًا،
   والجزء الثاني الساعة 7 مساءً على Buffer) — لو رجعت القصة جزء واحد بس،
   مفيش نشر مسائي أصلاً لتلك الحلقة، وده مخالف للمطلوب.
   دلوقتي كل قصة (طالما فيها جملتين على الأقل) بتتقسم لجزئين دايمًا، بغض
   النظر عن مدتها المقدّرة. الاستثناء الوحيد المتبقي: قصة من جملة واحدة
   بس (حالة نادرة جدًا/خطأ توليد) — مينفعش تتقسم منطقيًا فبترجع كريلز
   واحد استثنائيًا مع تحذير واضح في اللوج.
   كمان أُضيف تحذير مبكر لو مدة أي جزء بعد التقسيم قريبة من/متجاوزة حد
   الـ 90 ثانية الصلب المطبّق لاحقًا في assemble_video.py، عشان تعرف من
   اللوج إن فيه قص هيحصل في الفيديو النهائي بدل ما تكتشفه بالصدفة.
7) سكتات حقيقية بين الجمل أثناء الرواية (مش مجرد فاصلة في النص) لتحقيق
   إحساس رعب وتشويق فعلي، بمدد مختلفة حسب نوع نهاية الجملة (نقطة/سؤال/تعجب/...).

⚠️ ملاحظة عن اللهجة: التزام النص بالفصحى (وعدم استخدام العامية) بيتحكم
فيه على مستوى توليد النص نفسه في generate_script.py (والـ system prompt
في prompts/horror_system_prompt.md)، مش في هذا الملف — هذا الملف بياخد
narration جاهز ويحوّله لصوت فقط.

الملفات الناتجة (الوضع الطبيعي: القصة مقسّمة لجزئين دايمًا):
- downloaded_clips/narration_voice_part1.mp3 / narration_voice_part2.mp3
- downloaded_clips/narration_with_music_part1.mp3 / narration_with_music_part2.mp3
- downloaded_clips/narration_part1.ass / narration_part2.ass

الملفات الناتجة (استثناء نادر: قصة جملة واحدة بس، مينفعش تتقسم):
- downloaded_clips/narration_voice.mp3
- downloaded_clips/narration_with_music.mp3
- downloaded_clips/narration.ass

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

# الحد الأقصى الصلب لكل جزء (بالثانية)، نفس القيمة المطبّقة فعليًا في
# assemble_video.py (MAX_DURATION_SECONDS). بيُستخدم هنا بس كتحذير مبكر
# في اللوج لو جزء بعد التقسيم قريب من تجاوز الحد ده، مش لتحديد هل نقسّم
# أصلاً ولا لأ (التقسيم بقى إلزاميًا دايمًا بغض النظر عن المدة).
HARD_PART_LIMIT_SECONDS = 90
# هامش أمان تحذيري (أقل من الحد الصلب) عشان تاخد بالك قبل ما يوصل للقص
# الفعلي في assemble_video.py.
WARN_PART_SECONDS = 80

# سكتات حقيقية بين الجمل
PAUSE_AFTER_ELLIPSIS = 1.3
PAUSE_AFTER_QUESTION_EXCLAIM = 0.75
PAUSE_AFTER_PERIOD = 0.45
DEFAULT_PAUSE = 0.5

# === تعديل: إزالة أسئلة "لو كنت مكانه/مكانهم" ===
# النهايات القديمة كانت بتسأل المشاهد سؤال اختيار افتراضي ("هل كنت
# ستفتح الباب لو كنت مكانه؟"، "هل كنت ستكمل أم ستهرب؟") — وده مناسب لقصص
# "اختر مغامرتك" الخيالية، مش لقصص واقعية بتحكي حادثة حصلت فعلاً بالفعل.
# النهايات الجديدة بتفضل في دور الراوي اللي بيعلّق على غموض الحادثة نفسها
# (أسلوب شائع في محتوى الجرائم/الألغاز الحقيقية)، من غير ما تحوّل القصة
# لسيناريو اختيارات شخصية للمشاهد.
HOOK_ENDINGS = [
    "وحتى يومنا هذا، لم يستطع أحد أن يفسر ما حدث في تلك الليلة.",
    "ويبقى السؤال بلا إجابة حتى الآن: ماذا حدث فعلًا في تلك اللحظات الأخيرة؟",
    "وما زالت تفاصيل القضية غامضة، ولم تُغلق حتى يومنا هذا.",
    "وانتهت الأحداث عند هذا الحد، لكن الحقيقة الكاملة ربما لن تُعرف أبدًا.",
]

# === تعديل: إزالة إعلان "توقف كل شيء... تابعونا في الجزء القادم" ===
# الجملة القديمة كانت بتلفت النظر بشكل مباشر إن القصة اتقطعت صناعيًا
# (مرتبطة بحد الـ 90 ثانية)، بدل ما تحس القطعة إنها نقطة تشويق طبيعية
# نابعة من أحداث القصة نفسها. الإعلان عن وجود جزء ثانٍ أصلاً موجود في
# الكابشن (نص المنشور) اللي بيضيفه publish_buffer.py على الجزء الأول
# ("🔻 الجزء الثاني ينشر اليوم الساعة 7 مساءً... تابعونا 👀")، فمفيش داعي
# لتكرار نفس الإعلان بالصوت جوه الفيديو نفسه.
# دلوقتي الجزء الأول بينتهي عند آخر جملة من نصفه الأول زي ما كتبها
# الموديل بالظبط (المفروض تكون جملة تشويق طبيعية حسب توجيه
# horror_system_prompt.md)، من غير أي إضافة صوتية فوقها.
PART_ONE_CLIFFHANGER = ""  # اتشالت الجملة القديمة عن قصد — شوف الشرح فوق.

PART_TWO_INTRO = (
    "إن لم تكونوا قد شاهدتم الجزء الأول من القصة، فشاهدوه أولًا لتفهموا كل "
    "شيء، وبعد ذلك نكمل معًا من هنا."
)

VOICE_AUDIO = CLIPS_DIR / "narration_voice.mp3"
FINAL_AUDIO = CLIPS_DIR / "narration_with_music.mp3"
# .ass بدل .srt — الستايل والدقة بيتكتبوا جوه الملف نفسه (انظر
# build_ass_subtitles تحت)، فمفيش حاجة تتحط بعد كده في assemble_video.py.
SUBTITLES = CLIPS_DIR / "narration.ass"

# لازم تتطابق بالظبط مع TARGET_WIDTH/TARGET_HEIGHT في assemble_video.py،
# عشان PlayResX/PlayResY في الـ .ass يبقوا نفس دقة الفيديو الحقيقية.
VIDEO_W = 1080
VIDEO_H = 1920

SUBTITLE_STYLE_LINE = (
    "Style: Caption,Arial,64,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,"
    "1,0,0,0,100,100,0,0,1,3,0,8,60,60,260,1"
)


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


ARABIC_DIACRITICS_PATTERN = re.compile(
    r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED\u08D3-\u08E1\u08E3-\u08FF]"
)


def strip_diacritics(text: str) -> str:
    return ARABIC_DIACRITICS_PATTERN.sub("", text)


def normalize_text(text: str) -> str:
    text = strip_diacritics(text)
    return re.sub(r"\s+", " ", text).strip()


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
    def replace(match: re.Match) -> str:
        word = match.group(0)
        return HARD_WORDS_DIACRITICS.get(word, word)

    return _WORD_TOKEN_PATTERN.sub(replace, text)


def ass_time(seconds: float) -> str:
    centiseconds = max(0, int(round(seconds * 100)))
    hours, remainder = divmod(centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    secs, cs = divmod(remainder, 100)
    return f"{hours:d}:{minutes:02d}:{secs:02d}.{cs:02d}"


def build_ass_header() -> str:
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {VIDEO_W}\n"
        f"PlayResY: {VIDEO_H}\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"{SUBTITLE_STYLE_LINE}\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
    )


RTL_MARK = "\u200F"


def two_lines(words: list[str]) -> str:
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


def _estimated_seconds(text: str) -> float:
    return len(text.split()) / AVG_WORDS_PER_SECOND


def _warn_if_too_long(label: str, text: str) -> None:
    """تحذير مبكر فقط — القص الفعلي (لو حصل) بيحصل في assemble_video.py."""
    estimated = _estimated_seconds(text)
    if estimated >= HARD_PART_LIMIT_SECONDS:
        print(
            f"❌ {label}: المدة المقدّرة {estimated:.0f} ثانية ≥ الحد الصلب "
            f"{HARD_PART_LIMIT_SECONDS}s — هيحصل قص فعلي في نهاية الفيديو "
            f"في assemble_video.py. قلّل TARGET_WORDS في generate_script.py."
        )
    elif estimated >= WARN_PART_SECONDS:
        print(
            f"⚠️ {label}: المدة المقدّرة {estimated:.0f} ثانية قريبة من الحد "
            f"الصلب {HARD_PART_LIMIT_SECONDS}s — مفيش هامش أمان كبير."
        )


def build_final_narration(raw_narration: str) -> list[str]:
    """
    يجهز نصوص القصة النهائية (نسخة نظيفة بدون تشكيل)، مقسّمة دائمًا إلى
    جزأين (ريلين)، بغض النظر عن مدة القصة المقدّرة — التقسيم لجزء واحد بس
    بقى استثناء نادر جدًا (قصة من جملة واحدة بس، مينفعش تتقسم منطقيًا).
    """
    text = raw_narration.strip()
    if not text.endswith((".", "؟", "!", "…")):
        text += "."

    sentences = split_sentences(text)
    hook = random.choice(HOOK_ENDINGS)

    if len(sentences) <= 1:
        print(
            "⚠️ narration جملة واحدة بس — تعذّر تقسيمها لجزئين منطقيًا، "
            "هيتعمل ريلز واحد استثنائيًا (تحقق من جودة توليد generate_script.py)."
        )
        if hook not in text:
            text = f"{text} {hook}"
        _warn_if_too_long("الريلز الوحيد", text)
        return [text]

    total_words = sum(len(sentence.split()) for sentence in sentences)
    half_words = total_words / 2
    cumulative = 0
    split_index = len(sentences) - 1
    for index, sentence in enumerate(sentences):
        cumulative += len(sentence.split())
        if cumulative >= half_words:
            split_index = index
            break

    # نضمن إن كل جزء فيه جملة واحدة على الأقل (مفيش جزء فاضي)، حتى لو
    # نقطة المنتصف بالكلمات وقعت في آخر جملة.
    split_index = min(max(split_index, 0), len(sentences) - 2)

    part_one_sentences = sentences[: split_index + 1]
    part_two_sentences = sentences[split_index + 1:]

    part_one = " ".join(part_one_sentences)
    if PART_ONE_CLIFFHANGER:
        # لو حبيت ترجّع إعلان صوتي في المستقبل، ضيف نص في PART_ONE_CLIFFHANGER
        # فوق وهيتضاف هنا تلقائيًا — دلوقتي فاضي فعليًا (شوف الشرح فوق).
        part_one = f"{part_one} {PART_ONE_CLIFFHANGER}"
    part_two_body = " ".join(part_two_sentences)
    if hook not in part_two_body:
        part_two_body = f"{part_two_body} {hook}"
    part_two = f"{PART_TWO_INTRO} {part_two_body}"

    _warn_if_too_long("الجزء الأول", part_one)
    _warn_if_too_long("الجزء الثاني", part_two)

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

    dialogue_lines = []
    for start, end, content in subtitle_blocks:
        dialogue_lines.append(
            f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Caption,,0,0,0,,{content}"
        )
    ass_content = build_ass_header() + "\n".join(dialogue_lines) + "\n"
    subtitles.write_text(ass_content, encoding="utf-8")

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
    voice_audio = CLIPS_DIR / f"narration_voice{suffix}.mp3"
    final_audio = CLIPS_DIR / f"narration_with_music{suffix}.mp3"
    subtitles = CLIPS_DIR / f"narration{suffix}.ass"

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
        # استثناء نادر جدًا فقط (narration جملة واحدة بس) — شوف التحذير
        # اللي طبع فوق في build_final_narration.
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
        print("✅ القصة مقسّمة إلى جزئين (كل حلقة دايمًا جزئين، مش شرطي).")
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
