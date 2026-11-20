#!/usr/bin/env python3
"""Ask Google Gemini for a second opinion (used by Claude on every bot fix).

Usage:
    python ask_gemini.py "question"                 # question only
    python ask_gemini.py "question" file1.py file2  # attach files as context
    echo "long text" | python ask_gemini.py -        # question from stdin

Key: either a Claude Code "Network secret" for generativelanguage.googleapis.com
(header x-goog-api-key, injected by the proxy) or env var GEMINI_API_KEY.
Optional GEMINI_MODEL to force a model; otherwise the best available "pro"
model is picked automatically. Standard library only.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

API = "https://generativelanguage.googleapis.com/v1beta"
SYSTEM = (
    "You are a senior quant developer reviewing an automated US-stock options "
    "trading bot (paper trading, intraday CALL/PUT). Another AI (Claude) is "
    "proposing analysis and code changes. Be critical and precise: point out "
    "bugs, wrong assumptions, risk-management flaws and better alternatives. "
    "Say clearly what you agree with and what you disagree with, and why. "
    "Keep it concise."
)


def _headers(key):
    h = {"Content-Type": "application/json"}
    if key:
        h["x-goog-api-key"] = key
    return h


def _get(url, key):
    req = urllib.request.Request(url, headers=_headers(key))
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def _version_key(name):
    nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", name)]
    return nums[0] if nums else 0.0


def candidates(key):
    """Models to try in order; a 404 (retired model) moves to the next one."""
    forced = os.environ.get("GEMINI_MODEL")
    if forced:
        return [forced]
    return ["gemini-4-neon", "gemini-pro-latest", "gemini-3.1-pro-preview", "gemini-2.5-pro",
            "gemini-3.8-flash", "gemini-flash-latest", "gemini-3.5-flash"]


def main():
    key = os.environ.get("GEMINI_API_KEY", "")
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    question = sys.stdin.read() if sys.argv[1] == "-" else sys.argv[1]
    parts = [question]
    for path in sys.argv[2:]:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                parts.append(f"\n--- FILE: {path} ---\n{f.read()[:200_000]}")
        except OSError as e:
            parts.append(f"\n--- FILE: {path} (could not read: {e}) ---")
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"role": "user", "parts": [{"text": "\n".join(parts)}]}],
    }
    err = ""
    for model in candidates(key):
        req = urllib.request.Request(
            f"{API}/models/{model}:generateContent",
            data=json.dumps(body).encode(),
            headers=_headers(key),
        )
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            err = f"Gemini error {e.code} ({model}): {e.read().decode()[:500]}"
            if e.code in (404, 429, 503):
                continue
            sys.exit(err)
        cand = (data.get("candidates") or [{}])[0]
        text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
        print(f"[Gemini model: {model}]\n{text or json.dumps(data)[:1000]}")
        return
    sys.exit(err)


if __name__ == "__main__":
    main()
