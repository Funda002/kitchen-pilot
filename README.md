# 🍳 Kitchen Pilot

> **A voice-native cooking assistant built for the DataForge 2026 × Rime
> Hackathon Challenge**

Kitchen Pilot is a hands-free, realtime cooking companion designed for
situations where the user's hands are busy and interacting with a screen
is inconvenient.

Instead of being a chatbot with a play button, Kitchen Pilot is designed
around **voice as the primary interface**: the user speaks, the system
listens, deterministic application logic manages recipes and timers, and
**Rime-generated speech** provides the spoken response.

------------------------------------------------------------------------

## 🏆 Hackathon

**DataForge 2026 --- Rime Hackathon Challenge**\
**Organizer:** Kharagpur Data Analytics Group (KDAG), IIT Kharagpur\
**Challenge:** DataForge × Rime\
**Track:** Voice-native AI

### Why Kitchen Pilot fits

The challenge requires a product where removing speech would materially
damage the experience.

Kitchen Pilot is designed for a **hands-busy cooking environment**:

-   Select and control recipes by voice.
-   Hear cooking steps instead of reading a screen.
-   Navigate naturally with commands such as "next", "continue", and
    "move on".
-   Run cooking timers in the background.
-   Receive spoken timer-expiry alerts.
-   Interrupt the assistant while it is speaking.
-   Use Rime as the **primary spoken output**.
-   Keep recipe state deterministic so the LLM cannot invent or skip
    steps.

------------------------------------------------------------------------
# 🎥 Demo Implementation

> **Watch the complete working demo of Kitchen Pilot**

[▶️ **Watch Kitchen Pilot Demo on YouTube**](YOUR_YOUTUBE_LINK)

The demo demonstrates:

- Voice-based recipe selection
- Deterministic recipe navigation
- Cooking timers with spoken alerts
- Voice interruption and recovery
- Rime-powered speech output
- Realtime voice interaction through LiveKit

# 🎯 Problem

Cooking is a naturally hands-busy activity.

A conventional recipe application forces the user to repeatedly:

1.  Stop cooking.
2.  Clean or dry their hands.
3.  Look at a screen.
4.  Find the current step.
5.  Set a timer.
6.  Return to cooking.
7.  Repeat.

A normal text chatbot does not completely solve this. It may lose recipe
progress, repeat actions, hallucinate steps, confuse timers with recipe
state, or continue speaking after the user starts talking.

Kitchen Pilot treats the kitchen as a **voice-first environment**, where
the assistant behaves more like a cooking partner than a text chatbot.

------------------------------------------------------------------------

# 💡 Solution

Kitchen Pilot combines realtime speech processing with deterministic
application state.

The core design is:

``` text
USER SPEECH
     ↓
SPEECH-TO-TEXT
     ↓
DETERMINISTIC INTENT ROUTER
     ↓
┌─────────────────────────────┐
│ Recipe action               │
│ Timer action                │
│ Ordinary conversation      │
└─────────────────────────────┘
     ↓
APPLICATION RESULT / LLM
     ↓
RIME TEXT-TO-SPEECH
     ↓
AUDIO
     ↓
USER
```

The LLM is deliberately **not the owner of recipe progress**.

For example:

> User: "Next step."

The application directly advances the recipe by exactly one step. The
LLM does not decide what the next step should be.

------------------------------------------------------------------------

# 🧠 Architecture

``` mermaid
flowchart TD
    U["User Voice"] --> LK["LiveKit AgentSession"]
    LK --> VAD["Silero VAD / Turn Handling"]
    VAD --> STT["Deepgram STT"]
    STT --> R["Deterministic Intent Router"]

    R --> REC["Recipe State Manager"]
    R --> TIMER["Timer Manager"]
    R --> LLM["OpenAI GPT-4o-mini"]

    REC --> RESULT["Application Result"]
    TIMER --> RESULT
    LLM --> RESULT

    RESULT --> NORM["Voice Text Normalizer"]
    NORM --> RIME["Rime TTS - mistv3"]
    RIME --> AUDIO["24 kHz Mono Audio"]
    AUDIO --> LK
    LK --> U

    TIMER --> EVENT["Timer Expiry Event"]
    EVENT --> STOP["Interrupt Current Speech"]
    STOP --> RIME
```

