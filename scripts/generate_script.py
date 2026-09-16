"""
generate_script.py
يستدعي Groq API (مجاني) عشان يولّد سيناريو القصة + كلمات البحث البصرية.
بديل مباشر لـ node "AI Agent" اللي كان بيستخدم OpenAI في n8n.

يحتاج: متغير بيئة GROQ_API_KEY (مجاني من https://console.groq.com)
"""
import os
import json
import sys
from pathlib import Path
from groq import Groq  # pip install groq

SCRIPT_DIR = Path(__file__).parent
PROMPT_PATH = SCRIPT_DIR.parent / "prompts" / "horror_system_prompt.md"
OUTPUT_PATH = SCRIPT_DIR.parent / "state" / "current_episode.json"


def load_system_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def load_used_history(limit: int = 15) -> list[str]:
    """يجيب آخر N قصص عشان الموديل يتجنب التكرار."""
    history_path = SCRIPT_DIR.parent / "state" / "used_clips.json"
    if not history_path.exists():
        return []
    data = json.loads(history_path.read_text(encoding="utf-8"))
    return [h.get("title", "") for h in data.get("history", [])][-limit:]


def generate_episode() -> dict:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        sys.exit("خطأ: لازم تضيف GROQ_API_KEY في GitHub Secrets")

    client = Groq(api_key=api_key)
    system_prompt = load_system_prompt()
    recent_titles = load_used_history()

    user_message = "اكتب حلقة جديدة تمامًا."
    if recent_titles:
        user_message += (
            "\n\nالعناوين اللي اتستخدمت قبل كده (تجنب أي تشابه معاها):\n- "
            + "\n- ".join(recent_titles)
        )

    completion = client.chat.completions.create(
        model="llama-3.3-70b-versatile",  # موديل مجاني قوي على Groq
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        temperature=0.9,  # تنويع أعلى بين الحلقات
        max_tokens=3000,  # القصة بقت أطول (~2.5-2.8 دقيقة قراءة) فمحتاجة مساحة أكبر
        response_format={"type": "json_object"},
    )

    raw = completion.choices[0].message.content
    episode = json.loads(raw)

    required_keys = {"narration", "visual_keywords", "title", "caption"}
    if not required_keys.issubset(episode.keys()):
        sys.exit(f"خطأ: الرد من الموديل ناقص حقول مطلوبة: {episode.keys()}")

    return episode


if __name__ == "__main__":
    episode = generate_episode()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(episode, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"✅ اتكتبت الحلقة: {episode['title']}")
    print(f"   كلمات البحث: {episode['visual_keywords']}")
