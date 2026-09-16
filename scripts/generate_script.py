"""
generate_script.py
يستدعي Groq API (مجاني) عشان يولّد سيناريو القصة + كلمات البحث البصرية.
بديل مباشر لـ node "AI Agent" اللي كان بيستخدم OpenAI في n8n.

يحتاج: متغير بيئة GROQ_API_KEY (مجاني من https://console.groq.com)

تعديلات على النسخة دي:
- إعادة محاولة تلقائية (retry) لو الرد رجع مقطوع بسبب حد max_tokens، أو مش
  JSON صالح، أو ناقص حقول، أو الـ narration نفسها شكلها متقطوع. ده بيعالج
  مباشرة مشكلة "الفيديو بيخلص فجأة" لو كانت سببها إن نص القصة نفسه جاي
  مقطوع من الموديل من الأساس (قبل ما يوصل حتى لـ generate_voice.py).
- max_tokens اتزود عشان يديك مساحة كافية لقصة كاملة (بداية - تصاعد - خاتمة)
  من غير ما تتقطع، خصوصًا إن generate_voice.py دلوقتي بيقدر يقسم القصة
  الطويلة على ريلزين لو احتاج الأمر.
- ملحوظة مهمة: جودة/واقعية القصة نفسها (تبقى "حقيقية" مش "هبل") بتتحدد
  أساسًا من محتوى prompts/horror_system_prompt.md، وده ملف مش متاح عندي.
  لو عايزني أساعدك تظبطه، ابعتهولي.
"""
import os
import json
import sys
from pathlib import Path
from groq import Groq  # pip install groq

SCRIPT_DIR = Path(__file__).parent
PROMPT_PATH = SCRIPT_DIR.parent / "prompts" / "horror_system_prompt.md"
OUTPUT_PATH = SCRIPT_DIR.parent / "state" / "current_episode.json"

MODEL = "llama-3.3-70b-versatile"  # موديل مجاني قوي على Groq

# لو حسّيت إن القصص لسه بتيجي "هبل"/غير متماسكة، جرّب تقلل الرقم ده شوية
# (مثلاً 0.75)؛ درجة حرارة أعلى = تنويع أكتر بس مخاطرة أعلى في التماسك.
TEMPERATURE = 0.85

# اتزودت من 3000 لمساحة أكبر عشان القصة تخلص كاملة من غير ما تتقطع.
# لو لسه بييجي finish_reason == "length" باستمرار، زوّد الرقم أكتر من هنا
# (تأكد الأول من أقصى حد مسموح للموديل ده على https://console.groq.com).
MAX_TOKENS = 4096

MAX_ATTEMPTS = 3  # عدد محاولات إعادة التوليد لو الرد جه ناقص أو مقطوع


def load_system_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def load_used_history(limit: int = 15) -> list[str]:
    """يجيب آخر N قصص عشان الموديل يتجنب التكرار."""
    history_path = SCRIPT_DIR.parent / "state" / "used_clips.json"
    if not history_path.exists():
        return []
    data = json.loads(history_path.read_text(encoding="utf-8"))
    return [h.get("title", "") for h in data.get("history", [])][-limit:]


def looks_truncated(narration: str) -> bool:
    """فحص بسيط: هل نص القصة شكله متقطوع في نص الكلام؟"""
    stripped = narration.strip()
    if not stripped:
        return True
    return not stripped.endswith((".", "!", "؟", "…", '"', "”"))


def generate_episode() -> dict:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        sys.exit("خطأ: لازم تضيف GROQ_API_KEY في GitHub Secrets")

    client = Groq(api_key=api_key)
    system_prompt = load_system_prompt()
    recent_titles = load_used_history()

    user_message = (
        "اكتب حلقة جديدة تمامًا. لازم القصة تكون مكتملة تمامًا: بداية واضحة، "
        "تصاعد حقيقي في الأحداث، وخاتمة فعلية تقفل القصة — من غير ما تتقطع "
        "في نص الكلام أو تسيب حاجة معلقة من غير قصد."
    )
    if recent_titles:
        user_message += (
            "\n\nالعناوين اللي اتستخدمت قبل كده (تجنب أي تشابه معاها):\n- "
            + "\n- ".join(recent_titles)
        )

    required_keys = {"narration", "visual_keywords", "title", "caption"}
    last_error = "لا يوجد"

    for attempt in range(1, MAX_ATTEMPTS + 1):
        completion = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
            response_format={"type": "json_object"},
        )

        choice = completion.choices[0]

        if choice.finish_reason == "length":
            last_error = "الرد اتقطع بسبب حد max_tokens (finish_reason=length)"
            print(f"⚠️ المحاولة {attempt}/{MAX_ATTEMPTS}: {last_error} — هنعيد المحاولة...")
            continue

        raw = choice.message.content
        try:
            episode = json.loads(raw)
        except json.JSONDecodeError as exc:
            last_error = f"رد غير صالح JSON ({exc})"
            print(f"⚠️ المحاولة {attempt}/{MAX_ATTEMPTS}: {last_error} — هنعيد المحاولة...")
            continue

        if not required_keys.issubset(episode.keys()):
            last_error = f"الرد ناقص حقول مطلوبة: {sorted(episode.keys())}"
            print(f"⚠️ المحاولة {attempt}/{MAX_ATTEMPTS}: {last_error} — هنعيد المحاولة...")
            continue

        narration = str(episode.get("narration", "")).strip()
        if looks_truncated(narration):
            last_error = "نص narration شكله متقطوع (مش منتهي بعلامة ترقيم واضحة)"
            print(f"⚠️ المحاولة {attempt}/{MAX_ATTEMPTS}: {last_error} — هنعيد المحاولة...")
            continue

        if not episode.get("visual_keywords"):
            last_error = "حقل visual_keywords فاضي"
            print(f"⚠️ المحاولة {attempt}/{MAX_ATTEMPTS}: {last_error} — هنعيد المحاولة...")
            continue

        return episode

    sys.exit(f"❌ فشل توليد حلقة سليمة بعد {MAX_ATTEMPTS} محاولات. آخر خطأ: {last_error}")


if __name__ == "__main__":
    episode = generate_episode()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(episode, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"✅ اتكتبت الحلقة: {episode['title']}")
    print(f"   كلمات البحث: {episode['visual_keywords']}")