### Runtime flow

``` text
                     ┌─────────────────────┐
                     │       USER          │
                     │   speaks naturally  │
                     └──────────┬──────────┘
                                │
                                ▼
                     ┌─────────────────────┐
                     │     LiveKit         │
                     │   AgentSession      │
                     └──────────┬──────────┘
                                │
                                ▼
                     ┌─────────────────────┐
                     │     Deepgram STT    │
                     └──────────┬──────────┘
                                │
                                ▼
                  ┌───────────────────────────┐
                  │   Deterministic Router    │
                  └─────────────┬─────────────┘
                                │
              ┌─────────────────┼─────────────────┐
              │                 │                 │
              ▼                 ▼                 ▼
       ┌─────────────┐  ┌─────────────┐  ┌──────────────┐
       │   Recipe    │  │    Timer    │  │     LLM      │
       │ State       │  │   Manager   │  │ GPT-4o-mini  │
       └──────┬──────┘  └──────┬──────┘  └──────┬───────┘
              │                 │                 │
              └─────────────────┼─────────────────┘
                                ▼
                       ┌─────────────────┐
                       │ Rime TTS        │
                       │ mistv3          │
                       └────────┬────────┘
                                │
                                ▼
                       ┌─────────────────┐
                       │ Spoken response │
                       └─────────────────┘
```

------------------------------------------------------------------------

# 🔥 Primary Voice Problem: Interruption and Recovery

The primary hard voice problem Kitchen Pilot targets is:

> **How can a voice cooking assistant stop speaking promptly when the
> user barges in, while keeping application state correct?**

This matters in cooking because users naturally need to interrupt:

-   "Wait."
-   "Stop."
-   "Actually, I already did that."
-   "What's the next step?"
-   "Set a timer for two minutes."
-   "I don't have ginger."

When the user barges in while the assistant is speaking:

``` text
Assistant speaking
      │
      ▼
User starts speaking
      │
      ▼
LiveKit detects user speech
      │
      ▼
note_interruption()
      │
      ▼
abort active Rime streams
      │
      ▼
Treat newest input as a fresh request
      │
      ▼
Do not mutate recipe state just because speech was interrupted
```

This deliberately separates:

``` text
USER INTERRUPTS
      ≠
RECIPE ADVANCES
```

------------------------------------------------------------------------

# 🧭 Deterministic Recipe Lifecycle

Recipe progress is owned by Python application logic.

``` text
idle
  │
  ▼
checklist
  │
  │ ingredients confirmed
  ▼
cooking
  │
  │ final step completed
  ▼
completed
```

Example:

``` text
User:
"I want to make Indian chai."

        ↓

Select Indian Masala Chai

        ↓

Ingredient checklist

User:
"I've got everything. Let's start."

        ↓

Checklist → Cooking

        ↓

Step 1
```

The LLM cannot independently decide that the recipe moved to another
step.

This prevents agentic tool loops and hallucinated recipe progress.

------------------------------------------------------------------------

# 🧩 Deterministic Intent Routing

The router processes supported application commands before ordinary LLM
conversation.

Approximate priority:

``` text
1. Timer request
2. Recipe selection
3. Serving count
4. Checklist confirmation / start
5. Ingredient request
6. Next-step navigation
7. Previous-step navigation
8. Current-step question
9. Recipe summary
10. Ordinary conversation → LLM
```

Examples of deterministic navigation:

``` text
"next"
"next step"
"continue"
"go ahead"
"move on"
"what's next?"
"I've finished, let's continue"
```

Timer requests such as:

``` text
"Set a timer for two minutes."
```

are routed directly to the timer system.

Ordinary questions such as:

``` text
"Why does tea become darker when it simmers?"
```

go to the LLM without recipe-navigation tools.

------------------------------------------------------------------------

# 🍲 Recipe State Manager

`RecipeStateManager` is the source of truth for cooking.

It manages:

-   active recipe
-   serving count
-   ingredient checklist
-   lifecycle
-   current step
-   next step
-   previous step
-   explicit step jumps
-   completion
-   recipe summary
-   persistence
-   recipe matching

