# Horror Content Pipeline

أتمتة كاملة لإنشاء محتوى رعب لليوتيوب باستخدام الذكاء الاصطناعي.

## 🌟 المميزات

- ✅ توليد سيناريو رعب تلقائي باستخدام AI
- ✅ توليد صوتي احترافي (TTS)
- ✅ تجميع فيديو تلقائي (لقات + صوت + نصوص)
- ✅ نشر تلقائي عبر Buffer
- ✅ نظام prompts متقدم لتوليد محتوى عالي الجودة
- ✅ هيكل مشروع احترافي ومنظم

## 📁 هيكل المشروع

```
horror_content/
├── .github/
│   └── workflows/           # GitHub Actions
├── assets/                  # الصور والفيديوهات الجاهزة
├── prompts/
│   └── horror_system_prompt.md  # System prompt للـ AI
├── scripts/
│   ├── assemble_video.py    # تجميع الفيديو
│   ├── fetch_clips.py       # جلب لقطات من Pexels
│   ├── generate_script.py   # توليد السيناريو
│   ├── generate_voice.py    # توليد الصوت
│   └── publish_buffer.py    # النشر على Buffer
├── state/                   # حالة pipeline
├── config.py                # الإعدادات (يجب إنشاؤه)
├── requirements.txt         # المكتبات المطلوبة
└── README.md               # هذا الملف
```

## 🚀 التثبيت

### 1. استنساخ المشروع

```bash
git clone https://github.com/ahmedalsharqwi2-gif/horror_content.git
cd horror_content
```

### 2. تثبيت المكتبات

```bash
pip install -r requirements.txt
```

### 3. إعداد المتغيرات البيئية

أنشئ ملف `.env` في الجذر:

```bash
# AI & TTS
ELEVENLABS_API_KEY=your_elevenlabs_key
OPENAI_API_KEY=your_openai_key

# Video Sources
PEXELS_API_KEY=your_pexels_key

# Publishing
BUFFER_ACCESS_TOKEN=your_buffer_token
```

### 4. إنشاء config.py

انسخ `config.example.py` إلى `config.py` وعدّل القيم:

```python
# مثال سريع
ELEVENLABS_API_KEY = "your_key_here"
PEXELS_API_KEY = "your_key_here"
BUFFER_ACCESS_TOKEN = "your_token_here"
```

## 📖 الاستخدام

### تشغيل كامل (Pipeline)

```bash
# 1. توليد السيناريو
python scripts/generate_script.py

# 2. جلب اللقطات
python scripts/fetch_clips.py

# 3. توليد الصوت
python scripts/generate_voice.py

# 4. تجميع الفيديو
python scripts/assemble_video.py

# 5. النشر
python scripts/publish_buffer.py
```

### تشغيل سريع (كل الخطوات)

```bash
python -m scripts.run_full_pipeline
```

## 🔧 Troubleshooting

### خطأ: `ModuleNotFoundError`

```bash
pip install -r requirements.txt --upgrade
```

### خطأ: `API Key not set`

- تأكد من ملف `.env` أو `config.py`
- تحقق من أن المفاتيح صحيحة

### خطأ: `No clips found`

- جرب كلمات مفتاحية مختلفة في `fetch_clips.py`
- تحقق من Pexels API quota

## 🔐 الأمان

- ⚠️ **لا تشارك** API Keys أبداً
- ✅ استخدم `.env` وأضفه لـ `.gitignore`
- ✅ استخدم GitHub Secrets للـ CI/CD

## 📝 الترخيص

MIT License

## 🤝 المساهمة

1. Fork المشروع
2. أنشئ فرع جديد
3. Commit التغييرات
4. Push
5. Pull Request

## 📧 للتواصل

- GitHub: [@ahmedalsharqwi2-gif](https://github.com/ahmedalsharqwi2-gif)

---

**مبني بحب ❤️ لمحبّي الرعب**
