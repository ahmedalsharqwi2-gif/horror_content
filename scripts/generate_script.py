"""
generate_script.py
يستدعي Groq API عشان يولّد سيناريو القصة + كلمات البحث البصرية.

=== تعديلات نسخة "توفير التوكن" ===

1) الموديل: llama-3.3-70b-versatile اتشال من الخطة المجانية/Developer
   (بقى Enterprise/Contact Sales) → 404. البديل: openai/gpt-oss-120b.

2) reasoning_effort="low"  ← أهم تغيير في الملف كله.
   موديلات GPT-OSS موديلات تفكير: بتحرق توكنز في التفكير قبل ما تكتب حرف
   من القصة، والافتراضي عند جروك هو "medium". من غير السطر ده، نص
   الميزانية أو أكتر بتتاكل على الفاضي وبعدين الرد يتقطع.

3) Structured Outputs (json_schema + strict=True) بدل json_object.
   الموديل بيتقيّد على مستوى التوكن إنه يطلع JSON مطابق للـ schema.
   ده بيلغي 3 من أسباب إعادة المحاولة القديمة (JSON غير صالح / حقول ناقصة /
   visual_keywords فاضية) — يعني مفيش توليد كامل بيتضيع تاني بسببها.

4) إعادة المحاولة بقت "بتصعّد" الميزانية بدل ما تكرر نفس النداء الفاشل حرفيًا.
   قبل كده: نفس الموديل + نفس الـ prompt + نفس السقف = نفس الاقتطاع بالظبط،
   3 مرات. دلوقتي الاقتطاع بيرفع السقف قبل المحاولة الجاية.

5) طول القصة اتحدد صراحةً في الـ prompt (TARGET_WORDS) — ده اللي بيتحكم في
   الاستهلاك الفعلي، مش السقف.

6) HISTORY_LIMIT اتقلل من 15 لـ 8 (العناوين دي بتتبعت في كل نداء).

7) طباعة استهلاك التوكنز بعد كل نداء عشان تشوف الأرقام الحقيقية.

ملحوظة: MAX_COMPLETION_TOKENS سقف مش استهلاك — بتدفع على اللي اتولّد فعلاً بس.
رفعه مجاني، وبيمنع الاقتطاع اللي بيضيّع نداء كامل.

يحتاج: GROQ_API_KEY في GitHub Secrets.
اختياري: GROQ_MODEL, GROQ_REASONING_EFFORT كمتغيرات بيئة.
"""
import os
import json
import sys
from pathlib import Path

from groq import Groq  # pip install -U groq

SCRIPT_DIR = Path(__file__).parent
PROMPT_PATH = SCRIPT_DIR.parent / "prompts" / "horror_system_prompt.md"
OUTPUT_PATH = SCRIPT_DIR.parent / "state" / "current_episode.json"

# ─────────────────────────── الإعدادات ───────────────────────────

# جرّب "openai/gpt-oss-20b" لو عايز نص التكلفة وضعف السرعة؛ الجودة أقل شوية
# في السرد العربي الطويل. التغيير من GitHub Secrets من غير ما تلمس الكود.
MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

# "low" | "medium" | "high" — سيبها low. كتابة قصة رعب مش محتاجة تفكير عميق،
# ورفعها لـ medium ممكن يضاعف استهلاك المخرجات من غير فايدة تُذكر.
REASONING_EFFORT = os.getenv("GROQ_REASONING_EFFORT", "low")

TEMPERATURE = 0.85

# سقف مش استهلاك. واسع عن قصد عشان الاقتطاع ميضيعش نداء كامل.
MAX_COMPLETION_TOKENS = 6000

# الطول المستهدف للقصة — ده المتحكم الحقيقي في الاستهلاك.
# قلّله لو عايز ريل واحد قصير، زوّده لو عايز تقسيم على ريلزين.
TARGET_WORDS = int(os.getenv("TARGET_WORDS", "320"))

# محاولتين كفاية دلوقتي: الـ schema الصارم شال معظم أسباب الفشل.
MAX_ATTEMPTS = 2

# كل عنوان هنا بيتبعت في كل نداء — 8 كفاية لتجنب التكرار.
HISTORY_LIMIT = 8

# لو الرد اتقطع، اضرب السقف في الرقم ده قبل المحاولة الجاية.
LENGTH_ESCALATION = 1.5

# ⚠️ لو السكريبت اللي بيجيب الفيديوهات من Pexels بيتوقع visual_keywords
# كـ string مفصول بفواصل بدل list، غيّر "type": "array" لـ "type": "string"
# في الـ schema تحت.
EPISODE_SCHEMA = {
    "name": "horror_episode",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "narration": {"type": "string"},
            "visual_keywords": {"type": "array", "items": {"type": "string"}},
            "caption": {"type": "string"},
        },
        # strict mode بيطلب إن كل الحقول تكون في required و additionalProperties=false
        "required": ["title", "narration", "visual_keywords", "caption"],
        "additionalProperties": False,
    },
}

REQUIRED_KEYS = set(EPISODE_SCHEMA["schema"]["required"])


# ─────────────────────────── مساعدات ───────────────────────────