The stored recipe step is returned directly rather than asking the LLM
to recreate it.

> **Recipe data is authoritative. The LLM is conversational, not
> authoritative.**

------------------------------------------------------------------------

# ⏱️ Cooking Timers

Kitchen Pilot contains a session-local `TimerManager`.

Each timer tracks:

-   timer ID
-   label
-   start timestamp
-   duration
-   end timestamp
-   status
-   background asyncio task

Timers use the real system clock.

Example:

``` text
User:
"Set a timer for two minutes."

Assistant:
"Timer set for cooking: 2 minutes."

        ↓

Background timer continues

        ↓

Timer expires

        ↓

Application event

        ↓

Current speech is interrupted

        ↓

Rime announces:

"Time's up! Take the cooking off the heat immediately."
```

Timer expiry is independent of recipe progress.

------------------------------------------------------------------------

# 🔊 Rime Integration

Rime is the **primary TTS provider** for Kitchen Pilot.

The project implements a custom LiveKit-compatible `RimeTTS` class that:

-   connects to the Rime HTTP streaming endpoint,
-   streams generated audio,
-   converts the stream into LiveKit audio frames,
-   tracks active streams,
-   supports aborting active streams,
-   handles API errors and timeouts,
-   integrates with the LiveKit TTS pipeline.

### Rime configuration

  Parameter      Value
  -------------- -----------------
  Provider       Rime
  Model          `mistv3`
  Speaker        `RIME_SPEAKER`
  Language       English
  Audio format   24 kHz, mono
  Transport      HTTP streaming
  Endpoint       `RIME_BASE_URL`

**Submission requirement:** replace the environment-variable names in
the table with the exact speaker ID and exact endpoint used in the final
judged demo. Do not expose the API key.

------------------------------------------------------------------------

# 👂 Voice-Native Delivery

Kitchen Pilot is designed for the ear rather than for reading.

The voice system uses:

-   concise responses,
-   plain spoken language,
-   short cooking instructions,
-   minimal filler,
-   longer responses only when safety requires them.

Text is normalized before reaching Rime. Sentence boundaries are used to
make streaming speech more natural.

------------------------------------------------------------------------

# 🧠 LLM Responsibilities

Kitchen Pilot uses:

**OpenAI `gpt-4o-mini`**

The LLM handles:

-   ordinary conversation,
-   trivia,
-   contextual discussion,
-   natural-language explanations.

It does **not** own:

-   recipe progression,
-   recipe step generation,
-   timer state,
-   checklist state,
-   completion state.

For ordinary conversation, recipe tools are deliberately withheld to
prevent accidental state changes.

------------------------------------------------------------------------

# 🍽️ Ingredients and Servings

Users can change serving size before cooking begins.

Example:

``` text
User:
"Make it for four people."

        ↓

Deterministic serving update

        ↓

Scaled ingredient quantities
```

Serving changes are blocked once cooking starts.

Ingredient substitutions are also deterministic. The substitution system
can identify:

-   available substitute,
-   replacement ratio,
-   whether the ingredient is core,
-   whether a substitute is unavailable.

The LLM is instructed not to invent substitutions.

------------------------------------------------------------------------

# 📁 Project Structure

