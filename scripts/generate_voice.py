"""
generate_voice.py
يحوّل نص القصة (المُشكّل) لصوت بشري طبيعي باستخدام edge-tts،
وفي نفس الوقت بيولّد ملف ترجمة (.srt) بتوقيت متزامن مع الكلام،
عشان الكابشن يتحرك مع النطق لحظة بلحظة (Karaoke-style captions).
"""
import json
import asyncio
import sys
from pathlib import Path
import edge_tts  # pip install edge-tts>=6.1.9

SCRIPT_DIR = Path(__file__).parent
EPISODE_PATH = SCRIPT_DIR.parent / "state" / "current_episode.json"
OUTPUT_AUDIO = SCRIPT_DIR.parent / "downloaded_clips" / "narration.mp3"
OUTPUT_SUBTITLES = SCRIPT_DIR.parent / "downloaded_clips" / "narration.srt"

# غيّر ده لو عايز صوت مختلف بين الحلقات لتنويع أكبر
VOICE = "ar-EG-ShakirNeural"
RATE = "-4%"     # سرعة أبطأ شوية = إيقاع رعب أكتر
PITCH = "-2Hz"   # نبرة أعمق شوية

WORDS_PER_CAPTION_CHUNK = 2  # كام كلمة تظهر مع بعض في نفس اللحظة (2 بيدي إيقاع سلس)


async def synthesize_with_timing(text: str, audio_path: Path, subs_path: Path):
    communicate = edge_tts.Communicate(text, VOICE, rate=RATE, pitch=PITCH)

    word_events = []
    with open(audio_path, "wb") as audio_file:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_file.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                word_events.append(chunk)

    if not word_events:
        sys.exit("خطأ: edge-tts مرجعش أي توقيت كلمات — تأكد إن النسخة >=6.1.9")

    # نجمع كل WORDS_PER_CAPTION_CHUNK كلمة في "كيو" واحد بدل ما يكون كل كلمة لوحدها،
    # عشان القراءة تبقى مريحة للعين مع الحفاظ على التزامن الدقيق مع الصوت.
    submaker = edge_tts.SubMaker()
    for i in range(0, len(word_events), WORDS_PER_CAPTION_CHUNK):
        group = word_events[i:i + WORDS_PER_CAPTION_CHUNK]
        first, last = group[0], group[-1]
        merged_chunk = {
            "type": "WordBoundary",
            "offset": first["offset"],
            "duration": (last["offset"] + last["duration"]) - first["offset"],
            "text": " ".join(g["text"] for g in group),
        }
        submaker.feed(merged_chunk)

    subs_path.write_text(submaker.get_srt(), encoding="utf-8")


def main():
    if not EPISODE_PATH.exists():
        sys.exit("خطأ: مفيش current_episode.json — شغّل generate_script.py الأول")

    episode = json.loads(EPISODE_PATH.read_text(encoding="utf-8"))
    narration_text = episode["narration"]

    OUTPUT_AUDIO.parent.mkdir(parents=True, exist_ok=True)
    asyncio.run(synthesize_with_timing(narration_text, OUTPUT_AUDIO, OUTPUT_SUBTITLES))

    print(f"✅ اتولّد الصوت في: {OUTPUT_AUDIO}")
    print(f"✅ اتولّدت الترجمة المتزامنة في: {OUTPUT_SUBTITLES}")


if __name__ == "__main__":
    main()
