"""Kitchen utility tools for substitutions and proactive timers."""

from __future__ import annotations

import asyncio
import datetime
import inspect
import json
import logging
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from livekit import rtc
from livekit.agents import function_tool


logger = logging.getLogger("kitchen_pilot.tools")

TimerAlert = Callable[[str], Awaitable[None] | None]
TimerExpired = Callable[["TimerRecord"], Awaitable[None] | None]


@dataclass
class TimerRecord:
    """Deterministic timer state owned by one Kitchen Pilot session."""

    timer_id: str
    label: str
    start_timestamp: float
    duration: int
    end_timestamp: float
    status: str = "active"
    task: asyncio.Task[None] | None = None


class TimerManager:
    """Manage session-local timers using the system clock and background tasks."""

    def __init__(self, on_expired: TimerExpired) -> None:
        self._on_expired = on_expired
        self._timers: dict[str, TimerRecord] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    @staticmethod
    def _normalise_label(label: str | None) -> str:
        cleaned = " ".join((label or "").strip().split())[:80]
        lowered = cleaned.casefold()
        if lowered.startswith("the "):
            cleaned = cleaned[4:]
            lowered = cleaned.casefold()
        if lowered.endswith(" timer"):
            cleaned = cleaned[:-6].rstrip()
        return cleaned or "cooking"

    @staticmethod
    def _format_duration(seconds: int) -> str:
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        parts: list[str] = []
        if hours:
            parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
        if minutes:
            parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
        if seconds or not parts:
            parts.append(f"{seconds} second{'s' if seconds != 1 else ''}")
        return " and ".join(parts)

    @staticmethod
    def _format_clock(timestamp: float) -> str:
        return datetime.datetime.fromtimestamp(timestamp).strftime("%I:%M %p").lstrip("0")

    def create(self, duration: int, label: str | None) -> TimerRecord:
        if not 1 <= duration <= 86_400:
            raise ValueError("Set a timer from one second to twenty-four hours.")

        now = time.time()
        record = TimerRecord(
            timer_id=uuid4().hex,
            label=self._normalise_label(label),
            start_timestamp=now,
            duration=duration,
            end_timestamp=now + duration,
        )
        task = asyncio.create_task(self._run(record), name=f"kitchen-timer:{record.timer_id}")
        record.task = task
        self._timers[record.timer_id] = record
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return record

    def _resolve_active(self, label_or_id: str | None) -> tuple[TimerRecord | None, str | None]:
        active = [record for record in self._timers.values() if record.status == "active"]
        requested = (label_or_id or "").strip()
        if requested:
            if record := self._timers.get(requested):
                if record.status == "active":
                    return record, None
                return None, f"The timer for {record.label} is {record.status}."

            normalized = self._normalise_label(requested).casefold()
            matches = [record for record in active if record.label.casefold() == normalized]
            if len(matches) == 1:
                return matches[0], None
            if len(matches) > 1:
                return None, self._ambiguity_message(matches)
            return None, f"No active timer found for {self._normalise_label(requested)}."

        if len(active) == 1:
            return active[0], None
        if not active:
            return None, "There are no active timers."
        return None, self._ambiguity_message(active)

    def _ambiguity_message(self, records: list[TimerRecord]) -> str:
        choices = ", ".join(
            f"{record.label} ({self._format_duration(self.remaining_seconds(record))} remaining)"
            for record in records
        )
        return f"Multiple timers are active: {choices}. Please specify a timer label or ID."

    @staticmethod
    def remaining_seconds(record: TimerRecord) -> int:
        return max(0, math.ceil(record.end_timestamp - time.time()))

    @staticmethod
    def elapsed_seconds(record: TimerRecord) -> int:
        return max(0, math.floor(time.time() - record.start_timestamp))

    def remaining(self, label_or_id: str | None) -> str:
        record, error = self._resolve_active(label_or_id)
        if error:
            return error
        assert record is not None
        return f"{self._format_duration(self.remaining_seconds(record))} remaining on the {record.label} timer."

    def elapsed(self, label_or_id: str | None) -> str:
        record, error = self._resolve_active(label_or_id)
        if error:
            return error
        assert record is not None
        return f"The {record.label} timer has been running for {self._format_duration(self.elapsed_seconds(record))}."

    def finish_time(self, label_or_id: str | None) -> str:
        record, error = self._resolve_active(label_or_id)
        if error:
            return error
        assert record is not None
        return (
            f"The {record.label} timer ends at {self._format_clock(record.end_timestamp)} "
            f"with {self._format_duration(self.remaining_seconds(record))} remaining."
        )

    async def cancel(self, label_or_id: str | None) -> str:
        record, error = self._resolve_active(label_or_id)
        if error:
            return error
        assert record is not None
        record.status = "cancelled"
        if record.task:
            record.task.cancel()
            await asyncio.gather(record.task, return_exceptions=True)
        return f"Cancelled the {record.label} timer."

    async def _run(self, record: TimerRecord) -> None:
        try:
            await asyncio.sleep(max(0.0, record.end_timestamp - time.time()))
            if record.status != "active":
                return
            record.status = "completed"
            result = self._on_expired(record)
            if inspect.isawaitable(result):
                await result
        except asyncio.CancelledError:
            if record.status == "active":
                record.status = "cancelled"
            raise
        except Exception:
            logger.exception("timer expiration callback failed", extra={"timer_id": record.timer_id})

    async def aclose(self) -> None:
        for record in self._timers.values():
            if record.status == "active":
                record.status = "cancelled"
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._timers.clear()


