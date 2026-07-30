---
name: gemini-adversarial-gatekeeper
description: >-
  Call the Google Gemini API directly as an adversarial checker, second-opinion
  reviewer, or research partner with full parameter control (model choice,
  Google Search grounding, custom system prompts, thinking budget, temperature).
  Use this skill whenever the user asks to "check this with Gemini", "run an
  adversarial check", "get a second opinion from Gemini", "query Gemini with
  web search", "use deep thinking on Gemini", "have Gemini red-team this", or
  otherwise wants Gemini to verify, critique, stress-test, or research
  something. Also trigger when the user wants cross-model validation of code,
  plans, claims, or writing — even if they don't name Gemini explicitly but ask
  for "another model's opinion" or an "independent AI review".
---

# Gemini Adversarial Gatekeeper

This skill lets you (Claude) call the Google AI Studio / Gemini API through
`scripts/gemini_bridge.py` and use Gemini as an adversarial gatekeeper: a
skeptical second model that tries to find flaws in your work, verifies claims
against live web results, or contributes an independent perspective on hard
problems.

The value of this skill comes from *genuine independence*. When you send work
to Gemini for review, give it the artifact and the goal — not your own
conclusions or justifications — so its critique isn't anchored on your framing.
When Gemini's response comes back, engage with it honestly: concede points it
gets right, rebut points it gets wrong with evidence, and surface the
disagreement to the user rather than silently picking a winner.

## Prerequisites

1. **API key** — the script reads `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) from
   the environment. If neither is set, the script exits with a clear error;
   tell the user to export their key from https://aistudio.google.com/apikey.
2. **SDK (optional but preferred)** — `pip install google-genai`. If the SDK
   is not installed, the script automatically falls back to the raw REST API
   using only the Python standard library, so it still works. Don't install
   anything without need — try running it first.

## Invoking the bridge

Run the script with Bash from the skill directory (or by absolute path):

```bash
python3 scripts/gemini_bridge.py \
  --prompt "TEXT OR @/path/to/file" \
  [--model gemini-2.5-pro] \
  [--system_instruction "persona text or @file"] \
  [--enable_search] \
  [--thinking_budget N] \
  [--temperature 0.7] \
  [--max_output_tokens N] \
  [--json '{"prompt": ...}']
```

Argument reference:

| Flag | Meaning | Default |
|---|---|---|
| `--prompt` | The text to send. Prefix with `@` to read from a file (best for long content — avoids shell-quoting pain). Use `-` to read stdin. | required |
| `--model` | Gemini model ID. | `gemini-2.5-pro` |
| `--system_instruction` | System prompt / adversarial persona. Also accepts `@file`. | none |
| `--enable_search` | Turn on Google Search grounding so Gemini can consult live web results; sources are printed with the answer. | off |
| `--thinking_budget` | Thinking-token budget. `-1` = dynamic (model decides), `0` = off (Flash only — Pro cannot disable thinking), positive integer = fixed cap. | model default |
| `--temperature` | Sampling temperature, 0.0–2.0. | API default (1.0) |
| `--max_output_tokens` | Cap on response length. | API default |
| `--json` | Alternative to flags: a single JSON object with keys matching the flag names (`prompt`, `model`, `system_instruction`, `enable_search`, `thinking_budget`, `temperature`, `max_output_tokens`). Flags override JSON values. | — |

The script prints to stdout, in order: a `=== THINKING ===` section with
Gemini's reasoning/thought summary (when the model returns one), a
`=== RESPONSE ===` section with the answer, a `=== SOURCES ===` section with
grounding citations (when search was used), and a `=== USAGE ===` line with
token counts. Read all sections — the thinking trace often reveals *why*
Gemini reached a conclusion, which is exactly what you need to critique it.

## Choosing a model

- **`gemini-2.5-pro`** (default) — strongest reasoning; use for adversarial
  code review, architecture critique, math/logic verification, and anything
  where a shallow answer would be worse than no answer. Thinking is always on
  for Pro; budget range is 128–32768 tokens.
- **`gemini-2.5-flash`** — fast and cheap; use for quick fact checks,
  search-grounded lookups, summarization, and high-volume calls. Thinking
  budget range is 0–24576 (0 disables thinking entirely).
- **`gemini-2.5-flash-lite`** — cheapest; only for trivial checks.
- If the user names a different model (e.g., a newer release), pass it
  through verbatim — don't second-guess an explicit model choice.

## Configuring the call for the task

**Adversarial check (default mode).** Use `gemini-2.5-pro`, a hostile-reviewer
system instruction, low temperature (0.2–0.5) for rigor, and a generous
thinking budget (8192+ for substantial artifacts, `-1` if unsure). Example
persona:

```
You are an adversarial reviewer. Your only job is to find genuine flaws:
bugs, unsound reasoning, unstated assumptions, security issues, and missed
edge cases. Do not praise. Do not soften. If you find no real flaw, say
"NO SUBSTANTIVE OBJECTIONS" and briefly say why the work holds up. Rank
findings by severity and be concrete: point at the exact line, claim, or
step that fails and explain the failure scenario.
```

**Fact verification / research.** Add `--enable_search`. Ask Gemini to cite
sources for each claim and to distinguish what the sources actually say from
its own inference. Search grounding and a custom persona combine fine.

**Deep thinking.** When the user says "use deep thinking" or the problem is
genuinely hard (proofs, tricky concurrency, subtle spec questions), set
`--thinking_budget` high (16384–32768 on Pro) and say in the prompt that
thoroughness matters more than speed.

**Creative / brainstorming partner.** Raise temperature (0.9–1.3), use Flash
or Pro depending on depth needed, and frame the system instruction as a
collaborator rather than a critic.

## Workflow for an adversarial check

1. Assemble the artifact under review into a file (code, plan, argument) and
   pass it with `--prompt @file`. Include enough context for a stranger to
   judge it — the goal, constraints, and the artifact itself — but *not* your
   own defense of it.
2. Call the bridge with an adversarial persona and appropriate thinking budget.
3. Read the thinking trace and response. Triage each finding: **valid** (fix
   it or flag it), **wrong** (rebut with specific evidence), or **ambiguous**
   (investigate before deciding).
4. Report to the user: what Gemini objected to, what you accept, what you
   dispute and why. Never present Gemini's verdict as your own conclusion,
   and never discard it without saying so.
5. For high-stakes checks, consider a second round: send your rebuttals back
   to Gemini and see whether it concedes or holds firm.

## Failure handling

- **Missing API key** → the script says so on stderr; relay the setup
  instruction to the user rather than retrying.
- **HTTP 429 / rate limit** → wait briefly and retry once; if it persists,
  tell the user their quota is exhausted.
- **HTTP 400 on thinking_budget** → the budget is out of range for the model
  (e.g., `0` on Pro); rerun with `-1` or a value in the ranges listed above.
- **Empty response with finish reason `MAX_TOKENS`** → raise
  `--max_output_tokens` or lower the thinking budget.