def load_system_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def load_used_history(limit: int = HISTORY_LIMIT) -> list[str]:
    """يجيب آخر N عناوين عشان الموديل يتجنب التكرار."""
    history_path = SCRIPT_DIR.parent / "state" / "used_clips.json"
    if not history_path.exists():
        return []
    try:
        data = json.loads(history_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return [h.get("title", "") for h in data.get("history", [])][-limit:]


def looks_truncated(narration: str) -> bool:
    """فحص بسيط: هل نص القصة شكله متقطوع في نص الكلام؟"""
    stripped = narration.strip()
    if not stripped:
        return True
    return not stripped.endswith((".", "!", "؟", "?", "…", '"', "”", "»"))


def log_usage(completion, attempt: int) -> None:
    """يطبع الاستهلاك الفعلي عشان تعرف إنت بتدفع على إيه."""
    usage = getattr(completion, "usage", None)
    if not usage:
        return
    print(
        f"   🔢 محاولة {attempt} | مدخل: {usage.prompt_tokens} "
        f"| مخرج: {usage.completion_tokens} "
        f"| إجمالي: {usage.total_tokens}"
    )


def create_completion(client: Groq, **kwargs):
    """
    نداء الموديل مع fallback لو نسخة مكتبة groq المثبتة قديمة ومش عارفة
    reasoning_effort كـ parameter مباشر.
    """
    try:
        return client.chat.completions.create(**kwargs)
    except TypeError:
        effort = kwargs.pop("reasoning_effort", None)
        if effort:
            kwargs["extra_body"] = {"reasoning_effort": effort}
        return client.chat.completions.create(**kwargs)


# ─────────────────────────── التوليد ───────────────────────────

def build_user_message(recent_titles: list[str]) -> str:
    message = (
        "اكتب حلقة جديدة تمامًا.\n\n"
        f"الطول المستهدف لحقل narration: حوالي {TARGET_WORDS} كلمة "
        "(±15%) — لا أقصر ولا أطول بشكل ملحوظ.\n"
        "لازم القصة تكون مكتملة تمامًا: بداية واضحة، تصاعد حقيقي في الأحداث، "
        "وخاتمة فعلية تقفل القصة — من غير ما تتقطع في نص الكلام أو تسيب "
        "حاجة معلقة من غير قصد.\n"
        "اكتب النص النهائي مباشرة: من غير أي تمهيد، ولا شرح، ولا تعليق على "
        "القصة قبلها أو بعدها."
    )
    if recent_titles:
        message += (
            "\n\nالعناوين اللي اتستخدمت قبل كده (تجنب أي تشابه معاها):\n- "
            + "\n- ".join(recent_titles)
        )
    return message


def generate_episode() -> dict:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        sys.exit("خطأ: لازم تضيف GROQ_API_KEY في GitHub Secrets")

    client = Groq(api_key=api_key)
    system_prompt = load_system_prompt()
    user_message = build_user_message(load_used_history())

    budget = MAX_COMPLETION_TOKENS
    last_error = "لا يوجد"

    print(f"🎬 الموديل: {MODEL} | reasoning_effort: {REASONING_EFFORT}")

    for attempt in range(1, MAX_ATTEMPTS + 1):
        completion = create_completion(
            client,
            model=MODEL,
            messages=[
                # السيستم prompt أول رسالة وثابت حرفيًا → بيستفيد من
                # prompt caching بتاع جروك في المحاولات اللي بعدها.
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=TEMPERATURE,
            max_completion_tokens=budget,
            reasoning_effort=REASONING_EFFORT,
            response_format={"type": "json_schema", "json_schema": EPISODE_SCHEMA},
        )

        log_usage(completion, attempt)
        choice = completion.choices[0]

        # ── الاقتطاع: صعّد الميزانية، متكررش نفس النداء ──
        if choice.finish_reason == "length":
            budget = int(budget * LENGTH_ESCALATION)
            last_error = "الرد اتقطع بسبب حد التوكنز (finish_reason=length)"
            print(f"⚠️ {last_error} — هرفع السقف لـ {budget} وأعيد المحاولة...")
            continue

        raw = choice.message.content or ""

        # الـ schema الصارم بيضمن ده، بس سايبه كشبكة أمان لو الموديل اتغير.
        try:
            episode = json.loads(raw)
        except json.JSONDecodeError as exc:
            last_error = f"رد غير صالح JSON ({exc})"
            print(f"⚠️ محاولة {attempt}/{MAX_ATTEMPTS}: {last_error} — هعيد المحاولة...")
            continue

        if not REQUIRED_KEYS.issubset(episode.keys()):
            last_error = f"الرد ناقص حقول مطلوبة: {sorted(episode.keys())}"
            print(f"⚠️ محاولة {attempt}/{MAX_ATTEMPTS}: {last_error} — هعيد المحاولة...")
            continue

        # ── ده السبب الوحيد الواقعي لإعادة المحاولة دلوقتي ──
        # الـ schema بيضمن الشكل، مش اكتمال الحبكة.
        narration = str(episode.get("narration", "")).strip()
        if looks_truncated(narration):
            last_error = "نص narration شكله متقطوع (مش منتهي بعلامة ترقيم واضحة)"
            print(f"⚠️ محاولة {attempt}/{MAX_ATTEMPTS}: {last_error} — هعيد المحاولة...")
            continue

        if not episode.get("visual_keywords"):
            last_error = "حقل visual_keywords فاضي"
            print(f"⚠️ محاولة {attempt}/{MAX_ATTEMPTS}: {last_error} — هعيد المحاولة...")
            continue

        return episode

    sys.exit(
        f"❌ فشل توليد حلقة سليمة بعد {MAX_ATTEMPTS} محاولات. آخر خطأ: {last_error}"
    )


if __name__ == "__main__":
    episode = generate_episode()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(episode, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"✅ اتكتبت الحلقة: {episode['title']}")
    print(f"   كلمات البحث: {episode['visual_keywords']}")
