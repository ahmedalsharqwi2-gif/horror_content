"""
generate_voice.py

يحوّل narration إلى صوت عربي باستخدام edge-tts، ثم:
1) يضيف تشكيلًا خفيفًا في الكلمات الصعبة فقط.
2) ينشئ ترجمة SRT متزامنة مع توقيت الكلمات.
3) يخلط موسيقى الرعب داخل ملف صوت واحد بنسبة 15%.

الملفات الناتجة:
- downloaded_clips/narration_voice.mp3
- downloaded_clips/narration_with_music.mp3
- downloaded_clips/narration.srt

الموسيقى المطلوبة:
- assets/background_music.mp3
"""

import asyncio
import json
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
VOICE_AUDIO = CLIPS_DIR / "narration_voice.mp3"
FINAL_AUDIO = CLIPS_DIR / "narration_with_music.mp3"
SUBTITLES = CLIPS_DIR / "narration.srt"
BACKGROUND_MUSIC = ASSETS_DIR / "background_music.mp3"

VOICE = "ar-EG-ShakirNeural"
RATE = "-12%"
PITCH = "-7Hz"
VOLUME = "+0%"
MUSIC_VOLUME = 0.15
WORDS_PER_CAPTION_CHUNK = 6


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
    """تشكيل انتقائي للكلمات التي قد يخطئ Edge TTS في نطقها."""
    text = re.sub(r"\s+", " ", text).strip()
    replacements = [
        ("إن الله", "إِنَّ اللّٰه"),
        ("أن الله", "أَنَّ اللّٰه"),
        ("إنك", "إِنَّكَ"),
        ("إنكِ", "إِنَّكِ"),
        ("الله", "اللّٰه"),
        ("لكن", "لٰكِن"),
        ("لأن", "لِأَنَّ"),
        ("ألا", "أَلَا"),
        ("يا رب", "يَا رَبّ"),
        ("اطمئن", "اِطْمَئِنّ"),
        ("اطمئني", "اِطْمَئِنِّي"),
        ("مطمئن", "مُطْمَئِنّ"),
        ("حقا", "حَقًّا"),
        ("حقًا", "حَقًّا"),
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
    words = [word.strip() for word in words if word.strip()]
    if len(words) <= 3:
        return " ".join(words)
    midpoint = (len(words) + 1) // 2
    # \\N يفهمها libass كسطر جديد عند حرق SRT بواسطة ffmpeg.
    return " ".join(words[:midpoint]) + r"\N" + " ".join(words[midpoint:])


async def synthesize_voice(text: str):
    communicate = edge_tts.Communicate(
        text,
        VOICE,
        rate=RATE,
        pitch=PITCH,
        volume=VOLUME,
    )
    word_events = []
    with VOICE_AUDIO.open("wb") as audio_file:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_file.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                word_events.append(chunk)

    if not word_events:
        sys.exit("❌ Edge TTS لم يرجع توقيت الكلمات.")

    subtitle_blocks = []
    for index in range(0, len(word_events), WORDS_PER_CAPTION_CHUNK):
        group = word_events[index:index + WORDS_PER_CAPTION_CHUNK]
        start = group[0]["offset"] / 10_000_000
        end = (
            group[-1]["offset"] + group[-1]["duration"]
        ) / 10_000_000
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
    SUBTITLES.write_text("\n".join(srt_lines), encoding="utf-8")


def mix_music_into_voice():
    """ينتج ملفاً واحداً يحتوي على الصوت والموسيقى بنسبة 15%."""
    if not BACKGROUND_MUSIC.exists():
        print("⚠️ background_music.mp3 غير موجود؛ سيتم نسخ الصوت بدون موسيقى.")
        run([
            "ffmpeg", "-y", "-i", str(VOICE_AUDIO),
            "-c:a", "libmp3lame", "-b:a", "192k", str(FINAL_AUDIO),
        ])
        return

    run([
        "ffmpeg", "-y",
        "-i", str(VOICE_AUDIO),
        "-stream_loop", "-1", "-i", str(BACKGROUND_MUSIC),
        "-filter_complex",
        "[0:a]volume=1.0[voice];"
        "[1:a]volume=0.15[music];"
        "[voice][music]amix=inputs=2:duration=first:dropout_transition=3:normalize=0[aout]",
        "-map", "[aout]",
        "-c:a", "libmp3lame",
        "-b:a", "192k",
        "-shortest",
        str(FINAL_AUDIO),
    ])


def main():
    if not EPISODE_PATH.exists():
        sys.exit("❌ state/current_episode.json غير موجود.")

    episode = json.loads(EPISODE_PATH.read_text(encoding="utf-8"))
    narration = str(episode.get("narration", "")).strip()
    if not narration:
        sys.exit("❌ حقل narration غير موجود أو فارغ.")

    narration = light_diacritics(narration)
    episode["narration"] = narration
    EPISODE_PATH.write_text(
        json.dumps(episode, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)

    asyncio.run(synthesize_voice(narration))
    mix_music_into_voice()

    print(f"✅ صوت الراوي: {VOICE_AUDIO}")
    print(f"✅ الصوت النهائي مع الموسيقى: {FINAL_AUDIO}")
    print(f"✅ الترجمة المتزامنة: {SUBTITLES}")
    print(f"✅ مستوى الموسيقى: {int(MUSIC_VOLUME * 100)}%")


if __name__ == "__main__":
    main()
