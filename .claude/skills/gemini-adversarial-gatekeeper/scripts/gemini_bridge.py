#!/usr/bin/env python3
"""Bridge to the Google Gemini API for adversarial checks and research.

Sends a prompt to Gemini with full parameter control and prints the model's
thinking trace (when available), its response, grounding sources, and token
usage to stdout in clearly delimited sections.

Uses the google-genai SDK when installed; otherwise falls back to the raw
REST API via the standard library, so the only hard requirement is an API
key in GEMINI_API_KEY or GOOGLE_API_KEY.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_MODEL = "gemini-2.5-pro"
REST_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def read_arg_text(value):
    """Resolve a text argument: literal text, '@path' for a file, '-' for stdin."""
    if value is None:
        return None
    if value == "-":
        return sys.stdin.read()
    if value.startswith("@"):
        path = value[1:]
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except OSError as e:
            die(f"Could not read file {path!r}: {e}")
    return value


def die(message, code=1):
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(code)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Query the Gemini API with full parameter control.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--prompt", help="Text prompt. Use '@path' to read a file, '-' for stdin.")
    parser.add_argument("--model", default=None, help=f"Gemini model ID (default: {DEFAULT_MODEL})")
    parser.add_argument("--system_instruction", default=None,
                        help="System prompt / persona. Also accepts '@path'.")
    parser.add_argument("--enable_search", action="store_true", default=None,
                        help="Enable Google Search grounding.")
    parser.add_argument("--thinking_budget", type=int, default=None,
                        help="Thinking token budget: -1 dynamic, 0 off (Flash only), or a positive cap.")
    parser.add_argument("--temperature", type=float, default=None,
                        help="Sampling temperature (0.0-2.0).")
    parser.add_argument("--max_output_tokens", type=int, default=None,
                        help="Cap on response tokens.")
    parser.add_argument("--json", dest="json_payload", default=None,
                        help="JSON object with any of the above keys; explicit flags override it.")
    args = parser.parse_args()

    config = {}
    if args.json_payload:
        try:
            config = json.loads(args.json_payload)
        except json.JSONDecodeError as e:
            die(f"--json is not valid JSON: {e}")
        if not isinstance(config, dict):
            die("--json must be a JSON object")

    for key in ("prompt", "model", "system_instruction", "enable_search",
                "thinking_budget", "temperature", "max_output_tokens"):
        flag_value = getattr(args, key)
        if flag_value is not None:
            config[key] = flag_value

    config.setdefault("model", DEFAULT_MODEL)
    config.setdefault("enable_search", False)
    config["prompt"] = read_arg_text(config.get("prompt"))
    config["system_instruction"] = read_arg_text(config.get("system_instruction"))

    if not config["prompt"]:
        die("A prompt is required (--prompt or the 'prompt' key in --json).")
    return config


def get_api_key():
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        die("No API key found. Set GEMINI_API_KEY (get one at https://aistudio.google.com/apikey).")
    return key


def call_with_sdk(config, api_key):
    from google import genai
    from google.genai import types

    gen_config = {}
    if config.get("system_instruction"):
        gen_config["system_instruction"] = config["system_instruction"]
    if config.get("temperature") is not None:
        gen_config["temperature"] = config["temperature"]
    if config.get("max_output_tokens") is not None:
        gen_config["max_output_tokens"] = config["max_output_tokens"]
    if config.get("thinking_budget") is not None:
        gen_config["thinking_config"] = types.ThinkingConfig(
            thinking_budget=config["thinking_budget"], include_thoughts=True)
    else:
        gen_config["thinking_config"] = types.ThinkingConfig(include_thoughts=True)
    if config.get("enable_search"):
        gen_config["tools"] = [types.Tool(google_search=types.GoogleSearch())]

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=config["model"],
        contents=config["prompt"],
        config=types.GenerateContentConfig(**gen_config),
    )
    return response.model_dump(exclude_none=True) if hasattr(response, "model_dump") else response.to_json_dict()


def call_with_rest(config, api_key):
    body = {"contents": [{"role": "user", "parts": [{"text": config["prompt"]}]}]}
    if config.get("system_instruction"):
        body["systemInstruction"] = {"parts": [{"text": config["system_instruction"]}]}

    generation_config = {"thinkingConfig": {"includeThoughts": True}}
    if config.get("thinking_budget") is not None:
        generation_config["thinkingConfig"]["thinkingBudget"] = config["thinking_budget"]
    if config.get("temperature") is not None:
        generation_config["temperature"] = config["temperature"]
    if config.get("max_output_tokens") is not None:
        generation_config["maxOutputTokens"] = config["max_output_tokens"]
    body["generationConfig"] = generation_config

    if config.get("enable_search"):
        body["tools"] = [{"google_search": {}}]

    url = REST_ENDPOINT.format(model=config["model"])
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=600) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        die(f"Gemini API returned HTTP {e.code}:\n{detail}")
    except urllib.error.URLError as e:
        die(f"Network error reaching the Gemini API: {e.reason}")


def snake_or_camel(d, snake):
    """The SDK dump uses snake_case; the REST API uses camelCase. Accept both."""
    camel = "".join(w.capitalize() if i else w for i, w in enumerate(snake.split("_")))
    return d.get(snake, d.get(camel))


def print_response(data):
    candidates = data.get("candidates") or []
    if not candidates:
        feedback = snake_or_camel(data, "prompt_feedback")
        die(f"No candidates returned. Prompt feedback: {json.dumps(feedback, indent=2)}")
    candidate = candidates[0]

    thoughts, answers = [], []
    for part in (candidate.get("content") or {}).get("parts") or []:
        text = part.get("text")
        if not text:
            continue
        (thoughts if part.get("thought") else answers).append(text)

    if thoughts:
        print("=== THINKING ===")
        print("\n".join(thoughts).strip())
        print()
    print("=== RESPONSE ===")
    print("\n".join(answers).strip() or "(empty response)")

    finish_reason = snake_or_camel(candidate, "finish_reason")
    if finish_reason and str(finish_reason) not in ("STOP", "FinishReason.STOP"):
        print(f"\n[finish reason: {finish_reason}]")

    grounding = snake_or_camel(candidate, "grounding_metadata") or {}
    chunks = snake_or_camel(grounding, "grounding_chunks") or []
    sources = []
    for chunk in chunks:
        web = chunk.get("web") or {}
        if web.get("uri"):
            sources.append(f"- {web.get('title', 'untitled')}: {web['uri']}")
    if sources:
        print("\n=== SOURCES ===")
        print("\n".join(sources))
    queries = snake_or_camel(grounding, "web_search_queries") or []
    if queries:
        print(f"[search queries used: {', '.join(queries)}]")

    usage = snake_or_camel(data, "usage_metadata") or {}
    if usage:
        prompt_t = snake_or_camel(usage, "prompt_token_count") or 0
        thoughts_t = snake_or_camel(usage, "thoughts_token_count") or 0
        output_t = snake_or_camel(usage, "candidates_token_count") or 0
        total_t = snake_or_camel(usage, "total_token_count") or 0
        print(f"\n=== USAGE ===\nprompt={prompt_t} thinking={thoughts_t} output={output_t} total={total_t}")


def main():
    config = parse_args()
    api_key = get_api_key()
    try:
        import google.genai  # noqa: F401
        data = call_with_sdk(config, api_key)
    except ImportError:
        data = call_with_rest(config, api_key)
    print_response(data)


if __name__ == "__main__":
    main()
