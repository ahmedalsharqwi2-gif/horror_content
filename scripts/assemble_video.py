"""
assemble_video.py

يجمع المقاطع مع الصوت النهائي الناتج من generate_voice.py، ويحرق
الترجمة الصغيرة المتزامنة أعلى الشاشة أسفل منطقة الكاميرا الأمامية.
الموسيقى مدمجة مسبقاً داخل narration_with_music.mp3، لذلك لا نخلطها هنا مرة أخرى.
"""

import json
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
ROOT_DIR = SCRIPT_DIR.parent
STATE_DIR = ROOT_DIR / "state"
CLIPS_DIR = ROOT_DIR / "downloaded_clips"
OUTPUT_DIR = ROOT_DIR / "output"

FETCHED_CLIPS_PATH = STATE_DIR / "fetched_clips.json"
FINAL_AUDIO = CLIPS_DIR / "narration_with_music.mp3"
SUBTITLES = CLIPS_DIR / "narration.srt"
FINAL_OUTPUT = OUTPUT_DIR / "final_video.mp4"

TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920
MAX_DURATION_SECONDS = 178

# نص صغير، سطران كحد أقصى، أعلى الشاشة أسفل منطقة الكاميرا الأمامية.
SUBTITLE_STYLE = (
    "FontName=Arial,FontSize=30,Bold=0,"
    "PrimaryColour=&H00FFFFFF,OutlineColour=&H99000000,"
    "BorderStyle=1,Outline=2,Shadow=1,"
    "Alignment=8,MarginL=70,MarginR=70,MarginV=145,"
    "WrapStyle=2,Spacing=0"
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


def get_audio_duration(path: Path) -> float:
    result = run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ])
    return float(result.stdout.strip())


def normalize_clip(input_path: Path, output_path: Path, duration: float):
    run([
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-t", str(duration),
        "-vf",
        f"scale={TARGET_WIDTH}:{TARGET_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={TARGET_WIDTH}:{TARGET_HEIGHT},fps=24",
        "-an",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        str(output_path),
    ])


def concat_clips(paths: list[Path], output_path: Path):
    if not paths:
        sys.exit("❌ لا توجد مقاطع لتجميعها.")

    list_file = paths[0].parent / "concat_list.txt"
    list_file.write_text(
        "\n".join(f"file '{path.name}'" for path in paths),
        encoding="utf-8",
    )
    run([
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        str(output_path),
    ])


def add_audio_and_subtitles(video_path: Path):
    # ملف الصوت يحتوي بالفعل على الراوي + الموسيقى بنسبة 15%.
    subtitle_filter = f"subtitles={SUBTITLES}:force_style='{SUBTITLE_STYLE}'"
    run([
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", str(FINAL_AUDIO),
        "-vf", subtitle_filter,
        "-map", "0:v",
        "-map", "1:a",
        "-t", str(MAX_DURATION_SECONDS),
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        "-movflags", "+faststart",
        str(FINAL_OUTPUT),
    ])


def main():
    required = [FETCHED_CLIPS_PATH, FINAL_AUDIO, SUBTITLES]
    for path in required:
        if not path.exists():
            sys.exit(f"❌ الملف غير موجود: {path}")

    clips = json.loads(FETCHED_CLIPS_PATH.read_text(encoding="utf-8"))
    if not clips:
        sys.exit("❌ fetched_clips.json فارغ.")

    narration_duration = min(get_audio_duration(FINAL_AUDIO), MAX_DURATION_SECONDS)
    duration_per_clip = max(narration_duration / len(clips), 2.0)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    normalized = []
    for index, clip in enumerate(clips):
        output_path = CLIPS_DIR / f"norm_{index:02d}.mp4"
        normalize_clip(Path(clip["file"]), output_path, duration_per_clip)
        normalized.append(output_path)

    concatenated = CLIPS_DIR / "concatenated.mp4"
    concat_clips(normalized, concatenated)
    add_audio_and_subtitles(concatenated)

    print(f"✅ الفيديو النهائي: {FINAL_OUTPUT}")
    print(f"✅ المدة: {narration_duration:.1f} ثانية")
    print("✅ النص: صغير، سطران، أعلى الشاشة أسفل الكاميرا")
    print("✅ الصوت: narration_with_music.mp3")
    print("✅ الموسيقى مدمجة مسبقاً بنسبة 15%")


if __name__ == "__main__":
    main()
