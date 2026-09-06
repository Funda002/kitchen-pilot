import os
from dotenv import load_dotenv
from google import genai

# Load .env
load_dotenv()

# Check that the key exists
api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    raise ValueError("❌ GEMINI_API_KEY not found in .env")

print("✅ Gemini API key found")
print("🔄 Connecting to Gemini...")

try:
    # Create Gemini client
    client = genai.Client(api_key=api_key)

    # Send test request
    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=(
            "You are a voice assistant. "
            "Reply with exactly: "
            "Gemini API connection successful!"
        ),
    )

    print("\n==============================")
    print("✅ GEMINI API IS WORKING")
    print("==============================")
    print("Response:")
    print(response.text)
    print("==============================")

except Exception as e:
    print("\n❌ GEMINI API TEST FAILED")
    print("Error:", e)