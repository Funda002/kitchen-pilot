"""Deterministic recipe state, lifecycle, and durable session persistence."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_RECIPE_LIBRARY = Path(__file__).parent.parent / "data" / "recipes.json"
STATE_SCHEMA_VERSION = 2
logger = logging.getLogger("kitchen_pilot.state")
VALID_LIFECYCLES = {"idle", "checklist", "cooking", "completed"}


class RecipeStateManager:
    """Own recipe data, navigation, ingredient math, and durable room state."""

    def __init__(self, recipe_library: Path | str = DEFAULT_RECIPE_LIBRARY) -> None:
        self._recipe_library = Path(recipe_library)

        with self._recipe_library.open(encoding="utf-8") as recipe_file:
            recipes: dict[str, dict[str, Any]] = json.load(recipe_file)

        if not isinstance(recipes, dict) or not recipes:
            raise ValueError("The recipe library cannot be empty")

        self._recipes = recipes
        self._stored_recipe_keys = set(recipes)

        self.active_recipe: str | None = None
        self.step_index = -1
        self.servings = 2
        self.lifecycle = "idle"

        self._persistence_room_id: str | None = None

    @staticmethod
    def _canonical_name(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()

    @staticmethod
    def _safe_room_slug(room_id: str) -> str:
        # Keep a readable prefix while adding a hash to prevent room-name
        # collisions such as "a/b" and "a_b".
        slug = re.sub(r"[^a-zA-Z0-9_-]", "_", room_id).strip("_")
        slug = slug[:48] or "default_room"
        digest = hashlib.sha256(room_id.encode("utf-8")).hexdigest()[:12]
        return f"{slug}_{digest}"

    def _state_path(self, room_id: str) -> Path:
        return self._recipe_library.parent / f"session_{self._safe_room_slug(room_id)}.json"

    def _legacy_state_path(self, room_id: str) -> Path:
        slug = re.sub(r"[^a-zA-Z0-9_-]", "_", room_id)
        return self._recipe_library.parent / f"session_{slug}.json"

    def _find_recipe_key(self, recipe_name: str) -> str | None:
        """Resolve natural spoken recipe names to one stored recipe.

        Voice requests often contain harmless context words such as
        "today", "please", or "for me".  Match the meaningful recipe tokens
        against the stored key/title instead of requiring an exact phrase.
        """
        wanted = self._canonical_name(recipe_name).replace("maggie", "maggi")
        if not wanted:
            return None

        stop_words = {
            "a", "an", "the", "some", "please", "today", "tonight",
            "now", "for", "me", "my", "dish", "recipe", "food",
        }
        wanted_tokens = [token for token in wanted.split() if token not in stop_words]
        if not wanted_tokens:
            return None

        # Exact key/title match remains the highest-confidence path.
        for key, recipe in self._recipes.items():
            candidates = (
                self._canonical_name(key),
                self._canonical_name(recipe["title"]),
            )
            if wanted in candidates:
                return key

        # Token-subset match handles requests such as "pancakes today" ->
        # stored key "pancakes" and "chocolate mug cake tonight" -> title.
        matches: list[str] = []
        for key, recipe in self._recipes.items():
            candidate_tokens = set(
                self._canonical_name(key).split()
                + self._canonical_name(recipe["title"]).split()
            )
            if all(token in candidate_tokens for token in wanted_tokens):
                matches.append(key)

        if len(matches) == 1:
            return matches[0]

        # Also support a short spoken name contained within a stored title,
        # e.g. "eggs" for "Fluffy Scrambled Eggs".
        partial_matches = [
            key
            for key, recipe in self._recipes.items()
            if any(
                token in self._canonical_name(key).split()
                or token in self._canonical_name(recipe["title"]).split()
                for token in wanted_tokens
            )
        ]
        return partial_matches[0] if len(partial_matches) == 1 else None

    @property
    def current_step(self) -> str | None:
        if self.active_recipe is None:
            return None
        if self.lifecycle == "checklist" or self.step_index == -1:
            return "CHECKLIST_PHASE: We are checking the ingredient checklist."
        steps = self._recipes[self.active_recipe].get("steps", [])
        if not 0 <= self.step_index < len(steps):
            return None
        return steps[self.step_index]

    def select_recipe(self, recipe_name: str) -> str:
        recipe_key = self._find_recipe_key(recipe_name)
        if recipe_key is None:
            requested = recipe_name.strip() or "that dish"
            return (
                f"RECIPE_NOT_FOUND: I don't have a stored recipe for {requested}. "
                "Please choose a recipe from the recipe library."
            )

        self.active_recipe = recipe_key
        self.step_index = -1
        self.servings = self._recipes[recipe_key].get("base_servings", 2)
        self.lifecycle = "checklist"
        self._save_bound_state()

        title = self._recipes[recipe_key]["title"]
        checklist = self.get_ingredients_list()
        return (
            f"RECIPE_SELECTED_CHECKLIST: I've chosen {title} for {self.servings} people. "
            f"{checklist} Do you have these ingredients ready, or are you missing anything?"
        )

    def is_core_ingredient(self, ingredient: str) -> bool:
        if self.active_recipe is None:
            return False
        core_list = self._recipes[self.active_recipe].get("core_ingredients", [])
        cleaned = ingredient.casefold().strip()
        return any(
            str(core).casefold() in cleaned or cleaned in str(core).casefold()
            for core in core_list
        )

    def set_servings(self, num_people: int) -> str:
        if self.active_recipe is None:
            return "Please select a recipe before setting servings."
        if self.lifecycle != "checklist":
            return "Set servings during the ingredient checklist before cooking starts."
        if num_people < 1 or num_people > 50:
            return "Please select a serving size between one and fifty people."

        self.servings = num_people
        self._save_bound_state()
        return (
            f"I've adjusted the ingredients to serve {self.servings} people. "
            f"{self.get_ingredients_list()}"
        )

    def get_ingredients_list(self) -> str:
        if self.active_recipe is None:
            return "Please select a recipe first."

        recipe = self._recipes[self.active_recipe]

        # Dynamic recipes are no longer part of the normal recipe path.
        # If an old persisted object somehow contains this marker, refuse to
        # expose fabricated ingredient data.
        if recipe.get("dynamic_recipe"):
            return "This recipe is not a stored recipe. Please choose a recipe from the recipe library."

        ingredients = recipe.get("ingredients", [])
        factor = self.servings / recipe.get("base_servings", 2)

        lines: list[str] = []
        for ingredient in ingredients:
            scaled_amount = ingredient["amount"] * factor
            amount_str = (
                f"{int(scaled_amount)}"
                if float(scaled_amount).is_integer()
                else f"{scaled_amount:.1f}"
            )
            unit = str(ingredient.get("unit", "")).strip()
            if unit:
                lines.append(f"{amount_str} {unit} {ingredient['name']}")
            else:
                lines.append(f"{amount_str} {ingredient['name']}")

        return "You will need: " + ", ".join(lines) + "."

    def confirm_ingredients_ready(self) -> str:
        if self.active_recipe is None:
            return "Please select a recipe before starting to cook."

        if self.lifecycle == "completed":
            return f"ENDING_SIGNAL:{self._recipes[self.active_recipe]['title']}"

        if self.lifecycle == "cooking":
            return self.current_step or "Cooking is already underway."

        steps = self._recipes[self.active_recipe].get("steps", [])
        if not steps:
            self.lifecycle = "completed"
            self.step_index = -1
            self._save_bound_state()
            return f"ENDING_SIGNAL:{self._recipes[self.active_recipe]['title']}"

        self.lifecycle = "cooking"
        self.step_index = 0
        self._save_bound_state()
        return self.current_step or "This recipe has no cooking steps."

    def summarize_recipe(self) -> str:
        if self.active_recipe is None:
            return "No active recipe is currently selected to summarize."

        steps = self._recipes[self.active_recipe].get("steps", [])
        summary = " ".join(f"{i + 1}. {step}" for i, step in enumerate(steps))
        return f"Here is the recipe overview in one go: {summary}"

    def jump_to_step(self, step_number: int) -> str:
        if self.active_recipe is None:
            return "Please select a recipe before jumping to steps."
        if self.lifecycle == "checklist":
            return "Finish the ingredient checklist before jumping to a cooking step."

        steps = self._recipes[self.active_recipe].get("steps", [])
        idx = step_number - 1

        if 0 <= idx < len(steps):
            self.step_index = idx
            self.lifecycle = "cooking"
            self._save_bound_state()
            return steps[idx]

        return f"That step number is invalid. This recipe has {len(steps)} steps."

    def next_step(self) -> str:
        if self.active_recipe is None:
            return "Choose a recipe before moving to a cooking step."

        if self.lifecycle == "checklist":
            return "Finish the ingredient checklist before moving to the first cooking step."

        steps = self._recipes[self.active_recipe].get("steps", [])

        if self.lifecycle == "completed":
            return f"ENDING_SIGNAL:{self._recipes[self.active_recipe]['title']}"

        if not steps:
            self.lifecycle = "completed"
            self.step_index = -1
            self._save_bound_state()
            return f"ENDING_SIGNAL:{self._recipes[self.active_recipe]['title']}"

        if self.step_index >= len(steps) - 1:
            title = self._recipes[self.active_recipe]["title"]
            self.lifecycle = "completed"
            self._save_bound_state()
            return f"ENDING_SIGNAL:{title}"

        self.step_index += 1
        self._save_bound_state()
        return steps[self.step_index]

    def prev_step(self) -> str:
        if self.active_recipe is None:
            return "Choose a recipe before going back."
        if self.lifecycle == "checklist":
            return "You are still checking ingredients; cooking has not started."

        steps = self._recipes[self.active_recipe].get("steps", [])
        if not steps:
            return "This recipe has no cooking steps."

        if self.lifecycle == "completed":
            self.lifecycle = "cooking"
            self.step_index = len(steps) - 1

        if self.step_index <= 0:
            return "You're already at the beginning."

        self.step_index -= 1
        self._save_bound_state()
        return steps[self.step_index]

    def inject_dynamic_recipe(self, key: str, title: str, steps: list[str]) -> None:
        self._recipes[key] = {
            "title": title,
            "steps": steps,
            "base_servings": 2,
            "ingredients": [],
            "core_ingredients": [],
            "dynamic_recipe": True,
        }
        self.active_recipe = key
        self.step_index = -1
        self.servings = 2
        self.lifecycle = "checklist"
        self._save_bound_state()

    def _select_dynamic_recipe(self, recipe_name: str) -> str:
        target_recipe = recipe_name.strip() or "your dish"
        title = target_recipe.title()
        key = (
            re.sub(r"[^a-z0-9]+", "_", target_recipe.casefold()).strip("_")
            or "dynamic_recipe"
        )

        self.inject_dynamic_recipe(
            key,
            title,
            [
                f"Prepare your work surface and set out the ingredients for {title}.",
                f"Preheat your cooking equipment or pans safely to begin cooking {title}.",
                f"Combine and cook the components of your {title} carefully.",
                f"Turn off all heat sources, plate your delicious dish, and enjoy it safely!",
            ],
        )

        return (
            f"RECIPE_SELECTED_CHECKLIST: I've designed a safety guide for {title}. "
            f"Before we start, let's verify ingredients. {self.get_ingredients_list()} "
            "Ready to begin?"
        )

    def context_for_llm(self) -> str:
        if self.active_recipe is None:
            return (
                "RECIPE STATE (authoritative): No recipe selected. "
                "Available recipes are the recipes currently in the recipe library."
            )

        recipe = self._recipes[self.active_recipe]

        if self.lifecycle == "checklist":
            progress = "ingredient checklist; cooking has not started"
            step_text = "Confirm ingredients before giving step one."
        elif self.lifecycle == "completed":
            progress = "recipe completed"
            step_text = "No next cooking step remains."
        else:
            progress = (
                f"cooking step {self.step_index + 1} "
                f"of {len(recipe.get('steps', []))}"
            )
            step_text = self.current_step or "No current step available."

        return (
            "RECIPE STATE (authoritative): "
            f"Active recipe: {recipe['title']}. "
            f"Servings: {self.servings} people. "
            f"Lifecycle: {self.lifecycle}. "
            f"Progress: {progress}. "
            f"Exact current step text: {step_text}"
        )

    def recovery_message(self) -> str:
        if self.active_recipe is None:
            return "Welcome back to Kitchen Pilot. What would you like to cook?"

        title = self._recipes[self.active_recipe]["title"]

        if self.lifecycle == "checklist":
            return (
                f"Welcome back. We were preparing {title}. "
                "Let's continue with the ingredient checklist."
            )

        if self.lifecycle == "completed":
            return f"Welcome back. You previously completed {title}."

        return (
            f"Welcome back. We recovered {title}, step {self.step_index + 1}. "
            "Would you like me to repeat the current step?"
        )

    # --------------------------------------------------
    # Durable persistence
    # --------------------------------------------------

    def _build_state(self) -> dict[str, Any]:
        state: dict[str, Any] = {
            "schema_version": STATE_SCHEMA_VERSION,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "active_recipe": self.active_recipe,
            "step_index": self.step_index,
            "servings": self.servings,
            "lifecycle": self.lifecycle,
        }

        if self.active_recipe and self.active_recipe not in self._stored_recipe_keys:
            state["dynamic_recipe"] = self._recipes[self.active_recipe]

        return state

    def _save_bound_state(self) -> None:
        if self._persistence_room_id:
            self.save_state(self._persistence_room_id)

    def save_state(self, room_id: str) -> None:
        """Atomically persist state so a partial write cannot corrupt recovery."""
        path = self._state_path(room_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = self._build_state()

        temp_path: Path | None = None

        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temp_file:
                temp_path = Path(temp_file.name)
                json.dump(payload, temp_file, ensure_ascii=False, indent=2)
                temp_file.flush()
                os.fsync(temp_file.fileno())

            os.replace(temp_path, path)
        except OSError:
            # Persistence must never take down a live cooking session.
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    @staticmethod
    def _validate_state_data(state_data: Any) -> bool:
        if not isinstance(state_data, dict):
            return False

        lifecycle = state_data.get("lifecycle")
        if lifecycle not in VALID_LIFECYCLES:
            return False

        active_recipe = state_data.get("active_recipe")
        step_index = state_data.get("step_index", -1)
        servings = state_data.get("servings", 2)

        if active_recipe is not None and not isinstance(active_recipe, str):
            return False
        if not isinstance(step_index, int):
            return False
        if not isinstance(servings, int) or not 1 <= servings <= 50:
            return False

        if active_recipe is None:
            return lifecycle == "idle" and step_index == -1

        if lifecycle == "checklist" and step_index != -1:
            return False
        if lifecycle in {"cooking", "completed"} and step_index < 0:
            return False

        return True

    def load_state(self, room_id: str) -> bool:
        """Recover only validated state; corrupt state is ignored safely."""
        self._persistence_room_id = room_id
        path = self._state_path(room_id)

        # Read the old Phase 1-3 filename format once for backwards
        # compatibility, then future saves use the collision-safe filename.
        if not path.exists():
            legacy_path = self._legacy_state_path(room_id)
            if legacy_path.exists():
                path = legacy_path
            else:
                return False

        try:
            with path.open(encoding="utf-8") as state_file:
                state_data = json.load(state_file)
        except (OSError, json.JSONDecodeError):
            logger.warning("Could not read state file: %s", path)
            return False

        if not self._validate_state_data(state_data):
            return False

        active_recipe = state_data.get("active_recipe")

        # Recovery may only restore recipes that currently exist in the
        # authoritative recipe library.  This deliberately rejects old
        # dynamically-generated session recipes such as "pancakes_today".
        if active_recipe not in self._recipes:
            logger.info("Ignoring stale session recipe not present in library: %r", active_recipe)
            return False

        if self._recipes[active_recipe].get("dynamic_recipe"):
            logger.info("Ignoring dynamic recipe state for stored recipe key: %r", active_recipe)
            return False

        steps = self._recipes[active_recipe].get("steps", [])
        step_index = state_data.get("step_index", -1)

        if not isinstance(steps, list):
            return False

        if step_index != -1 and not 0 <= step_index < len(steps):
            return False

        self.active_recipe = active_recipe
        self.step_index = step_index
        self.servings = state_data.get("servings", 2)
        self.lifecycle = state_data["lifecycle"]

        # A completed recipe may legally point at its final step.
        if self.lifecycle == "completed" and self.step_index >= len(steps):
            return False

        return True
