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

The application owns recipe state and deterministic tool results. Never invent, infer, or modify recipe progress yourself.

VOICE RULES:
- Normally reply with one conversational sentence of 15 words or fewer.
- Safety-critical situations may exceed 15 words.
- Trivia/general knowledge: answer briefly and directly. Do not redirect the user back to cooking just because the question is unrelated.
- If a factual question is understandable, answer it; do not claim that trivia is unavailable.
- Never invent a cooking step.
- Never claim a step changed unless the application tool changed it.

RECIPE RULES:
- Recipe selection, servings, ingredients, checklist confirmation, navigation, and completion are controlled by application tools.
- Speak deterministic recipe-tool results as returned; do not rewrite them into different cooking instructions.
- During cooking, "next", "continue", "proceed", or "done" means advance exactly one step.
- "back", "previous", or "repeat the prior step" means use the corresponding navigation behavior.
- Questions about the current step must not advance the recipe.
- A user describing work they already did does not automatically advance the recipe unless they explicitly ask to continue/next.
- Trivia does not change recipe state.
- Never expose internal tool names, lifecycle fields, or implementation details.

INGREDIENT RULES:
- Use deterministic ingredient results for ingredient quantities.
- Servings can only change before cooking starts.
- Use the substitution tool for missing ingredients; never invent an unverified substitution.

TIMER RULES:
- Use timer tools for timer operations.
- Never estimate remaining or elapsed time.
- Timer alerts are application events and do not change recipe state.
- If multiple timers exist, ask which one when the user does not identify it.

