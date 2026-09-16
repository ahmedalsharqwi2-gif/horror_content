name: Create and Publish Horror Episode

on:
  schedule:
    - cron: "0 13 * * *"   # الساعة 3 عصرًا بتوقيت القاهرة (UTC+2 ثابت)
    - cron: "0 17 * * *"   # الساعة 7 مساءً بتوقيت القاهرة (UTC+2 ثابت)
  workflow_dispatch: {}     # للتشغيل اليدوي وقت الاختبار

permissions:
  contents: write

jobs:
  build-draft:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout repo
        uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Generate script (Groq)
        env:
          GROQ_API_KEY: ${{ secrets.GROQ_API_KEY }}
        run: python scripts/generate_script.py

      - name: Fetch real stock clips (Pexels)
        env:
          PEXELS_API_KEY: ${{ secrets.PEXELS_API_KEY }}
        run: python scripts/fetch_clips.py

      - name: Generate human-like voice + synced subtitles (edge-tts)
        run: python scripts/generate_voice.py

      - name: Assemble final video with burned captions (ffmpeg)
        run: python scripts/assemble_video.py

      - name: Publish directly via Buffer
        env:
          BUFFER_ACCESS_TOKEN: ${{ secrets.BUFFER_ACCESS_TOKEN }}
          BUFFER_CHANNEL_ID: ${{ secrets.BUFFER_CHANNEL_ID }}
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: python scripts/publish_buffer.py

      - name: Upload video as artifact (for your own records)
        uses: actions/upload-artifact@v4
        with:
          name: published-video
          path: output/final_video.mp4
          retention-days: 14

      - name: Commit updated clip-tracking state
        run: |
          git config user.name "content-bot"
          git config user.email "bot@users.noreply.github.com"
          git add state/used_clips.json
          git commit -m "chore: update used clips tracking [skip ci]" || echo "لا يوجد تغيير"
          git push
