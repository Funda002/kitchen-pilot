# Rime Voice Engineering Evidence

## Project

Kitchen Pilot

## Hard Voice Engineering Problem

### Interruption and Recovery

Kitchen Pilot is designed for hands-busy cooking situations where the user may interrupt the assistant while it is speaking.

The system uses Rime as the primary TTS provider.

When the user begins speaking while the assistant is producing speech:

1. The current assistant response is marked as interrupted.
2. Active Rime TTS streams are aborted.
3. The user's new request is processed.
4. Recipe state is not advanced merely because an interruption occurred.
5. The assistant produces a new response based on the latest request.

This prevents stale assistant speech from continuing after the user has changed the request.

---

## Why This Matters

Cooking is an environment where users frequently need to interrupt an assistant.

For example:

- The assistant is explaining a recipe step.
- The user realizes they need clarification.
- The user immediately asks another question.
- The previous speech should stop.
- The new request should become the active interaction.

A voice assistant that continues speaking an obsolete response creates confusion.

---

# Acceptance Test 1: User Barge-In

## Objective

Verify that the assistant stops the current Rime speech when the user interrupts and responds to the new request.

## Procedure

1. Start Kitchen Pilot.
2. Select a recipe.
3. Allow the assistant to begin speaking a recipe instruction.
4. Interrupt the assistant while it is speaking.
5. Ask a different question or request.
6. Observe the resulting behavior.

## Expected Result

- The previous assistant speech is interrupted.
- The active Rime stream is aborted.
- The new user request is processed.
- The assistant responds to the new request.
- The recipe does not accidentally advance because of the interruption.
- No stale response should be spoken after the new request.

## Result

PASS

The interruption/recovery behavior was tested during the Kitchen Pilot demonstration.

---

# Acceptance Test 2: Recipe State Preservation During Interruption

## Objective

Verify that interrupting the assistant does not accidentally advance the cooking workflow.

## Procedure

1. Start a recipe.
2. Allow the assistant to announce the current step.
3. Interrupt the assistant.
4. Ask an unrelated question.
5. Return to the recipe.
6. Ask for the current step or continue the recipe.

## Expected Result

The recipe remains at the correct step.

An interruption is treated as a voice interaction event rather than a recipe-navigation command.

## Result

PASS

---

# Acceptance Test 3: Cooking Timer Event

## Objective

Verify that a timer can operate while the user continues interacting with the assistant.

## Procedure

1. Start a recipe.
2. Set a cooking timer.
3. Continue interacting with the assistant while the timer is running.
4. Allow the timer to expire.
5. Observe the assistant's response.

## Expected Result

When the timer expires:

- The timer event is detected.
- The assistant interrupts the current interaction if necessary.
- A cooking alert is spoken.
- The recipe lifecycle itself is not incorrectly advanced by the timer event.

## Example

User:

"Set a timer for two minutes."

Assistant:

"Timer set for cooking: 2 minutes."

After expiry:

"Time's up! Take the cooking off the heat immediately."

## Result

PASS

---

# Rime Integration

## Speech Provider

Rime is used as the primary TTS provider for spoken assistant responses.

## Rime Model

```text
mistv3