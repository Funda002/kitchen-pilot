import os
import requests
from dotenv import load_dotenv

# Load variables from .env
load_dotenv()

# Get your Rime API key
api_key = os.getenv("rime_test_api")

if not api_key:
    raise ValueError("rime_test_api was not found in .env")

# Rime API endpoint
url = "https://users.rime.ai/v1/rime-tts"

headers = {
    "Accept": "audio/wav",
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json"
}

data = {
    "text": "Hello! This is my DataForge 2026 voice agent.",
    "modelId": "mistv3",
    "speaker": "cove",
    "lang": "en",
    "samplingRate": 24000
}

response = requests.post(
    url,
    headers=headers,
    json=data
)

print("Status code:", response.status_code)

if response.ok:
    with open("rime_test.wav", "wb") as audio_file:
        audio_file.write(response.content)

    print("✅ Rime API is working!")
    print("🎵 Audio saved as rime_test.wav")

else:
    print("❌ Rime API request failed")
    print(response.text)