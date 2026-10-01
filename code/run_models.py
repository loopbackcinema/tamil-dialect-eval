#!/usr/bin/env python3
"""
Tamil AI evaluation benchmark — model run harness.

Runs every item against every configured model, for both tasks, at
temperature 0, and writes three CSVs:

  runs_raw.csv    every response with the real model name  (keep private)
  runs_blind.csv  shuffled, model_code only                (give to scorers)
  model_key.csv   M1/M2/M3 -> real model + version + params

Resumable: already-completed calls are read from runs_log.jsonl and skipped.

Usage:
    python run_models.py --items items.json --models models.json
    python run_models.py --items items.json --models models.json --limit 3   # smoke test
"""

import argparse, csv, json, os, random, sys, time, hashlib, datetime
import threading, concurrent.futures
import urllib.request, urllib.error

# ---------------------------------------------------------------- prompts
# FROZEN. Do not edit after the first real run — changing a single word
# invalidates comparison with anything already collected.

PROMPT_CORRECT = (
    "கீழே தரப்பட்டுள்ள தமிழ்ப் பத்தியில் இலக்கணப் பிழைகள் உள்ளன. "
    "இப்பத்தி பேச்சுத் தமிழில் இருக்கலாம்; வட்டார வழக்கு பிழை அல்ல — "
    "வட்டார வடிவங்களை மாற்றாமல், இலக்கணப் பிழைகளை மட்டும் திருத்துக. "
    "திருத்தப்பட்ட பத்தியை மட்டும் தருக. விளக்கம் எதுவும் வேண்டாம்.\n\n"
    "பத்தி:\n{text}"
)

PROMPT_DETECT = (
    "கீழே தரப்பட்டுள்ள தமிழ்ப் பத்தியில் இலக்கணப் பிழைகள் உள்ளன. "
    "இப்பத்தி பேச்சுத் தமிழில் இருக்கலாம்; வட்டார வழக்கு பிழை அல்ல. "
    "பிழை உள்ள ஒவ்வொரு சொல்லையும் வரிசையாகப் பட்டியலிடுக. "
    "ஒவ்வொரு வரியும் சரியாக இந்த வடிவத்தில் இருக்க வேண்டும்:\n"
    "பிழையான சொல் | சரியான வடிவம்\n"
    "வேறு எதுவும் எழுத வேண்டாம்.\n\n"
    "பத்தி:\n{text}"
)

TASKS = {"correct": PROMPT_CORRECT, "detect": PROMPT_DETECT}

TEMPERATURE = 0
MAX_TOKENS = 1500
TIMEOUT = 120
RETRIES = 5
PAUSE = 1.0          # seconds between calls, raise if you hit rate limits


