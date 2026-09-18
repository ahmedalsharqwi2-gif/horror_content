"""
assemble_video.py

يجمع المقاطع مع الصوت النهائي الناتج من generate_voice.py، ويحرق
نص العنوان الكبير Bold المتزامن أعلى الشاشة أسفل منطقة الكاميرا الأمامية،
في المنتصف تماماً (مش يمين ولا شمال).

الموسيقى مدمجة مسبقاً داخل ملف narration_with_music*.mp3، لذلك لا نخلطها هنا مرة أخرى.

لو القصة اتقسمت جزئين في generate_voice.py (episode["parts"] فيه عنصرين)،
هنولد ريلزين منفصلين:
- output/final_video_part1.mp4
- output/final_video_part2.mp4

ولو جزء واحد بس (الوضع الافتراضي)، هنولد:
- output/final_video.mp4

=== تعديلات جديدة ===
1) MAX_DURATION_SECONDS نزلت من 600 لـ 90 ثانية (دقيقة ونصف) — ده الحد
   الصلب النهائي لكل جزء عشان يصلح فعليًا لريلز فيسبوك/انستجرام، حتى لو
   generate_voice.py قدّر المدة بشكل مختلف قليلاً.
2) تصحيح خطأ كان موجود في الكود القديم: التعليق كان يقول FontSize=64
   لكن القيمة الفعلية في SUBTITLE_STYLE كانت FontSize=12 (نص صغير جداً
   عمليًا). رجّعتها لـ 64 زي ما كانت الخطة الأصلية.
3) Outline رفعناها من 3 إلى 5px بناءً على طلبك (إطار أسود أوضح حول النص
   الأبيض لزيادة التباين على أي خلفية فيديو).

=== مكان وحجم النص (Alignment/MarginV/FontSize) ===
Alignment=8 يثبّت النص أعلى-منتصف الفريم (مش يمين ولا شمال)، MarginV=260
يبعّده عن حافة الشاشة العليا (تحت منطقة الكاميرا الأمامية)، وWrapStyle=2
بيحدد أقصى سطرين. القيم دي مضبوطة على فريم رأسي 1080×1920.
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
EPISODE_PATH = STATE_DIR / "current_episode.json"

TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920

# الحد الصلب النهائي لمدة أي جزء (بالثانية) — دقيقة ونصف، مناسب لريلز
# فيسبوك/انستجرام. أي جزء أطول من كده هيُقصّ عند هذا الحد.
MAX_DURATION_SECONDS = 90

# نص عنوان كبير وBold، سطران كحد أقصى، أعلى الشاشة أسفل منطقة الكاميرا
# الأمامية، بنفس الإحساس اللي في صورة المرجع (خط أبيض سميك بحدّ أسود واضح).
#   Alignment=8   -> يثبّت الكتلة أعلى-منتصف (7/8/9 = الصف العلوي،
#                    8 = وسط أفقيًا جوه الهوامش)
#   MarginV=260   -> المسافة من أعلى الفريم بالبكسل الحقيقي (لأن
#                    PlayResY == TARGET_HEIGHT)، تحت منطقة الكاميرا الأمامية براحة
#   MarginL/R=60  -> متماثلة عشان Alignment=8 يتمركز على منتصف الفريم
#                    الحقيقي مش بوكس مايل
#   FontSize=64   -> واضح على المقاس ده من غير ما يطغى على الشاشة
#   Outline=5, Shadow=0, BorderStyle=1 -> نص أبيض حاد بحدّ أسود سميك،
#                    من غير ظل منفصل
SUBTITLE_STYLE = (
    "FontName=Arial,FontSize=64,Bold=1,"
    "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
    "BorderStyle=1,Outline=5,Shadow=0,"
    "Alignment=8,MarginL=60,MarginR=60,MarginV=260,"
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
        "-stream_loop", "-1",
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


def concat_clips(paths: list[Path], output_path: Path, tmp_name: str):
    if not paths:
        sys.exit("❌ لا توجد مقاطع لتجميعها.")

    list_file = paths[0].parent / tmp_name
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


def add_audio_and_subtitles(video_path: Path, final_audio: Path, subtitles: Path, output_path: Path):
    subtitle_filter = f"subtitles={subtitles}:force_style='{SUBTITLE_STYLE}'"
    run([
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", str(final_audio),
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
        str(output_path),
    ])


def split_clips_by_weight(clips: list, weights: list[float]) -> list[list]:
    total_weight = sum(weights) or 1.0
    counts = []
    remaining = len(clips)
    for index, weight in enumerate(weights):
        if index == len(weights) - 1:
            count = remaining
        else:
            count = max(1, round(len(clips) * (weight / total_weight)))
            count = min(count, remaining - (len(weights) - index - 1))
        counts.append(count)
        remaining -= count

    result = []
    cursor = 0
    for count in counts:
        chunk = clips[cursor: cursor + count]
        if not chunk:
            chunk = clips
        result.append(chunk)
        cursor += count
    return result


def build_video_part(clips: list, final_audio: Path, subtitles: Path, output_path: Path, tmp_prefix: str) -> float:
    narration_duration = min(get_audio_duration(final_audio), MAX_DURATION_SECONDS)
    duration_per_clip = max(narration_duration / len(clips), 2.0)

    normalized = []
    for index, clip in enumerate(clips):
        norm_path = CLIPS_DIR / f"norm_{tmp_prefix}_{index:02d}.mp4"
        normalize_clip(Path(clip["file"]), norm_path, duration_per_clip)
        normalized.append(norm_path)

    concatenated = CLIPS_DIR / f"concatenated_{tmp_prefix}.mp4"
    concat_clips(normalized, concatenated, tmp_name=f"concat_list_{tmp_prefix}.txt")
    add_audio_and_subtitles(concatenated, final_audio, subtitles, output_path)

    return narration_duration


def main():
    required = [FETCHED_CLIPS_PATH, EPISODE_PATH]
    for path in required:
        if not path.exists():
            sys.exit(f"❌ الملف غير موجود: {path}")

    clips = json.loads(FETCHED_CLIPS_PATH.read_text(encoding="utf-8"))
    if not clips:
        sys.exit("❌ fetched_clips.json فارغ.")

    episode = json.loads(EPISODE_PATH.read_text(encoding="utf-8"))
    parts = episode.get("parts")

    if not parts:
        parts = [{
            "final_audio": str(CLIPS_DIR / "narration_with_music.mp3"),
            "subtitles": str(CLIPS_DIR / "narration.srt"),
        }]

    for part in parts:
        for key in ("final_audio", "subtitles"):
            if not Path(part[key]).exists():
                sys.exit(f"❌ الملف غير موجود: {part[key]}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if len(parts) == 1:
        output_path = OUTPUT_DIR / "final_video.mp4"
        duration = build_video_part(
            clips,
            Path(parts[0]["final_audio"]),
            Path(parts[0]["subtitles"]),
            output_path,
            tmp_prefix="single",
        )
        print(f"✅ الفيديو النهائي: {output_path}")
        print(f"✅ المدة: {duration:.1f} ثانية (حد أقصى {MAX_DURATION_SECONDS}s)")
    else:
        weights = [get_audio_duration(Path(part["final_audio"])) for part in parts]
        clip_groups = split_clips_by_weight(clips, weights)

        for index, (part, part_clips) in enumerate(zip(parts, clip_groups), 1):
            output_path = OUTPUT_DIR / f"final_video_part{index}.mp4"
            duration = build_video_part(
                part_clips,
                Path(part["final_audio"]),
                Path(part["subtitles"]),
                output_path,
                tmp_prefix=f"part{index}",
            )
            print(f"✅ الجزء {index}: {output_path}")
            print(f"✅ مدة الجزء {index}: {duration:.1f} ثانية (حد أقصى {MAX_DURATION_SECONDS}s)")

    print("✅ النص: FontSize=64 Bold، سطران كحد أقصى، أعلى الشاشة (MarginV=260)، في المنتصف، Outline=5")
    print("✅ الموسيقى مدمجة مسبقاً بنسبة 15%")


if __name__ == "__main__":
    main()
