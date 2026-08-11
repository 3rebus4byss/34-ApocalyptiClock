"""
Standalone test: confirms GDELT_CLOUD_API_KEY works and shows us the exact
shape of a real response, so we can write correct parsing code instead of
guessing field names from the docs alone.
"""
import json
import os
import sys

import requests

api_key = os.environ.get("GDELT_CLOUD_API_KEY")
if not api_key:
    print("GDELT_CLOUD_API_KEY not set in this shell.")
    print("Run: export GDELT_CLOUD_API_KEY=gdelt_sk_...")
    sys.exit(1)

BASE = "https://gdeltcloud.com/api/v2"
headers = {"Authorization": f"Bearer {api_key}"}

print("--- Testing /stories endpoint ---")
resp = requests.get(f"{BASE}/stories", headers=headers,
                     params={"search": "nuclear weapons", "limit": 3})
print(f"HTTP status: {resp.status_code}")
if resp.status_code == 429:
    print(f"Retry-After header: {resp.headers.get('Retry-After')}")
    sys.exit(1)
if resp.status_code != 200:
    print("Response body:", resp.text[:1000])
    sys.exit(1)

data = resp.json()
print("\nFull raw response (pretty-printed):")
print(json.dumps(data, indent=2)[:3000])

print("\n--- Testing /events endpoint too, for comparison ---")
resp2 = requests.get(f"{BASE}/events", headers=headers,
                      params={"category": "Battles", "limit": 3})
print(f"HTTP status: {resp2.status_code}")
if resp2.status_code == 200:
    print(json.dumps(resp2.json(), indent=2)[:3000])