# ---------------------------------------------------------------- http
def _post(url, payload, headers):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def call_openai_compatible(cfg, prompt):
    """OpenAI, Sarvam, Krutrim, OpenRouter, Together, vLLM — same wire format.

    Per-model options in models.json:
        "token_param": "max_completion_tokens"   (gpt-5 and later reject max_tokens)
        "omit_temperature": true                 (gpt-5 accepts only the default)
        "max_tokens": 16000                      (reasoning tokens count against this)
        "extra_body": {...}                      (any provider-specific parameter)
    """
    url = cfg.get("base_url", "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
    body = {
        "model": cfg["model"],
        "messages": [{"role": "user", "content": prompt}],
    }
    body[cfg.get("token_param", "max_tokens")] = cfg.get("max_tokens", MAX_TOKENS)
    if not cfg.get("omit_temperature"):
        body["temperature"] = TEMPERATURE
    body.update(cfg.get("extra_body", {}))
    h = {"Content-Type": "application/json",
         "Authorization": "Bearer " + os.environ[cfg["api_key_env"]]}
    r = _post(url, body, h)
    msg = r["choices"][0].get("message") or {}
    return (msg.get("content") or ""), r


def call_anthropic(cfg, prompt):
    url = cfg.get("base_url", "https://api.anthropic.com/v1").rstrip("/") + "/messages"
    body = {
        "model": cfg["model"],
        "messages": [{"role": "user", "content": prompt}],
        "temperature": TEMPERATURE,
        "max_tokens": cfg.get("max_tokens", MAX_TOKENS),
    }
    h = {"Content-Type": "application/json",
         "anthropic-version": "2023-06-01",
         "x-api-key": os.environ[cfg["api_key_env"]]}
    r = _post(url, body, h)
    text = "".join(b.get("text", "") for b in r.get("content", []) if b.get("type") == "text")
    return text, r


def call_gemini(cfg, prompt):
    base = cfg.get("base_url", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
    url = "{}/models/{}:generateContent?key={}".format(
        base, cfg["model"], os.environ[cfg["api_key_env"]])
    gen = {"temperature": TEMPERATURE,
           "maxOutputTokens": cfg.get("max_tokens", MAX_TOKENS)}
    gen.update(cfg.get("gen_extra", {}))
    body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": gen}
    r = _post(url, body, {"Content-Type": "application/json"})
    cands = r.get("candidates") or [{}]
    parts = (cands[0].get("content") or {}).get("parts") or []
    # skip parts flagged as thoughts
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    return text, r


def param_sig(m):
    """Every setting that changes what the model does, as one comparable string."""
    return json.dumps({"max_tokens": m.get("max_tokens", MAX_TOKENS),
                       "omit_temperature": bool(m.get("omit_temperature")),
                       "extra_body": m.get("extra_body", {}),
                       "gen_extra": m.get("gen_extra", {})},
                      sort_keys=True, ensure_ascii=False)


def meta_of(provider, raw):
    """finish reason and token usage, for the log — this is how truncation is detected."""
    try:
        if provider == "openai":
            ch = raw["choices"][0]
            u = raw.get("usage") or {}
            det = u.get("completion_tokens_details") or {}
            return {"finish_reason": ch.get("finish_reason"),
                    "completion_tokens": u.get("completion_tokens"),
                    "reasoning_tokens": det.get("reasoning_tokens"),
                    "message_keys": ",".join((ch.get("message") or {}).keys())}
        if provider == "anthropic":
            u = raw.get("usage") or {}
            return {"finish_reason": raw.get("stop_reason"),
                    "completion_tokens": u.get("output_tokens"),
                    "reasoning_tokens": None, "message_keys": ""}
        if provider == "google":
            c = (raw.get("candidates") or [{}])[0]
            u = raw.get("usageMetadata") or {}
            return {"finish_reason": c.get("finishReason"),
                    "completion_tokens": u.get("candidatesTokenCount"),
                    "reasoning_tokens": u.get("thoughtsTokenCount"),
                    "message_keys": ""}
    except Exception:                                   # noqa: BLE001
        pass
    return {"finish_reason": None, "completion_tokens": None,
            "reasoning_tokens": None, "message_keys": ""}


TRUNCATED = {"length", "max_tokens", "MAX_TOKENS"}


PROVIDERS = {
    "openai": call_openai_compatible,
    "anthropic": call_anthropic,
    "google": call_gemini,
}


def call_with_retry(cfg, prompt):
    last = None
    for attempt in range(RETRIES):
        try:
            return PROVIDERS[cfg["provider"]](cfg, prompt)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:2000]
            last = "HTTP {} {}".format(e.code, body)
            if e.code == 429:
                time.sleep(min(120, 15 * 2 ** attempt))      # 15, 30, 60, 120 ...
                continue
            if e.code in (500, 502, 503, 529):
                time.sleep(2 ** attempt * 3)
                continue
            break
        except Exception as e:                       # noqa: BLE001
            last = "{}: {}".format(type(e).__name__, e)
            time.sleep(2 ** attempt * 2)
    raise RuntimeError(last)


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--items", default="items.json")
    ap.add_argument("--models", default="models.json")
    ap.add_argument("--outdir", default=".")
    ap.add_argument("--limit", type=int, default=0, help="run only the first N items (smoke test)")
    ap.add_argument("--workers", type=int, default=1,
                    help="parallel calls; 6 is a sensible start for the full run")
    ap.add_argument("--only", default="", help="run only these model codes, e.g. M2 or M1,M2")
    ap.add_argument("--seed", type=int, default=20260930, help="shuffle seed for the blind file")
    a = ap.parse_args()

    items = json.load(open(a.items, encoding="utf-8"))
    models = json.load(open(a.models, encoding="utf-8"))
    if a.limit:
        items = items[: a.limit]
    all_models = list(models)
    if a.only:
        keep = set(a.only.split(","))
        models = [m for m in models if m["code"] in keep]

    for m in models:
        if m["api_key_env"] not in os.environ:
            sys.exit("Missing environment variable: " + m["api_key_env"])

    os.makedirs(a.outdir, exist_ok=True)
    log_path = os.path.join(a.outdir, "runs_log.jsonl")
    done = {}
    mismatched = {}
    cfg_by_code = {m["code"]: m for m in all_models}
    if os.path.exists(log_path):
        for line in open(log_path, encoding="utf-8"):
            try:
                r = json.loads(line)
                if not r.get("error"):          # failures are retried on re-run
                    cur = cfg_by_code.get(r["model_code"])
                    old = r.get("params")
                    if cur is not None and old is not None and old != param_sig(cur):
                        mismatched[r["model_code"]] = mismatched.get(r["model_code"], 0) + 1
                        continue                # made under other settings: re-run, never mix
                    done[(r["item_id"], r["model_code"], r["task"])] = r
            except json.JSONDecodeError:
                pass
        print("resuming: {} calls already logged".format(len(done)))
        for code, n in mismatched.items():
            print("WARNING: {} logged {} call(s) were made with DIFFERENT settings than models.json "
                  "now has. They are ignored and will be re-run, so one model never mixes "
                  "two conditions.".format(code, n))

    started = datetime.datetime.now(datetime.timezone.utc).isoformat()
    logf = open(log_path, "a", encoding="utf-8")
    lock = threading.Lock()

    def now():
        return datetime.datetime.now(datetime.timezone.utc).isoformat()

    def run_one(it, m, task):
        """One API call. Never raises: failures come back as a record with 'error' set."""
        prompt = TASKS[task].format(text=it["error_text"])
        base = {
            "item_id": it["item_id"], "variety": it["variety"],
            "model_code": m["code"], "model_real": m["model"],
            "provider": m["provider"], "task": task,
            "temperature": "default" if m.get("omit_temperature") else TEMPERATURE,
            "error_text": it["error_text"], "seeded_count": it["seeded_count"],
            "params": param_sig(m),
        }
        try:
            text, raw = call_with_retry(m, prompt)
            mt = meta_of(m["provider"], raw)
            text = (text or "").strip()
            try:                                     # keep the model's reasoning text for diagnosis
                rc = raw["choices"][0]["message"].get("reasoning_content") or ""
            except Exception:                        # noqa: BLE001
                rc = ""
            stripped = False
            if "</think>" in text:                   # drop visible chain-of-thought
                text = text.split("</think>")[-1].strip()
                stripped = True
            if not text:
                base["debug"] = (rc[:600] + " ...[snip]... " + rc[-600:]) if len(rc) > 1300 else rc
                raise RuntimeError(
                    "EMPTY response: finish={} completion_tokens={} reasoning_tokens={} "
                    "message_keys=[{}] — raise max_tokens or lower reasoning".format(
                        mt["finish_reason"], mt["completion_tokens"],
                        mt["reasoning_tokens"], mt["message_keys"]))
            if mt["finish_reason"] in TRUNCATED:
                base["debug"] = (text[:600] + " ...[snip]... " + text[-600:]) if len(text) > 1300 else text
                raise RuntimeError(
                    "TRUNCATED response: finish={} completion_tokens={} reasoning_tokens={} "
                    "— raise max_tokens".format(
                        mt["finish_reason"], mt["completion_tokens"], mt["reasoning_tokens"]))
            base.update({
                "prompt_sha1": hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:12],
                "response": text, "finish_reason": mt["finish_reason"],
                "completion_tokens": mt["completion_tokens"],
                "reasoning_tokens": mt["reasoning_tokens"],
                "think_stripped": stripped, "run_utc": now(), "error": "",
            })
        except Exception as e:                        # noqa: BLE001
            base.update({
                "prompt_sha1": "", "response": "", "finish_reason": "",
                "completion_tokens": "", "reasoning_tokens": "", "think_stripped": "",
                "run_utc": now(), "error": str(e)[:700],
            })
        return base

    jobs = []
    for it in items:
        for m in models:
            for task in TASKS:
                if (it["item_id"], m["code"], task) not in done:
                    jobs.append((it, m, task))
    total = len(jobs)
    print("{} calls to make, {} worker(s)".format(total, a.workers))
    counter = {"n": 0, "fail": 0}
    failed_recs = {}

    def finish(rec):
        with lock:
            counter["n"] += 1
            if rec["error"]:
                counter["fail"] += 1
                line = "FAILED: " + rec["error"][:600]
            else:
                line = "ok  ({} chars, finish={}, reasoning_tokens={})".format(
                    len(rec["response"]), rec["finish_reason"], rec["reasoning_tokens"])
            print("[{}/{}] {} {} {}  {}".format(
                counter["n"], total, rec["item_id"], rec["model_code"], rec["task"], line),
                flush=True)
            logf.write(json.dumps(rec, ensure_ascii=False) + "\n")
            logf.flush()
            key = (rec["item_id"], rec["model_code"], rec["task"])
            if rec["error"]:
                failed_recs[key] = rec
            else:
                done[key] = rec
                failed_recs.pop(key, None)

    if a.workers <= 1:
        for it, m, task in jobs:
            finish(run_one(it, m, task))
            time.sleep(PAUSE)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=a.workers) as ex:
            futs = [ex.submit(run_one, it, m, task) for it, m, task in jobs]
            try:
                for f in concurrent.futures.as_completed(futs):
                    finish(f.result())
            except KeyboardInterrupt:
                print("\ninterrupted — cancelling pending calls; completed ones are saved")
                for f in futs:
                    f.cancel()
                ex.shutdown(wait=False, cancel_futures=True)
                logf.close()
                sys.exit(1)

    logf.close()
    rows = list(done.values()) + [r for k, r in failed_recs.items() if k not in done]

    raw_cols = ["item_id", "variety", "model_code", "model_real", "provider", "task",
                "temperature", "prompt_sha1", "seeded_count", "error_text", "response",
                "finish_reason", "completion_tokens", "reasoning_tokens", "think_stripped",
                "params", "run_utc", "error", "debug"]
    with open(os.path.join(a.outdir, "runs_raw.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=raw_cols, extrasaction="ignore")
        w.writeheader()
        for r in sorted(rows, key=lambda r: (r["item_id"], r["model_code"], r["task"])):
            w.writerow(r)

    blind = [r for r in rows if not r["error"]]
    random.Random(a.seed).shuffle(blind)
    blind_cols = ["run_id", "item_id", "variety", "task", "model_code",
                  "error_text", "response", "seeded_count",
                  "caught", "false_pos", "valid_alt", "scorer", "notes"]
    with open(os.path.join(a.outdir, "runs_blind.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=blind_cols, extrasaction="ignore")
        w.writeheader()
        for i, r in enumerate(blind, 1):
            row = dict(r)
            row["run_id"] = "RUN-{:04d}".format(i)
            for c in ("caught", "false_pos", "valid_alt", "scorer", "notes"):
                row[c] = ""
            w.writerow(row)

    with open(os.path.join(a.outdir, "model_key.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["model_code", "model_real", "provider", "temperature",
                    "max_tokens", "settings", "last_run_started_utc"])
        for m in all_models:
            w.writerow([m["code"], m["model"], m["provider"],
                        "default" if m.get("omit_temperature") else TEMPERATURE,
                        m.get("max_tokens", MAX_TOKENS), param_sig(m), started])

    with open(os.path.join(a.outdir, "prompts_used.txt"), "w", encoding="utf-8") as f:
        for t, p in TASKS.items():
            f.write("### " + t + "\n" + p + "\n\n")

    failed = counter["fail"]
    print("\nresponses saved: {}   failed this run: {}".format(len(done), failed))
    print("wrote runs_raw.csv, runs_blind.csv, model_key.csv, prompts_used.txt")
    if failed:
        print("re-run the same command to retry only the failed calls")
    print("\nGive scorers runs_blind.csv ONLY. Never model_key.csv or runs_raw.csv.")


if __name__ == "__main__":
    main()
