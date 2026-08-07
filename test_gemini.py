"""
Standalone test: confirms GEMINI_API_KEY works and the API call/response
parsing is correct, without touching GDELT at all. Run this while GDELT
is cooling down to make progress on the other half of the pipeline.
"""
import json
import os
import sys

import requests

api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    print("GEMINI_API_KEY not set in this shell. Run: set -a; source .env; set +a")
    sys.exit(1)

GEMINI_MODEL = "gemini-2.5-flash-lite"
GEMINI_ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

fake_headlines = [
    {"title": "Tensions rise after border skirmish reported", "url": "https://example.com/a"},
    {"title": "Diplomats call for calm amid escalating rhetoric", "url": "https://example.com/b"},
]

digest = "\n".join(f"- {h['title']} ({h['url']})" for h in fake_headlines)

system_prompt = (
    "You are testing an API integration. You will be given fake headlines. "
    "Respond with ONLY a JSON object, no other text, no markdown fences, in exactly "
    "this shape:\n"
    '{"direction": "worse" | "better" | "steady", '
    '"delta_seconds": <number between -1.0 and 1.0>, '
    '"rationale": "<1-2 sentences>", '
    '"cited_urls": ["<url1>", "<url2>"]}'
)
user_prompt = f"Fake test headlines:\n{digest}\n\nReturn your JSON assessment now."

body = {
    "system_instruction": {"parts": [{"text": system_prompt}]},
    "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
    "generationConfig": {"maxOutputTokens": 400, "responseMimeType": "application/json"},
}

print(f"Calling {GEMINI_ENDPOINT} ...")
resp = requests.post(GEMINI_ENDPOINT, params={"key": api_key}, json=body, timeout=30)
print(f"HTTP status: {resp.status_code}")

if resp.status_code != 200:
    print("Response body:", resp.text[:2000])
    sys.exit(1)

payload = resp.json()
text = payload["candidates"][0]["content"]["parts"][0]["text"]
print("Raw model output:", text)

parsed = json.loads(text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip())
print("\nParsed successfully:")
print(json.dumps(parsed, indent=2))
print("\n✅ Gemini integration works.")
