"""Deterministic, per-call recipe navigation state."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


DEFAULT_RECIPE_LIBRARY = Path(__file__).parent.parent / "data" / "recipes.json"


class RecipeStateManager:
    """Own the active recipe and current step for one Kitchen Pilot session."""

    def __init__(self, recipe_library: Path | str = DEFAULT_RECIPE_LIBRARY) -> None:
        with Path(recipe_library).open(encoding="utf-8") as recipe_file:
            recipes: dict[str, dict[str, Any]] = json.load(recipe_file)

        if not recipes:
            raise ValueError("The recipe library cannot be empty")

        self._recipes = recipes
        self.active_recipe: str | None = None
        self.step_index = 0
        self._pending_step_index: int | None = None

    @staticmethod
    def _canonical_name(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()

    def _find_recipe_key(self, recipe_name: str) -> str | None:
        wanted = self._canonical_name(recipe_name)
        if not wanted:
            return None

        for key, recipe in self._recipes.items():
            candidates = (self._canonical_name(key), self._canonical_name(recipe["title"]))
            if wanted in candidates:
                return key

        # Accept natural phrases such as "classic grilled cheese" without
        # guessing a different recipe from the library.
        matches = [
            key
            for key, recipe in self._recipes.items()
            if wanted in self._canonical_name(recipe["title"])
            or self._canonical_name(recipe["title"]) in wanted
        ]
        return matches[0] if len(matches) == 1 else None

    @property
    def current_step(self) -> str | None:
        if self.active_recipe is None:
            return None
        return self._recipes[self.active_recipe]["steps"][self.step_index]

    def select_recipe(self, recipe_name: str) -> str:
        """Select a library recipe and reset its cursor to the first step."""
        recipe_key = self._find_recipe_key(recipe_name)
        if recipe_key is None:
            return "I don't have that recipe. Try grilled cheese, scrambled eggs, or mug cake."

        self.active_recipe = recipe_key
        self.step_index = 0
        self._pending_step_index = None
        return self.current_step or "That recipe has no steps."

    def next_step(self) -> str:
        """Advance one step, without ever moving past the recipe's final step."""
        if self.active_recipe is None:
            return "Choose grilled cheese, scrambled eggs, or chocolate mug cake first."

        steps = self._recipes[self.active_recipe]["steps"]
        if self._pending_step_index is not None:
            return steps[self._pending_step_index]
        if self.step_index >= len(steps) - 1:
            return "That recipe is complete. Ask to restart it or choose another recipe."

        # Do not commit until the corresponding audio finishes. If the user
        # barges in, discard_pending_step() keeps them on the current step.
        self._pending_step_index = self.step_index + 1
        return steps[self._pending_step_index]

    def prev_step(self) -> str:
        """Move back one step, without ever moving before the first step."""
        if self.active_recipe is None:
            return "Choose a recipe before going back."
        steps = self._recipes[self.active_recipe]["steps"]
        if self._pending_step_index is not None:
            return steps[self._pending_step_index]
        if self.step_index == 0:
            return "You're already at the first step."

        self._pending_step_index = self.step_index - 1
        return steps[self._pending_step_index]

    def commit_pending_step(self) -> None:
        """Commit a staged next/previous step after its speech completed."""
        if self._pending_step_index is not None:
            self.step_index = self._pending_step_index
            self._pending_step_index = None

    def discard_pending_step(self) -> None:
        """Cancel a staged move when its speech was interrupted."""
        self._pending_step_index = None

    def context_for_llm(self) -> str:
        """Return an authoritative state snapshot for every LLM invocation."""
        if self.active_recipe is None:
            return (
                "RECIPE STATE (authoritative): No active recipe. "
                "Available recipes: Classic Grilled Cheese, Fluffy Scrambled Eggs, "
                "Chocolate Mug Cake."
            )

        recipe = self._recipes[self.active_recipe]
        return (
            "RECIPE STATE (authoritative): "
            f"Active recipe: {recipe['title']}. "
            f"Current step: {self.step_index + 1} of {len(recipe['steps'])}. "
            f"Exact current-step text: {self.current_step}"
        )