class KitchenTools:
    """Session-scoped LiveKit tools that do not block recipe conversation."""

    # Production-level structured substitution database
    _SUBSTITUTIONS = {
        "butter": {"substitute": "olive oil or margarine", "ratio": "1:1 ratio", "critical": False},
        "egg": {"substitute": "unsweetened applesauce", "ratio": "1/4 cup per egg", "critical": False},
        "eggs": {"substitute": "unsweetened applesauce", "ratio": "1/4 cup per egg", "critical": False},
        "milk": {"substitute": "oat milk or water", "ratio": "1:1 ratio", "critical": False},
        "buttermilk": {"substitute": "milk with lemon juice", "ratio": "1 cup milk with 1 tsp lemon juice", "critical": False},
        "sour cream": {"substitute": "plain Greek yogurt", "ratio": "1:1 ratio", "critical": False},
        "heavy cream": {"substitute": "evaporated milk", "ratio": "1:1 ratio", "critical": False},
        "baking powder": {"substitute": "baking soda and cream of tartar", "ratio": "1/4 tsp baking soda plus 1/2 tsp cream of tartar", "critical": False},
        "baking soda": {"substitute": "baking powder", "ratio": "3 tsp baking powder", "critical": False},
        "lemon juice": {"substitute": "white vinegar", "ratio": "1:1 ratio", "critical": False},
        "soy sauce": {"substitute": "tamari or coconut aminos", "ratio": "1:1 ratio", "critical": False},
        "flour": {"substitute": "all-purpose gluten-free flour", "ratio": "1:1 ratio", "critical": False},
    }

    def __init__(self, room: rtc.Room, on_timer_alert: TimerAlert, recipe_state: Any) -> None:
        self._room = room
        self._on_timer_alert = on_timer_alert
        self._recipe_state = recipe_state
        self._timer_manager = TimerManager(self._notify_timer_expired)

    @function_tool(
        description="Get a practical cooking substitution when the user is missing an ingredient."
    )
    async def get_substitution(self, ingredient: str) -> str:
        """Return structured JSON defining a substitute, ratio, and if it is critical to the dish."""
        cleaned = ingredient.casefold().strip()

        if self._recipe_state.active_recipe is None:
            return json.dumps({
                "substitute": "NOT_FOUND",
                "ratio": "None",
                "critical": False,
                "reason": "Select a recipe before checking substitutions."
            })

        # Check if the requested missing ingredient is vital/core to the current dish
        if self._recipe_state.is_core_ingredient(cleaned):
            return json.dumps({
                "substitute": "None",
                "ratio": "None",
                "critical": True,
                "reason": f"{ingredient} is a core ingredient and cannot be replaced."
            })

        sub = self._SUBSTITUTIONS.get(cleaned)
        if sub is None:
            return json.dumps({
                "substitute": "NOT_FOUND",
                "ratio": "None",
                "critical": False,
                "ingredient": ingredient
            })
            
        return json.dumps({
            "substitute": sub["substitute"],
            "ratio": sub["ratio"],
            "critical": sub["critical"]
        })

    @function_tool(
        description="Set a non-blocking cooking timer. Duration is in whole seconds."
    )
    async def set_timer(self, duration: int, label: str = "cooking") -> str:
        """Start a background timer and return immediately."""
        try:
            timer = self._timer_manager.create(duration, label)
        except ValueError as exc:
            return str(exc)
        return (
            f"Timer set for {timer.label}: "
            f"{self._timer_manager._format_duration(timer.duration)}."
        )

    @function_tool(
        description="Check the remaining time on an active cooking timer. Call this when the user asks how much time is left."
    )
    async def check_timer_remaining(self, label: str | None = None) -> str:
        """Calculate and return the remaining duration on an active timer."""
        return self._timer_manager.remaining(label)

    @function_tool(
        description="Check elapsed time on an active timer using the real clock."
    )
    async def check_timer_elapsed(self, label: str | None = None) -> str:
        """Calculate elapsed time; never estimate it with the LLM."""
        return self._timer_manager.elapsed(label)

    @function_tool(
        description="Get the actual clock time when an active timer will finish."
    )
    async def check_timer_finish_time(self, label: str | None = None) -> str:
        """Return the deterministic end time and remaining duration."""
        return self._timer_manager.finish_time(label)

    @function_tool(
        description="Cancel an active timer by label or timer ID. Ask for clarification if several timers are active."
    )
    async def cancel_timer(self, label: str | None = None) -> str:
        """Cancel one deterministically selected active timer."""
        return await self._timer_manager.cancel(label)

    async def set_timer_direct(self, duration: int, label: str = "cooking") -> str:
        try:
            timer = self._timer_manager.create(duration, label)
        except ValueError as exc:
            return str(exc)
        return (
            f"Timer set for {timer.label}: "
            f"{self._timer_manager._format_duration(timer.duration)}."
        )

    async def check_timer_remaining_direct(self, label: str | None = None) -> str:
        return self._timer_manager.remaining(label)

    async def check_timer_elapsed_direct(self, label: str | None = None) -> str:
        return self._timer_manager.elapsed(label)

    async def check_timer_finish_time_direct(self, label: str | None = None) -> str:
        return self._timer_manager.finish_time(label)

    async def cancel_timer_direct(self, label: str | None = None) -> str:
        return await self._timer_manager.cancel(label)

    async def _notify_timer_expired(self, timer: TimerRecord) -> None:
        message = f"Time's up! Take the {timer.label} off the heat immediately."
        try:
            self._room.local_participant.publish_data(message, topic="kitchen.timer")
        except Exception:
            logger.exception("timer data notification could not be delivered", extra={"timer_id": timer.timer_id})

        result = self._on_timer_alert(message)
        if inspect.isawaitable(result):
            await result

    async def aclose(self) -> None:
        """Cancel outstanding timers when this LiveKit job ends."""
        await self._timer_manager.aclose()