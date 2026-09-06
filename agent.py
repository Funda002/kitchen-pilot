# agent.py

import asyncio
import logging
import os

from dotenv import load_dotenv

from livekit.agents import JobContext, WorkerOptions, cli
from livekit.agents.voice import Agent, AgentSession

from livekit.plugins import deepgram, google, silero

from src.config import Config
from src.services.rime_tts import RimeTTS


# --------------------------------------------------
# Logging
# --------------------------------------------------

logging.basicConfig(level=logging.INFO)

logger = logging.getLogger("kitchen_pilot.agent")


# --------------------------------------------------
# Load environment
# --------------------------------------------------

load_dotenv(override=True)


# --------------------------------------------------
# Validate configuration
# --------------------------------------------------

Config.validate()


# --------------------------------------------------
# Agent Entrypoint
# --------------------------------------------------

async def entrypoint(ctx: JobContext):

    logger.info(f"Connecting to room: {ctx.room.name}")

    await ctx.connect()

    # --------------------------------------------------
    # 1. Deepgram STT
    # --------------------------------------------------

    stt_engine = deepgram.STT(
        api_key=Config.DEEPGRAM_API_KEY
    )

    # --------------------------------------------------
    # 2. Gemini LLM
    # --------------------------------------------------

    llm_engine = google.LLM(
        api_key=Config.GEMINI_API_KEY,
        model=Config.GEMINI_MODEL,
    )

    # --------------------------------------------------
    # 3. Rime TTS
    # --------------------------------------------------

    tts_engine = RimeTTS(
        api_key=Config.RIME_API_KEY,
        speaker=Config.RIME_SPEAKER,
        base_url=Config.RIME_BASE_URL,
    )

    # --------------------------------------------------
    # 4. Kitchen Pilot Agent
    # --------------------------------------------------

    assistant = Agent(
        instructions="""
You are Kitchen Pilot, a helpful voice-native AI cooking companion.

For this initial milestone:
- Speak naturally.
- Keep responses concise.
- Help the user with cooking questions.
- Do not overwhelm the user with long explanations.
""",
    )

    # --------------------------------------------------
    # 5. Agent Session
    # --------------------------------------------------

    session = AgentSession(
        stt=stt_engine,
        llm=llm_engine,
        tts=tts_engine,
        vad=silero.VAD.load(),
    )

    # --------------------------------------------------
    # 6. Start session
    # --------------------------------------------------

    await session.start(
        agent=assistant,
        room=ctx.room,
    )

    logger.info(
        "Kitchen Pilot started successfully with Gemini + Rime."
    )

    # --------------------------------------------------
    # 7. Initial greeting
    # --------------------------------------------------

    await session.generate_reply(
        instructions=(
            "Greet the user briefly and ask what they would "
            "like to cook today."
        )
    )


# --------------------------------------------------
# Worker
# --------------------------------------------------

if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint
        )
    )