Speak naturally, plainly, and without Markdown, bullets, emojis, or filler."""



class KitchenPilotAgent(Agent):
    """
    Kitchen Pilot agent.

    Important design rule:
    Deterministic recipe commands are executed by Python directly.
    The LLM is NOT allowed to repeatedly call recipe-navigation tools.
    For ordinary conversation/trivia, the LLM receives NO tools.
    """

    _NUMBER_WORDS = {
        "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
        "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
        "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
        "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
        "eighteen": 18, "nineteen": 19, "twenty": 20,
        "thirty": 30, "forty": 40, "fifty": 50,
    }

    def __init__(
        self,
        *,
        recipe_state: RecipeStateManager,
        kitchen_tools: KitchenTools | None = None,
        **kwargs: object,
    ) -> None:
        self._recipe_state = recipe_state
        self._kitchen_tools = kitchen_tools
        self._was_interrupted = False
        super().__init__(**kwargs)

    def note_interruption(self) -> None:
        self._was_interrupted = True

    @classmethod
    def _parse_number(cls, value: str) -> int | None:
        value = value.strip().lower()

        if value.isdigit():
            return int(value)

        if value in cls._NUMBER_WORDS:
            return cls._NUMBER_WORDS[value]

        parts = value.split()
        if len(parts) == 2:
            tens = cls._NUMBER_WORDS.get(parts[0])
            ones = cls._NUMBER_WORDS.get(parts[1])
            if tens in (20, 30, 40, 50) and ones is not None:
                return tens + ones

        return None

    @staticmethod
    def _clean_recipe_result(result: str) -> str:
        """
        Remove application-only markers before speaking.
        Recipe facts themselves are left untouched.
        """
        result = result.strip()

        if result.startswith("RECIPE_SELECTED_CHECKLIST:"):
            result = result[len("RECIPE_SELECTED_CHECKLIST:"):].strip()

        if result.startswith("ENDING_SIGNAL:"):
            result = result[len("ENDING_SIGNAL:"):].strip()
            return result or "The recipe is complete."

        return result

    @staticmethod
    def _latest_user_text(chat_ctx: llm.ChatContext) -> str:
        for item in reversed(chat_ctx.items):
            if getattr(item, "role", None) == "user":
                try:
                    text = item.text_content
                except Exception:
                    text = ""
                if text:
                    return text.strip()
        return ""

    def _serving_count(self, text: str) -> int | None:
        # Examples:
        # "cooking for three people"
        # "for 3 people"
        # "serves four"
        pattern = re.compile(
            r"\b(?:cooking\s+for|cook(?:ing)?\s+for|for|serves?|serving|servings)"
            r"\s+(\d+|[a-z]+(?:\s+(?:twenty|thirty|forty|fifty|one|two|three|four|five|"
            r"six|seven|eight|nine))?)"
            r"\s*(?:people|persons?|servings?)?\b",
            re.IGNORECASE,
        )
        match = pattern.search(text)
        if not match:
            return None
        return self._parse_number(match.group(1))

    def _recipe_name_from_request(self, text: str) -> str | None:
        patterns = [
            r"\bi\s+want\s+to\s+(?:make|cook|prepare)\s+(.+)$",
            r"\bi(?:\'d| would)\s+like\s+to\s+(?:make|cook|prepare)\s+(.+)$",
            r"\blet(?:\'s| us)\s+(?:make|cook|prepare)\s+(.+)$",
            r"\b(?:make|cook|prepare)\s+me\s+(.+)$",
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if not match:
                continue

            name = match.group(1).strip(" .?!")
            if not name:
                return None

            # Natural voice filler: "make some Indian chai".
            name = re.sub(r"^(?:some|a|an)\s+", "", name, flags=re.IGNORECASE).strip()

            # Deterministic aliases for common ways users refer to chai.
            aliases = {
                "indian chai": "Indian Masala Chai",
                "masala chai": "Indian Masala Chai",
                "chai": "Indian Masala Chai",
            }
            return aliases.get(name.casefold(), name)

        return None


    def _is_ingredients_request(self, text: str) -> bool:
        t = text.lower()
        return (
            "what ingredients" in t
            or "which ingredients" in t
            or "ingredients do i need" in t
            or "what do i need" in t
            or t.strip() in {"ingredients", "ingredient", "what are the ingredients"}
        )

    def _normalise_user_text(self, text: str) -> str:
        t = re.sub(r"[^\w\s]", " ", text.lower())
        return re.sub(r"\s+", " ", t).strip()

    def _is_ready_confirmation(self, text: str) -> bool:
        """Recognise natural confirmations while the recipe is in checklist."""
        t = self._normalise_user_text(text)

        phrases = (
            "i have everything",
            "ive got everything",
            "i got everything",
            "i have all the ingredients",
            "ive got all the ingredients",
            "i got all the ingredients",
            "all the ingredients are ready",
            "everything is ready",
            "everything ready",
            "i m ready",
            "im ready",
            "ready to cook",
            "ready to start",
            "lets start",
            "let us start",
            "begin",
            "start",
            "go ahead",
            "yes",
            "yep",
            "yeah",
            "yes please",
        )
        if any(t == phrase or t.startswith(phrase + " ") for phrase in phrases):
            return True

        # Voice transcripts often contain filler or combine acknowledgement + start:
        # "Hi, everything. Let's start." / "Okay, I've got everything, begin."
        has_everything = re.search(r"\b(everything|all the ingredients)\b", t)
        has_start_intent = re.search(r"\b(start|begin|ready|got|have)\b", t)
        return bool(has_everything and has_start_intent)

    def _is_checklist_start_request(self, text: str) -> bool:
        """Treat explicit start/next requests as checklist confirmation, not cooking navigation."""
        t = self._normalise_user_text(text)
        return t in {
            "next", "next step", "continue", "continue cooking",
            "proceed", "go on", "go ahead", "begin", "start",
            "lets start", "let us start", "start cooking",
        }

    def _is_next(self, text: str) -> bool:
        """
        Detect explicit advancement requests even when the user combines
        acknowledgement + navigation, e.g.:
        "Yeah, I did it. Continue."
        "Okay, continue."
        "I did that, next."
        """
        t = re.sub(r"[^\w\s]", " ", text.lower())
        t = re.sub(r"\s+", " ", t).strip()

        exact = {
            "next", "next step", "continue", "continue cooking",
            "proceed", "go on", "go ahead", "done", "i did it",
            "i am finished", "im finished", "i have finished",
            "ive finished", "we are finished", "were finished",
            "we are done", "were done", "move on",
            "whats next", "what is next", "what next",
        }
        if t in exact:
            return True

        # Explicit navigation words anywhere in the same user turn.
        # This intentionally requires a navigation verb; merely saying
        # "I did it" does not advance the recipe.
        navigation = re.search(
            r"\b(next|continue|proceed|go\s+on|go\s+ahead|move\s+on)\b",
            t,
        )
        if navigation:
            return True

        # Natural completion + navigation: "I've finished that, let's continue."
        if re.search(r"\b(finished|done|completed)\b", t) and re.search(
            r"\b(continue|proceed|next|move\s+on)\b", t
        ):
            return True
        return False

    def _is_previous(self, text: str) -> bool:
        t = re.sub(r"[^\w\s]", " ", text.lower()).strip()
        return t in {
            "back", "go back", "previous", "previous step",
            "last step", "repeat the last step", "repeat that",
            "repeat the previous step",
        }

    def _is_current_step_question(self, text: str) -> bool:
        t = text.lower()
        return (
            "what step am i on" in t
            or "which step am i on" in t
            or "what are we doing" in t
            or "where are we" in t
            or "current step" in t
        )

    def _timer_duration(self, text: str) -> int | None:
        """Parse simple voice timer requests such as 'two minutes' or '30 seconds'."""
        t = self._normalise_user_text(text)
        match = re.search(
            r"\b(?:set|start|put|give)\s+(?:a\s+)?timer\s+(?:for\s+)?"
            r"(\d+|[a-z]+(?:\s+[a-z]+)?)\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?)\b",
            t,
        )
        if not match:
            # Also accept natural phrasing: "timer for two minutes".
            match = re.search(
                r"\btimer\s+(?:for\s+)?"
                r"(\d+|[a-z]+(?:\s+[a-z]+)?)\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?)\b",
                t,
            )
        if not match:
            return None

        amount = self._parse_number(match.group(1))
        if amount is None or amount <= 0:
            return None

        unit = match.group(2)
        if unit.startswith(("hour", "hr")):
            return amount * 3600
        if unit.startswith(("minute", "min")):
            return amount * 60
        return amount

    def _is_timer_set_request(self, text: str) -> bool:
        t = self._normalise_user_text(text)
        return "timer" in t and bool(re.search(r"\b(set|start|put|give)\b", t) or "timer for" in t)

    async def _route_timer(self, text: str) -> str | None:
        if not self._is_timer_set_request(text):
            return None
        duration = self._timer_duration(text)
        if duration is None:
            return "How long should I set the timer for?"
        if self._kitchen_tools is None:
            return "The timer is not available right now."
        return await self._kitchen_tools.set_timer_direct(duration, "cooking")

    async def _deterministic_recipe_route(self, text: str) -> str | None:
        """
        Returns a final speech string for deterministic recipe commands.
        None means: this is ordinary conversation and should go to the LLM.
        """
        # 1. Timer commands must be checked BEFORE serving-count parsing.
        # Otherwise words such as "two" or "three" can be mistaken for servings.
        timer_result = await self._route_timer(text)
        if timer_result is not None:
            return timer_result

        # 2. Explicit recipe selection.
        recipe_name = self._recipe_name_from_request(text)
        if recipe_name:
            result = self._recipe_state.select_recipe(recipe_name)
            return self._clean_recipe_result(result)

        # 2. Serving count.
        servings = self._serving_count(text)
        if servings is not None:
            return self._clean_recipe_result(
                self._recipe_state.set_servings(servings)
            )

        # 3. Checklist lifecycle has priority over generic navigation.
        # While in checklist, affirmations and start/next requests mean:
        # "confirm my ingredients and begin cooking." This prevents the LLM
        # from asking for the same confirmation repeatedly.
        if self._recipe_state.lifecycle == "checklist":
            if self._is_ready_confirmation(text) or self._is_checklist_start_request(text):
                return self._clean_recipe_result(
                    self._recipe_state.confirm_ingredients_ready()
                )

        # 4. Ingredients.
        if self._is_ingredients_request(text):
            return self._clean_recipe_result(
                self._recipe_state.get_ingredients_list()
            )

        # 5. Cooking navigation.
        if self._recipe_state.lifecycle == "cooking" and self._is_next(text):
            return self._clean_recipe_result(self._recipe_state.next_step())

        if self._is_previous(text):
            return self._clean_recipe_result(self._recipe_state.prev_step())

        # 6. Current-step inspection. This MUST NOT advance state.
        if self._is_current_step_question(text):
            if self._recipe_state.active_recipe is None:
                return "No recipe is selected yet."

            if self._recipe_state.lifecycle == "checklist":
                return "We are still checking the ingredients."

            if self._recipe_state.lifecycle == "completed":
                return "The recipe is complete."

            step_number = self._recipe_state.step_index + 1
            current_step = getattr(self._recipe_state, "current_step", None)

            if callable(current_step):
                current_step = current_step()

            if current_step:
                return f"Step {step_number}: {current_step}"

            return f"You are on step {step_number}."

        # 7. Explicit summary.
        t = text.lower()
        if (
            "summarize the recipe" in t
            or "summarise the recipe" in t
            or "give me the recipe summary" in t
            or t.strip() in {"summary", "summarize", "summarise"}
        ):
            return self._clean_recipe_result(self._recipe_state.summarize_recipe())

        # No deterministic recipe intent -> LLM handles ordinary conversation.
        return None

    async def llm_node(
        self,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool],
        model_settings: ModelSettings,
    ):
        user_text = self._latest_user_text(chat_ctx)

        # IMPORTANT:
        # Do not give the LLM recipe tools for deterministic commands.
        # Execute those operations exactly once in Python and return the result.
        deterministic_result = await self._deterministic_recipe_route(user_text)

        if deterministic_result is not None:
            logger.info(
                "deterministic route: %r -> %r",
                user_text,
                deterministic_result,
            )
            return deterministic_result

        contextual_chat = chat_ctx.copy()
        contextual_chat.add_message(
            role="system",
            content=self._recipe_state.context_for_llm(),
        )

        if self._was_interrupted:
            contextual_chat.add_message(
                role="system",
                content=(
                    "The previous assistant speech was interrupted. "
                    "Treat the newest user message as a new request. "
                    "Do not change recipe state unless the user explicitly requests "
                    "a supported recipe action."
                ),
            )
            self._was_interrupted = False

        # Ordinary conversation/trivia receives NO tools.
        # This prevents trivia, explanations, or ambiguous speech from
        # accidentally calling next_step/check_ingredients/etc.
        safe_settings = model_settings
        try:
            safe_settings = model_settings.model_copy(deep=True)
        except AttributeError:
            pass

        try:
            safe_settings.tool_choice = "none"
        except Exception:
            pass

        return Agent.default.llm_node(
            self,
            contextual_chat,
            [],
            safe_settings,
        )

    @function_tool(
        description="Select a recipe when the user explicitly names a recipe or dish."
    )
    async def select_recipe(self, recipe_name: str) -> str:
        return self._recipe_state.select_recipe(recipe_name)

    @function_tool(
        description="Set serving count when the user explicitly gives the number of people."
    )
    async def set_servings(self, num_people: int) -> str:
        return self._recipe_state.set_servings(num_people)

    @function_tool(
        description="Confirm ingredients are ready and transition from checklist to cooking step one."
    )
    async def confirm_ingredients_ready(self) -> str:
        return self._recipe_state.confirm_ingredients_ready()

    @function_tool(
        description="Return the deterministic scaled ingredient list for the selected recipe."
    )
    async def check_ingredients(self) -> str:
        return self._recipe_state.get_ingredients_list()

    @function_tool(
        description="Return the deterministic overview of the selected recipe."
    )
    async def summarize_recipe(self) -> str:
        return self._recipe_state.summarize_recipe()

    @function_tool(
        description="Jump to an explicitly requested 1-based recipe step."
    )
    async def jump_to_step(self, step_number: int) -> str:
        return self._recipe_state.jump_to_step(step_number)

    @function_tool(
        description="Advance exactly one recipe step when explicitly requested."
    )
    async def next_step(self) -> str:
        return self._recipe_state.next_step()

    @function_tool(
        description="Move back exactly one recipe step when explicitly requested."
    )
    async def prev_step(self) -> str:
        return self._recipe_state.prev_step()

    async def tts_node(
        self,
        text: AsyncIterable[str],
        model_settings: ModelSettings,
    ) -> AsyncGenerator[rtc.AudioFrame, None]:
        async def normalized_text() -> AsyncGenerator[str, None]:
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

        async for frame in Agent.default.tts_node(
            self,
            normalized_text(),
            model_settings,
        ):
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
    logger.info("Connecting to room: %s", ctx.room.name)
    await ctx.connect()

    stt_engine = deepgram.STT(api_key=Config.DEEPGRAM_API_KEY)
    llm_engine = openai.LLM(
        api_key=Config.OPENAI_API_KEY,
        model="gpt-4o-mini",
    )
    tts_engine = RimeTTS(
        api_key=Config.RIME_API_KEY,
        speaker=Config.RIME_SPEAKER,
        base_url=Config.RIME_BASE_URL,
    )

    recipe_state = RecipeStateManager()
    room_name = ctx.room.name
    was_restored = recipe_state.load_state(room_name)

    if was_restored:
        logger.info(
            "Recovered recipe state for room %s: recipe=%s step=%s lifecycle=%s",
            room_name,
            recipe_state.active_recipe,
            recipe_state.step_index,
            recipe_state.lifecycle,
        )

    kitchen_vad = silero.VAD.load(
        activation_threshold=0.82,
        min_speech_duration=0.25,
        min_silence_duration=0.6,
    )

    session = AgentSession(
        stt=stt_engine,
        llm=llm_engine,
        tts=tts_engine,
        vad=kitchen_vad,
        allow_interruptions=True,
        min_interruption_duration=0.1,
        resume_false_interruption=False,
    )

    # Keep interruption handling outside recipe state.
    # A barge-in only stops stale Rime synthesis and marks the next turn as fresh.
    assistant_ref: dict[str, KitchenPilotAgent | None] = {"agent": None}

    def on_user_state_changed(event: object) -> None:
        if event.new_state != "speaking" or event.old_state == "speaking":  # type: ignore[attr-defined]
            return
        if session.agent_state != "speaking":
            return

        assistant = assistant_ref["agent"]
        if assistant is not None:
            assistant.note_interruption()

        asyncio.create_task(
            tts_engine.abort_all(),
            name="abort-rime-on-barge-in",
        )

    session.on("user_state_changed", on_user_state_changed)

    async def announce_timer(message: str) -> None:
        # Timer expiry is independent of recipe progress.
        await session.interrupt(force=True)
        session.say(message, allow_interruptions=True)

    kitchen_tools = KitchenTools(
        ctx.room,
        on_timer_alert=announce_timer,
        recipe_state=recipe_state,
    )
    ctx.add_shutdown_callback(kitchen_tools.aclose)

    assistant = KitchenPilotAgent(
        instructions=SYSTEM_PROMPT,
        recipe_state=recipe_state,
        kitchen_tools=kitchen_tools,
        tools=[
            kitchen_tools.get_substitution,
            kitchen_tools.set_timer,
            kitchen_tools.check_timer_remaining,
            kitchen_tools.check_timer_elapsed,
            kitchen_tools.check_timer_finish_time,
            kitchen_tools.cancel_timer,
        ],
    )
    assistant_ref["agent"] = assistant

    await session.start(
        agent=assistant,
        room=ctx.room,
    )

    logger.info("Kitchen Pilot started successfully with OpenAI + Rime.")

    if was_restored and recipe_state.active_recipe:
        state_description = recipe_state.context_for_llm()
        await session.generate_reply(
            instructions=(
                "Welcome the user back briefly. State below is authoritative. "
                "Do not advance the recipe and do not invent a new step. "
                f"{state_description}"
            )
        )
    else:
        session.say(
            "Hey! What would you like to cook today?",
            allow_interruptions=True,
        )


# --------------------------------------------------
# Worker
# --------------------------------------------------

if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
