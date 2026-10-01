#!/usr/bin/env python3
"""
Diagnose why sarvam-105b returns empty answers. Runs the same real prompt
(CHN-01, detect task) under six settings at once and prints what came back.

    python sarvam_probe.py

Needs SARVAM_API_KEY in the environment, and run_models.py + items.json in
the same folder. Uses a small token ceiling so every variant fails fast.
"""
import json, os, sys, time, threading, urllib.error
import run_models as rm

BASE = "https://api.sarvam.ai/v1/chat/completions"
MAXTOK = 3000

items = json.load(open("items.json", encoding="utf-8"))
item = next(i for i in items if i["item_id"] == "CHN-01")
PROMPT = rm.PROMPT_DETECT.format(text=item["error_text"])

VARIANTS = [
    ("A  temp=0     effort=(not sent)", {"temperature": 0}),
    ("B  temp=0     effort=low",        {"temperature": 0, "reasoning_effort": "low"}),
    ("C  temp=0     effort=null",       {"temperature": 0, "reasoning_effort": None}),
    ("D  temp=(def) effort=(not sent)", {}),
    ("E  temp=(def) effort=low",        {"reasoning_effort": "low"}),
    ("F  temp=(def) effort=null",       {"reasoning_effort": None}),
]

results = {}

def run(label, extra):
    body = {"model": "sarvam-105b", "max_tokens": MAXTOK,
            "messages": [{"role": "user", "content": PROMPT}]}
    body.update(extra)
    headers = {"Content-Type": "application/json",
               "Authorization": "Bearer " + os.environ["SARVAM_API_KEY"]}
    t = time.time()
    try:
        r = rm._post(BASE, body, headers)
        ch = r["choices"][0]
        msg = ch.get("message") or {}
        results[label] = {
            "secs": time.time() - t, "finish": ch.get("finish_reason"),
            "tokens": (r.get("usage") or {}).get("completion_tokens"),
            "content": msg.get("content") or "",
            "reasoning": msg.get("reasoning_content") or "",
            "keys": list(msg.keys()),
        }
    except urllib.error.HTTPError as e:
        results[label] = {"secs": time.time() - t,
                          "http_error": "HTTP %s %s" % (e.code, e.read().decode("utf-8", "replace")[:400])}
    except Exception as e:                                # noqa: BLE001
        results[label] = {"secs": time.time() - t, "http_error": "%s: %s" % (type(e).__name__, e)}

def looks_looped(text):
    tail = text[-120:]
    return len(tail) >= 60 and text.count(tail) >= 3

threads = [threading.Thread(target=run, args=v) for v in VARIANTS]
[t.start() for t in threads]
[t.join() for t in threads]

for label, _ in VARIANTS:
    r = results[label]
    print("\n" + "=" * 78)
    print(label, " (%.0fs)" % r["secs"])
    if "http_error" in r:
        print("  ERROR:", r["http_error"]); continue
    print("  finish=%s  completion_tokens=%s  content_chars=%d  reasoning_chars=%d"
          % (r["finish"], r["tokens"], len(r["content"]), len(r["reasoning"])))
    if r["content"]:
        print("  CONTENT  :", r["content"][:300].replace("\n", " / "))
    else:
        print("  CONTENT  : (empty)")
    if r["reasoning"]:
        print("  LOOPING? :", "YES - the same text repeats" if looks_looped(r["reasoning"]) else "no")
        print("  REASONING (last 250 chars):", r["reasoning"][-250:].replace("\n", " / "))
print("\nPaste this whole output back.")