``` text
kitchen_copilot/
│
├── agent.py
│
├── src/
│   ├── config.py
│   │
│   ├── services/
│   │   ├── rime_tts.py
│   │   ├── state_manager.py
│   │   └── tool_service.py
│   │
│   └── utils/
│       └── text_normalizer.py
│
├── recipes/
│   └── kitchen_pilot_35_recipe_library.json
│
├── RIME_EVIDENCE.md
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

Adjust the recipe-library path if it is stored elsewhere in the
repository.

------------------------------------------------------------------------

# 🛠️ Main Components

## `agent.py`

Main LiveKit agent.

Responsibilities:

-   connect to LiveKit,
-   initialize Deepgram,
-   initialize OpenAI,
-   initialize Rime,
-   initialize Silero VAD,
-   create `AgentSession`,
-   route deterministic commands,
-   handle user interruptions,
-   connect timer events to spoken alerts,
-   start the worker.

------------------------------------------------------------------------

## `src/services/rime_tts.py`

Custom Rime TTS integration.

Responsibilities:

-   Rime API communication,
-   streaming audio,
-   LiveKit audio frames,
-   active stream tracking,
-   cancellation,
-   timeout/error handling.

The `abort_all()` capability is central to the interruption design.

------------------------------------------------------------------------

## `src/services/state_manager.py`

Deterministic recipe state machine.

Responsibilities:

``` text
Recipe selection
Serving count
Ingredient checklist
Checklist confirmation
Step navigation
Step repetition
Completion
Persistence
Recipe context
```

------------------------------------------------------------------------

## `src/services/tool_service.py`

Kitchen utilities.

Contains:

``` text
TimerRecord
TimerManager
KitchenTools
```

Timer operations include:

-   create timer,
-   remaining time,
-   elapsed time,
-   finish time,
-   cancel timer,
-   expiry callback.

Substitution functionality is also provided here.

------------------------------------------------------------------------

## `src/utils/text_normalizer.py`

Prepares text for speech synthesis.

It handles normalization needed for more consistent spoken output and
pronunciation.

------------------------------------------------------------------------

# 🍵 Demo Recipe: Indian Masala Chai

The primary demo recipe is Indian Masala Chai.

Example flow:

``` text
Assistant:
"Hey! What would you like to cook today?"

User:
"I want to make some Indian chai."

Assistant:
"Indian Masala Chai."

User:
"Yeah, I've got everything. Let's start."

Assistant:
"Step 1: Crush the ginger, cardamom, and cloves lightly to release their flavor."

User:
"Okay. Next."

Assistant:
"Add the water, crushed spices, and ginger to a saucepan and bring it to a gentle boil."

User:
"Set a timer for two minutes."

Assistant:
"Timer set for cooking: 2 minutes."

...

Timer expires.

Assistant:
"Time's up! Take the cooking off the heat immediately."
```

The user can continue talking while the timer runs.

------------------------------------------------------------------------

# 🧪 Voice Acceptance Test

The Rime challenge emphasizes measuring user-visible behavior rather
than making unsupported claims.

## Interruption test

1.  Start a recipe.
2.  Cause the assistant to speak.
3.  Interrupt while Rime audio is playing.
4.  Immediately issue a different request.
5.  Verify the old speech stops.
6.  Verify the new request is processed.
7.  Verify recipe state did not accidentally advance.
8.  Verify stale speech does not return.

Example:

``` text
Assistant:
"The next step is to add the milk and sugar, then bring..."

User:
"Wait — what's the current step?"

Expected:

Old Rime speech stops
        ↓
New request is processed
        ↓
Current step is returned
        ↓
Recipe position remains correct
```

------------------------------------------------------------------------

# ⏱️ Timer Acceptance Test

``` text
User:
"Set a timer for ten seconds."

Assistant:
"Timer set for cooking: 10 seconds."

        ↓

User continues conversation.

        ↓

Timer expires.

        ↓

Application creates timer event.

        ↓

Current speech is interrupted.

        ↓

Rime speaks the timer alert.
```

Expected:

-   timer runs independently,
-   timer expiry comes from the application,
-   recipe state does not change,
-   alert is spoken through Rime,
-   conversation can continue afterward.

------------------------------------------------------------------------

# 📊 Acceptance Criteria

  Behavior                  Expected result
  ------------------------- -------------------------------------
  Recipe selection          Deterministic recipe selected
  Natural recipe alias      Correct stored recipe selected
  Ingredient request        Stored/scaled ingredients returned
  Serving change            Works before cooking
  Ingredient confirmation   Checklist → Step 1
  "Next"                    Advances exactly one step
  "Previous"                Moves back one step
  Current-step question     Does not advance
  Completion                Deterministic completion
  Timer creation            Background timer starts
  Timer expiry              Spoken application event
  Timer expiry              Does not mutate recipe state
  User interruption         Active Rime speech is aborted
  Trivia                    LLM answers without changing recipe
  Substitution              Deterministic substitution response

------------------------------------------------------------------------

# ⚠️ Known Limitations

Kitchen Pilot is a hackathon prototype, not a fully productionized
consumer application.

### Timer queries

Timer creation and expiry are implemented. Remaining-time and related
natural-language timer queries require additional coverage before being
considered completely production-ready.

### Network dependency

Realtime interaction depends on:

-   LiveKit
-   Deepgram
-   OpenAI
-   Rime

Network failures can affect voice interaction.

### Session-local timers

Timers are owned by the active session and are not designed to survive a
complete process shutdown.

### Persistence

Recipe progress can be persisted by room/session, but this is not a
distributed production database.

### Speech recognition

STT performance depends on microphone quality, background kitchen noise,
accent, speech rate, and network conditions.

### Safety

Kitchen Pilot does not replace normal cooking safety judgment. Users
remain responsible for heat, knives, appliances, hot liquids, allergies,
and food safety.

------------------------------------------------------------------------

# 🔐 Configuration

Create a local `.env` file.

Example:

``` env
LIVEKIT_URL=your_livekit_url
LIVEKIT_API_KEY=your_livekit_api_key
LIVEKIT_API_SECRET=your_livekit_api_secret

