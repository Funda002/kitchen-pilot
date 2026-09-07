# agent.py

import asyncio
import logging
import os
import re
from collections.abc import AsyncGenerator, AsyncIterable

from dotenv import load_dotenv

from livekit import rtc
from livekit.agents import JobContext, WorkerOptions, cli, function_tool, llm
from livekit.agents.voice import Agent, AgentSession
from livekit.agents.voice.agent import ModelSettings

from livekit.plugins import deepgram, openai, silero

from src.config import Config
from src.services.rime_tts import RimeTTS
from src.services.state_manager import RecipeStateManager
from src.services.tool_service import KitchenTools
from src.utils.text_normalizer import normalize_for_rime


# --------------------------------------------------
# Logging
# --------------------------------------------------

logging.basicConfig(level=logging.INFO)

logger = logging.getLogger("kitchen_pilot.agent")


SYSTEM_PROMPT = """You are Chef Kitchen Pilot, a warm, calm, voice-native cooking coach.

NON-NEGOTIABLE VOICE RULES:
- Reply with one conversational sentence of 15 words or fewer.
- Give exactly one actionable recipe step per reply.
- After a recipe step, stop and wait for the user to say "next", "ready", or ask a question.
- Never volunteer later steps, ingredient lists, or a complete recipe unless the user explicitly asks.
- If the user explicitly asks for multiple steps, give only the next most useful step, then wait.
- Be encouraging, practical, and concise; sound like a helpful chef beside the stove.
- Put safety first: mention heat, sharp tools, raw food hygiene, and allergy risks when relevant.
- If a safety-critical detail is missing, ask one short clarifying question before advising.

RECIPE NAVIGATION RULES:
- The injected RECIPE STATE is the only source of truth for recipe steps.
- When a user says start, make, cook, or chooses a recipe, call select_recipe.
- When a user says next, ready, continue, or asks what is next, call next_step.
- When a user says back, previous, repeat the prior step, or go back, call prev_step.
- Never advance, rewind, select, or invent a recipe step without calling the matching tool.
- After a recipe-navigation tool returns, speak its returned text exactly, with no additions.

UTILITY TOOL RULES:
- Use get_substitution when the user is missing an ingredient or asks for a substitute.
- Use set_timer for any user request to time a cooking activity; convert minutes to seconds.
- Timers run independently. When a timer alert arrives, it must be announced immediately.

INTERRUPTION RULES:
- If the user interrupts a step without a new request, briefly apologize and ask to repeat it.
- If the user interrupts with a new request, answer that request instead.
- An interruption never advances or rewinds the active recipe step.

Use plain spoken cooking language. Do not use Markdown, bullet points, emojis, or filler."""


