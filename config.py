"""
Configuration file for Horror Content Pipeline.

All API keys, tokens, and settings should be stored here or in environment variables.
Never commit sensitive data to version control.
"""

import os
from pathlib import Path

# =============================================================================
# API KEYS (Use environment variables in production)
# =============================================================================

# AI & TTS
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# Video Sources
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")

# Publishing
BUFFER_ACCESS_TOKEN = os.getenv("BUFFER_ACCESS_TOKEN", "")

# =============================================================================
# FILE PATHS
# =============================================================================

# Base directory
BASE_DIR = Path(__file__).parent.resolve()

# Directories
ASSETS_DIR = BASE_DIR / "assets"
PROMPTS_DIR = BASE_DIR / "prompts"
SCRIPTS_DIR = BASE_DIR / "scripts"
STATE_DIR = BASE_DIR / "state"
OUTPUT_DIR = BASE_DIR / "output"

# Create directories if they don't exist
for directory in [ASSETS_DIR, PROMPTS_DIR, SCRIPTS_DIR, STATE_DIR, OUTPUT_DIR]:
    directory.mkdir(exist_ok=True)

# Files
SYSTEM_PROMPT_FILE = PROMPTS_DIR / "horror_system_prompt.md"
STATE_FILE = STATE_DIR / "pipeline_state.json"

# =============================================================================
# VIDEO SETTINGS
# =============================================================================

# Target video duration (seconds)
VIDEO_DURATION_TARGET = int(os.getenv("VIDEO_DURATION_TARGET", "60"))

# Minimum clip duration (seconds)
MIN_CLIP_DURATION = int(os.getenv("MIN_CLIP_DURATION", "3"))

# Maximum clips to fetch per query
MAX_CLIPS_PER_QUERY = int(os.getenv("MAX_CLIPS_PER_QUERY", "10"))

# =============================================================================
# AUDIO SETTINGS
# =============================================================================

# ElevenLabs voice ID
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb")  # "George"

# Audio output format
AUDIO_FORMAT = os.getenv("AUDIO_FORMAT", "mp3")

# =============================================================================
# PUBLISHING SETTINGS
# =============================================================================

# Buffer profile IDs (leave empty for default)
BUFFER_PROFILES = os.getenv("BUFFER_PROFILES", "").split(",") if os.getenv("BUFFER_PROFILES") else []

# Posting delay (seconds)
POST_DELAY_SECONDS = int(os.getenv("POST_DELAY_SECONDS", "2"))

# =============================================================================
# LOGGING SETTINGS
# =============================================================================

# Log level: DEBUG, INFO, WARNING, ERROR, CRITICAL
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Log file
LOG_FILE = BASE_DIR / "horror_pipeline.log"

# =============================================================================
# VALIDATION
# =============================================================================

def validate_config():
    """Validate that all required configuration is present."""
    errors = []
    
    if not ELEVENLABS_API_KEY:
        errors.append("ELEVENLABS_API_KEY is not set")
    if not PEXELS_API_KEY:
        errors.append("PEXELS_API_KEY is not set")
    if not BUFFER_ACCESS_TOKEN:
        errors.append("BUFFER_ACCESS_TOKEN is not set")
    
    if errors:
        raise ValueError("Configuration errors:\n" + "\n".join(errors))
    
    return True

# Print configuration info on import
if __name__ == "__main__":
    print("Configuration loaded successfully!")
    print(f"ElevenLabs: {'Set' if ELEVENLABS_API_KEY else 'NOT SET'}")
    print(f"OpenAI: {'Set' if OPENAI_API_KEY else 'NOT SET'}")
    print(f"Pexels: {'Set' if PEXELS_API_KEY else 'NOT SET'}")
    print(f"Buffer: {'Set' if BUFFER_ACCESS_TOKEN else 'NOT SET'}")
    print(f"Output Dir: {OUTPUT_DIR}")