DEEPGRAM_API_KEY=your_deepgram_api_key
OPENAI_API_KEY=your_openai_api_key

RIME_API_KEY=your_rime_api_key
RIME_SPEAKER=your_rime_speaker
RIME_BASE_URL=your_rime_endpoint
```

Never commit:

``` text
.env
API keys
API secrets
LiveKit credentials
Rime credentials
Deepgram credentials
OpenAI credentials
```

`.env.example` must contain placeholders only.

------------------------------------------------------------------------

# 🚀 Installation

## 1. Clone

``` bash
git clone <YOUR_GITHUB_REPOSITORY>
cd kitchen_copilot
```

## 2. Create virtual environment

Windows:

``` bash
python -m venv .venv
.venv\Scriptsctivate
```

Linux/macOS:

``` bash
python -m venv .venv
source .venv/bin/activate
```

## 3. Install dependencies

``` bash
pip install -r requirements.txt
```

The current implementation uses LiveKit Agents 1.8.x.

## 4. Configure secrets

Copy `.env.example` to `.env` and add the required credentials.

## 5. Start the agent

Current development command:

``` bash
python agent.py dev
```

If the newer LiveKit CLI is installed:

``` bash
lk agent dev
```

------------------------------------------------------------------------

# 🔌 Third-Party Services

  -----------------------------------------------------------------------
  Service                             Purpose
  ----------------------------------- -----------------------------------
  LiveKit                             Realtime audio transport, rooms,
                                      sessions and turn handling

  Deepgram                            Realtime speech-to-text

  OpenAI                              Conversational reasoning and trivia

  Rime                                Primary text-to-speech

  Silero                              Voice activity detection
  -----------------------------------------------------------------------

Rime provides the spoken output; the application remains responsible for
input handling, reasoning, orchestration, state, transport, timers and
evaluation.

------------------------------------------------------------------------

# 🔄 Failure Behavior

### STT failure

The user may need to repeat the request.

### OpenAI failure

Ordinary conversational responses may be unavailable.

### Rime failure

Speech synthesis can fail or time out. The application logs the error
rather than inventing a spoken result.

### Timer expiry

The timer creates an application event and does not advance the recipe.

### User interruption

Active Rime streams are aborted and the newest request is processed
independently.

------------------------------------------------------------------------

# 🧱 Design Principles

### 1. State belongs to the application

``` text
LLM ≠ source of truth
Python state = source of truth
```

### 2. Voice is the interface

The user should not need to touch a screen for basic recipe navigation.

### 3. Deterministic actions execute once

``` text
"Next step."
        ↓
one application transition
        ↓
