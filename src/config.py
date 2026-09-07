# src/config.py
import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    LIVEKIT_URL = os.getenv("LIVEKIT_URL", "ws://localhost:7880")
    LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
    LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")
    
    # Deepgram STT
    DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
    
    # OpenAI LLM
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    
    # Rime TTS (Mapped to rime_test_api)
    RIME_API_KEY = os.getenv("rime_test_api")
    RIME_SPEAKER = os.getenv("RIME_SPEAKER", "cove")
    RIME_BASE_URL = os.getenv("RIME_BASE_URL", "https://users.rime.ai/v1/rime-tts")

    @classmethod
    def validate(cls):
        required = ["LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "DEEPGRAM_API_KEY", "OPENAI_API_KEY", "RIME_API_KEY"]
        missing = [key for key in required if not getattr(cls, key)]
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")
