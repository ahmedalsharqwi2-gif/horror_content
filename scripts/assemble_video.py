"""
assemble_video.py
يجمع الكليبات + الصوت + موسيقى خلفية في فيديو نهائي واحد 9:16 بـ ffmpeg.

يحتاج: ffmpeg متثبت (متوفر جاهز على GitHub Actions ubuntu-latest بدون أي إعداد)
اختياري: ملف موسيقى في assets/background_music.mp3 (مجاني من Pixabay Audio
أو YouTube Audio Library)
"""
import json
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
STATE_DIR = SCRIPT_DIR.parent / "state"
CLIPS_DIR = SCRIPT_DIR.parent / "downloaded_clips"
ASSETS_DIR = SCRIPT_DIR.parent / "assets"
OUTPUT_DIR = SCRIPT_DIR.parent / "output"

FETCHED_CLIPS_PATH = STATE_DIR / "fetched_clips.json"
NARRATION_AUDIO = CLIPS_DIR / "narration.mp3"
NARRATION_SUBTITLES = CLIPS_DIR / "narration.srt"
BACKGROUND_MUSIC = ASSETS_DIR / "background_music.mp3"  # اختياري
FINAL_OUTPUT = OUTPUT_DIR / "final_video.mp4"

TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920
MAX_DURATION_SECONDS = 178  # أمان تحت حد الـ 180 ثانية بتاع الريلز/الشورتس مباشرة

# موضع الترجمة: قريب من أعلى الشاشة (تحت مكان كاميرا الموبايل الأمامية مباشرة)
# مش تحت خالص عشان مايتغطاش بأزرار التفاعل اللي بتظهر أسفل الشاشة في كل المنصات
SUBTITLE_STYLE = (
    "FontName=Arial,FontSize=64,Bold=1,"
    "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
    "BorderStyle=3,Outline=3,Shadow=0,"
    "Alignment=8,MarginV=230"
)


def run(cmd: list[str], cwd: Path | None = None):
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(cwd) if cwd else None)
    if result.returncode != 0:
        sys.exit(f"❌ فشل الأمر:\n{' '.join(cmd)}\n\n{result.stderr}")
    return result


def get_audio_duration(path: Path) -> float:
    result = run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ])
    return float(result.stdout.strip())


def normalize_clip(input_path: Path, output_path: Path, duration_each: float):
    """يوحّد مقاس/نسبة كل كليب لـ 9:16 ويقصّه للمدة المطلوبة، بدون صوت أصلي."""
    run([
        "ffmpeg", "-y", "-i", str(input_path),
        "-t", str(duration_each),
        "-vf",
        f"scale={TARGET_WIDTH}:{TARGET_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={TARGET_WIDTH}:{TARGET_HEIGHT},fps=24",
        "-an",  # نشيل صوت الكليب الأصلي، هنستخدم صوتنا احنا بس
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        str(output_path),
    ])


def concat_clips(clip_paths: list[Path], output_path: Path):
    concat_list_path = clip_paths[0].parent / "concat_list.txt"
    concat_list_path.write_text(
        "\n".join(f"file '{p.name}'" for p in clip_paths), encoding="utf-8"
    )
    run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat_list_path),
        "-c", "copy", str(output_path),
    ])


def mux_audio_and_burn_subtitles(
    video_path: Path,
    narration_path: Path,
    subtitles_path: Path,
    music_path: Path | None,
    output_path: Path,
):
    # المسار بيتحط زي ما هو (نسبي، من غير أي ':') عشان فلتر subtitles في ffmpeg
    # بيتعامل مع الـ ':' كفاصل بارامترات، فمفيش داعي لأي escaping هنا على لينكس.
    subtitle_filter = f"subtitles={subtitles_path}:force_style='{SUBTITLE_STYLE}'"

    if music_path and music_path.exists():
        filter_complex = (
            "[1:a]volume=1.0[voice];"
            "[2:a]volume=0.12[music];"
            "[voice][music]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        )
        run([
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-i", str(narration_path),
            "-i", str(music_path),
            "-filter_complex", filter_complex,
            "-vf", subtitle_filter,
            "-map", "0:v", "-map", "[aout]",
            "-t", str(MAX_DURATION_SECONDS),
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-shortest",
            str(output_path),
        ])
    else:
        run([
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-i", str(narration_path),
            "-vf", subtitle_filter,
            "-map", "0:v", "-map", "1:a",
            "-t", str(MAX_DURATION_SECONDS),
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-shortest",
            str(output_path),
        ])


def main():
    if not FETCHED_CLIPS_PATH.exists():
        sys.exit("خطأ: مفيش fetched_clips.json — شغّل fetch_clips.py الأول")
    if not NARRATION_AUDIO.exists():
        sys.exit("خطأ: مفيش narration.mp3 — شغّل generate_voice.py الأول")
    if not NARRATION_SUBTITLES.exists():
        sys.exit("خطأ: مفيش narration.srt — شغّل generate_voice.py الأول")

    clips_meta = json.loads(FETCHED_CLIPS_PATH.read_text(encoding="utf-8"))
    narration_duration = min(get_audio_duration(NARRATION_AUDIO), MAX_DURATION_SECONDS)
    duration_per_clip = max(narration_duration / len(clips_meta), 2.0)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    normalized_paths = []

    for i, clip in enumerate(clips_meta):
        norm_path = CLIPS_DIR / f"norm_{i:02d}.mp4"
        normalize_clip(Path(clip["file"]), norm_path, duration_per_clip)
        normalized_paths.append(norm_path)

    concatenated_path = CLIPS_DIR / "concatenated.mp4"
    concat_clips(normalized_paths, concatenated_path)

    music_path = BACKGROUND_MUSIC if BACKGROUND_MUSIC.exists() else None
    mux_audio_and_burn_subtitles(
        concatenated_path, NARRATION_AUDIO, NARRATION_SUBTITLES, music_path, FINAL_OUTPUT
    )

    print(f"✅ الفيديو النهائي جاهز: {FINAL_OUTPUT}")
    print(f"   المدة الإجمالية: ~{narration_duration:.1f} ثانية (الحد الأقصى {MAX_DURATION_SECONDS})")


if __name__ == "__main__":
    main()