one exact stored step
```

### 4. Timers are independent events

A timer finishing does not mean the recipe moved to the next step.

### 5. Interruptions do not corrupt state

Stopping speech must not automatically change the cooking lifecycle.

### 6. Rime is primary

Rime is used for the actual cooking conversation, instructions and timer
alerts, not merely for a welcome message.

------------------------------------------------------------------------

# 📄 RIME_EVIDENCE.md

The final submission should include `RIME_EVIDENCE.md`.

It should document:

``` text
1. Hard voice claim
2. Acceptance test
3. Test procedure
4. Result
5. Limitations
6. Repeatable command / fixture
7. Exact Rime model ID
8. Exact Rime speaker
9. Language
10. Endpoint
11. Audio format
12. Transport
```

Suggested claim:

> **Kitchen Pilot addresses interruption and recovery in a hands-busy
> cooking workflow by stopping active Rime speech when the user barges
> in, treating the new request as fresh input, and keeping recipe state
> independent from speech interruption.**

Do not publish measured performance numbers until they have actually
been measured.

------------------------------------------------------------------------

# 🎥 Recommended 4--5 Minute Demo

### 0:00--0:30 --- Problem

Show a user cooking with occupied hands.

Explain:

> "A normal recipe app makes me repeatedly stop cooking to interact with
> a screen."

### 0:30--1:20 --- Start cooking

``` text
"I want to make some Indian chai."

"I've got everything. Let's start."
```

Show recipe selection and Step 1.

### 1:20--2:00 --- Natural navigation

``` text
"Next."

"What's the current step?"

"Okay, move on."
```

Show that a current-step question does not accidentally advance the
recipe.

### 2:00--2:40 --- Timer

``` text
"Set a timer for two minutes."
```

Continue a normal conversation while the timer runs.

### 2:40--3:30 --- Hard voice challenge

Interrupt while Kitchen Pilot is speaking:

``` text
"Wait — what's the current step?"
```

Show:

``` text
Rime speech stops
        ↓
new request
        ↓
correct state
        ↓
new Rime response
```

### 3:30--4:10 --- Timer event

Allow a short timer to expire.

Show:

``` text
timer expiry
     ↓
application event
     ↓
speech interruption
     ↓
Rime alert
```

### 4:10--4:40 --- Architecture

Briefly show:

``` text
LiveKit
  ↓
Deepgram
  ↓
Deterministic Router
  ↓
Recipe State / Timers / LLM
  ↓
Rime
```

------------------------------------------------------------------------

# 🏁 Project Status

### Implemented

-   [x] Realtime voice interaction
-   [x] LiveKit AgentSession
-   [x] Deepgram STT
-   [x] OpenAI GPT-4o-mini
-   [x] Rime TTS integration
-   [x] Silero VAD
-   [x] Deterministic recipe selection
-   [x] Natural recipe aliases
-   [x] Serving scaling
-   [x] Ingredient checklist
-   [x] Deterministic recipe lifecycle
-   [x] Next/previous navigation
-   [x] Current-step inspection
-   [x] Recipe completion
-   [x] Recipe persistence
-   [x] Background cooking timers
-   [x] Timer expiry events
-   [x] Timer spoken alerts
-   [x] Voice interruption handling
-   [x] Active Rime stream cancellation
-   [x] Voice text normalization
-   [x] Ingredient substitution handling
-   [x] Indian Masala Chai demo recipe
-   [x] 35-recipe library

### Future work

-   [ ] Formal interruption latency measurement
-   [ ] Formal stale-result/fencing stress test
-   [ ] Complete remaining-time voice query coverage
-   [ ] Multilingual/code-switched speech
-   [ ] Distributed persistent state
-   [ ] Production deployment
-   [ ] Larger automated voice regression suite

------------------------------------------------------------------------

# 🤝 Development

Create a feature branch:

``` bash
git checkout -b feature/<feature-name>
```

After testing:

``` bash
git add .
git commit -m "Describe the change"
git push origin <branch-name>
```

Before committing:

``` bash
git status
```

Verify that no secrets are included.

------------------------------------------------------------------------

# 🔒 Security

Never commit credentials.

Keep these local:

``` text
.env
API keys
API secrets
LiveKit credentials
Rime credentials
Deepgram credentials
OpenAI credentials
```

Only placeholders should appear in documentation and `.env.example`.

------------------------------------------------------------------------

# 📜 License

Add the project's chosen license before public release.

------------------------------------------------------------------------

# 👨‍🍳 Kitchen Pilot

**Voice-native cooking assistance for hands-busy kitchens.**

Built for:

**DataForge 2026 × Rime**

> Don't make the cook stop cooking. Let the cook talk.
