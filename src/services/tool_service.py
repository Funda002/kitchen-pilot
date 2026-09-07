"""Kitchen utility tools for substitutions and proactive timers."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable

from livekit import rtc
from livekit.agents import function_tool


logger = logging.getLogger("kitchen_pilot.tools")

TimerAlert = Callable[[str], Awaitable[None] | None]


class KitchenTools:
    """Session-scoped LiveKit tools that do not block recipe conversation."""

    _SUBSTITUTIONS = {
        "butter": "olive oil or margarine",
        "egg": "a quarter cup of unsweetened applesauce",
        "eggs": "a quarter cup of unsweetened applesauce per egg",
        "milk": "an equal amount of oat milk or water",
        "buttermilk": "milk with a teaspoon of lemon juice",
        "sour cream": "plain Greek yogurt",
        "heavy cream": "evaporated milk",
        "baking powder": "a quarter teaspoon baking soda plus half teaspoon cream of tartar",
        "baking soda": "three teaspoons baking powder",
        "lemon juice": "an equal amount of white vinegar",
        "soy sauce": "tamari or coconut aminos",
        "flour": "an equal amount of all-purpose gluten-free flour",
    }

    def __init__(self, room: rtc.Room, on_timer_alert: TimerAlert) -> None:
        self._room = room
        self._on_timer_alert = on_timer_alert
        self._timer_tasks: set[asyncio.Task[None]] = set()

    @function_tool(
        description="Get a practical cooking substitution when the user is missing an ingredient."
    )
    async def get_substitution(self, ingredient: str) -> str:
        """Return a substitution for one ingredient."""
        key = ingredient.casefold().strip()
        substitute = self._SUBSTITUTIONS.get(key)
        if substitute is None:
            return f"I don't have a reliable substitution for {ingredient}."
        return f"For {ingredient}, use {substitute}."

    @function_tool(
        description="Set a non-blocking cooking timer. Duration is in whole seconds."
    )
    async def set_timer(self, duration: int, label: str) -> str:
        """Start a background timer and return immediately."""
        if not 1 <= duration <= 86_400:
            return "Set a timer from one second to twenty-four hours."

        clean_label = label.strip()[:80] or "cooking"
        task = asyncio.create_task(
            self._run_timer(duration, clean_label),
            name=f"kitchen-timer:{clean_label}",
        )
        self._timer_tasks.add(task)
        task.add_done_callback(self._timer_tasks.discard)
        unit = "second" if duration == 1 else "seconds"
        return f"Timer set for {clean_label}: {duration} {unit}."

    async def _run_timer(self, duration: int, label: str) -> None:
        try:
            await asyncio.sleep(duration)
            message = f"Timer for {label} is up!"

            # Notify connected clients as well as speaking in the voice room.
            self._room.local_participant.publish_data(message, topic="kitchen.timer")

            result = self._on_timer_alert(message)
            if inspect.isawaitable(result):
                await result
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("timer alert could not be delivered", extra={"label": label})

    async def aclose(self) -> None:
        """Cancel outstanding timers when this LiveKit job ends."""
        tasks = list(self._timer_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