class KitchenPilotAgent(Agent):
    """Chef persona with a non-blocking text transform before Rime synthesis."""

    def __init__(self, *, recipe_state: RecipeStateManager, **kwargs: object) -> None:
        # This object is intentionally per AgentSession: separate callers must never
        # share a recipe cursor.
        self._recipe_state = recipe_state
        self._was_interrupted = False
        super().__init__(**kwargs)

    def note_interruption(self) -> None:
        """Record a barge-in so the next completed user turn can recover gracefully."""
        self._was_interrupted = True

    def llm_node(
        self,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool],
        model_settings: ModelSettings,
    ):
        """Inject the latest deterministic state on every LLM/tool-response turn."""
        contextual_chat = chat_ctx.copy()
        contextual_chat.add_message(
            role="system",
            content=self._recipe_state.context_for_llm(),
        )
        if self._was_interrupted:
            contextual_chat.add_message(
                role="system",
                content=(
                    "INTERRUPTION CONTEXT: Your prior reply was interrupted. "
                    "If the user made no new request, apologize briefly and offer to repeat the current step."
                ),
            )
            self._was_interrupted = False
        return Agent.default.llm_node(self, contextual_chat, tools, model_settings)

    @function_tool(
        description="Select a recipe and return its exact first step. Use for start or recipe-selection requests."
    )
    async def select_recipe(self, recipe_name: str) -> str:
        """Select a recipe by its spoken name."""
        return self._recipe_state.select_recipe(recipe_name)

    @function_tool(
        description="Advance the active recipe by exactly one step and return that exact step."
    )
    async def next_step(self) -> str:
        """Move to the next recipe step."""
        return self._recipe_state.next_step()

    @function_tool(
        description="Move the active recipe back by exactly one step and return that exact step."
    )
    async def prev_step(self) -> str:
        """Move to the previous recipe step."""
        return self._recipe_state.prev_step()

    async def tts_node(
        self,
        text: AsyncIterable[str],
        model_settings: ModelSettings,
    ) -> AsyncGenerator[rtc.AudioFrame, None]:
        async def normalized_text() -> AsyncGenerator[str, None]:
            # LLM tokens can split a fraction (for example, ``1/`` + ``2``).
            # Buffer through sentence boundaries so normalisation sees complete recipe text.
            pending = ""
            async for chunk in text:
                pending += chunk
                while match := re.search(r"[.!?](?:\s|$)", pending):
                    boundary = match.end()
                    sentence, pending = pending[:boundary], pending[boundary:]
                    if clean := normalize_for_rime(sentence):
                        yield clean

            if clean := normalize_for_rime(pending):
                yield clean

        async for frame in Agent.default.tts_node(self, normalized_text(), model_settings):
            yield frame


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
    # 2. OpenAI LLM
    # --------------------------------------------------

    llm_engine = openai.LLM(
        api_key=Config.OPENAI_API_KEY,
        model="gpt-4o-mini",
    )

    # --------------------------------------------------
    # 3. Rime TTS
    # --------------------------------------------------

    tts_engine = RimeTTS(
        api_key=Config.RIME_API_KEY,
        speaker=Config.RIME_SPEAKER,
        base_url=Config.RIME_BASE_URL,
    )

    recipe_state = RecipeStateManager()
    kitchen_vad = silero.VAD.load(
        activation_threshold=0.65,
        min_speech_duration=0.1,
        min_silence_duration=0.4,
    )

    # --------------------------------------------------
    # 4. Agent session and utility tools
    # --------------------------------------------------

    session = AgentSession(
        stt=stt_engine,
        llm=llm_engine,
        tts=tts_engine,
        vad=kitchen_vad,
        allow_interruptions=True,
        min_interruption_duration=0.1,
        resume_false_interruption=False,
    )

    def on_speech_created(event: object) -> None:
        """Commit a recipe move only when its associated speech fully plays."""
        speech_handle = event.speech_handle  # type: ignore[attr-defined]

        def finalize_recipe_move(handle: object) -> None:
            if handle.interrupted:  # type: ignore[attr-defined]
                recipe_state.discard_pending_step()
            else:
                recipe_state.commit_pending_step()

        speech_handle.add_done_callback(finalize_recipe_move)

    def on_user_state_changed(event: object) -> None:
        """Barge-in fence: cancel custom Rime work before stale audio can play."""
        if event.new_state != "speaking" or event.old_state == "speaking":  # type: ignore[attr-defined]
            return
        if session.agent_state != "speaking":
            return

        recipe_state.discard_pending_step()
        assistant.note_interruption()
        asyncio.create_task(tts_engine.abort_all(), name="abort-rime-on-barge-in")

    session.on("speech_created", on_speech_created)
    session.on("user_state_changed", on_user_state_changed)

    async def announce_timer(message: str) -> None:
        # session.say schedules speech without blocking the timer or WebRTC loop.
        session.say(message, allow_interruptions=False)

    kitchen_tools = KitchenTools(ctx.room, on_timer_alert=announce_timer)
    ctx.add_shutdown_callback(kitchen_tools.aclose)

    # --------------------------------------------------
    # 5. Kitchen Pilot Agent
    # --------------------------------------------------

    assistant = KitchenPilotAgent(
        instructions=SYSTEM_PROMPT,
        recipe_state=recipe_state,
        tools=[
            kitchen_tools.get_substitution,
            kitchen_tools.set_timer,
        ],
    )

    # --------------------------------------------------
    # 6. Start session
    # --------------------------------------------------

    await session.start(
        agent=assistant,
        room=ctx.room,
    )

    logger.info(
        "Kitchen Pilot started successfully with OpenAI + Rime."
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
