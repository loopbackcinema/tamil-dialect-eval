#!/usr/bin/env python3
"""
Automatic scorer for the Tamil AI evaluation benchmark.

    python score_runs.py --raw runs_raw.csv --items items.json --outdir scores

Reads the model responses and the seeded-error ground truth and writes:

  detect_calls.csv      one row per (item, model): seeded / caught / flags / unmatched ...
  correct_calls.csv     one row per (item, model): fixed / changed / unchanged / unrequested edits
  seed_outcomes.csv     one row per (seeded error, model, task): what happened to that error
  unmatched_flags.csv   words a model flagged that are NOT seeded errors  -> for human adjudication
  score_checks.txt      sanity checks on the ground-truth alignment

Nothing here is a judgement call about Tamil grammar. The scorer only asks
"did the model touch the place where we seeded an error, and did it land on the
reference form?"  Whether a flagged, un-seeded word is a dialect form or a real
miss by the writers is exactly what the adjudication file is for.
"""
import argparse, csv, difflib, json, os, re, sys, unicodedata
from collections import Counter, defaultdict

QUOTES = '“”‘’"\'`«»‹›'
HEADER_MARK = "பிழையான சொல்"


# ----------------------------------------------------------------- text helpers
def nfc(s):
    return unicodedata.normalize("NFC", (s or "").replace("\u200b", "").replace("\ufeff", ""))


def key(tok):
    """A token reduced to its letters: all punctuation, quotes and symbols removed."""
    t = nfc(tok)
    return "".join(ch for ch in t if unicodedata.category(ch)[0] not in "PS" and ch not in QUOTES)


def tokenize(text):
    """Whitespace tokens with character spans; tokens with no letters are dropped."""
    out = []
    for m in re.finditer(r"\S+", nfc(text)):
        k = key(m.group(0))
        if k:
            out.append({"raw": m.group(0), "key": k, "start": m.start(), "end": m.end()})
    return out


def strip_edges(s):
    s = nfc(s).strip()
    s = re.sub(r"^[\s*_`\"'“”‘’]+|[\s*_`\"'“”‘’]+$", "", s)
    return s.strip()


# ----------------------------------------------------------------- seeded errors
def punct_chars(s):
    return {ch for ch in s if unicodedata.category(ch)[0] == "P" and ch not in QUOTES}


def punct_chars_list(s):
    return [ch for ch in s if unicodedata.category(ch)[0] == "P" and ch not in QUOTES]


def seed_info(error_text, clean_text, seed):
    """Locate one seeded error inside the item text.

    Returns the error's 'loci' (the tokens of error_text it sits on), whether it is a
    punctuation-only error, and the reference tokens.  Everything is derived from the
    writers' wrong/correct strings plus a token diff; no Tamil grammar is assumed.
    """
    wrong, correct = nfc(seed["wrong"]), nfc(seed["correct"])
    E = tokenize(error_text)
    pos = nfc(error_text).find(wrong)
    if pos < 0:
        return None
    W, Cc = wrong.split(), correct.split()
    offs, cur = [], 0
    for w in W:
        k = wrong.index(w, cur)
        offs.append(k)
        cur = k + len(w)
    sm = difflib.SequenceMatcher(None, W, Cc, autojunk=False)
    changed, fix_raw, punct_only, need_punct = [], [], True, set()
    any_op = False
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        any_op = True
        if tag == "insert":
            changed.append(max(i1 - 1, 0))
            punct_only = False
        else:
            changed.extend(range(i1, i2))
            if tag == "replace" and (i2 - i1) == (j2 - j1) and all(
                    key(W[i1 + k]) == key(Cc[j1 + k]) for k in range(i2 - i1)):
                for k in range(i2 - i1):
                    need_punct |= punct_chars(Cc[j1 + k]) - punct_chars(W[i1 + k])
            else:
                punct_only = False
        fix_raw += Cc[j1:j2]
    if not any_op:
        return None
    loci = []
    for wi in sorted(set(changed)):
        a, b = pos + offs[wi], pos + offs[wi] + len(W[wi])
        piece = key(W[wi])
        for ti, t in enumerate(E):
            if t["start"] < b and t["end"] > a and piece:
                loci.append({"etok": ti, "etok_key": t["key"], "piece": piece,
                             "partial": t["key"] != piece})
    if not loci:
        return None
    seen, uniq = set(), []
    for l in loci:
        if l["etok"] not in seen:
            seen.add(l["etok"])
            uniq.append(l)
    fix_keys = [key(t) for t in fix_raw if key(t)]
    occ = sum(1 for t in E if any(t["key"] == l["etok_key"] for l in uniq))
    return {"code": seed["code"], "wrong": seed["wrong"], "correct": seed["correct"],
            "loci": uniq, "punct_only": punct_only and bool(need_punct),
            "need_punct": need_punct, "fix_keys": fix_keys,
            "ambiguous": occ > len(uniq)}


# ----------------------------------------------------------------- DETECT task
PAREN = re.compile(r"\s*[\(（][^)）]*[\)）]")


def parse_pairs(resp):
    """Turn a model's detect answer into (left, right) pairs.

    Returns pairs, number of header echoes, number of lines that were not pairs.
    Accepts  a | b   and   a -> b   and strips bullets / numbering / quotes.
    """
    pairs, headers, unparsed = [], 0, 0
    for line in (resp or "").splitlines():
        s = nfc(line).strip()
        if not s:
            continue
        s = re.sub(r"^\s*(?:[-*•]+|\d+[\.\)])\s*", "", s)
        if "|" in s:
            parts = s.split("|")
            a, b = parts[0], parts[1] if len(parts) > 1 else ""
        elif re.search(r"→|->|=>", s):
            a, b = re.split(r"→|->|=>", s, maxsplit=1)
        else:
            unparsed += 1
            continue
        # a model sometimes appends a remark in brackets, e.g. "x | x (no error)": drop the remark first
        a, b = strip_edges(PAREN.sub("", a)), strip_edges(PAREN.sub("", b))
        if a and not b:
            b = a                      # a bare remark such as "(no error)" means: nothing to change
        if HEADER_MARK in a:
            headers += 1
            continue
        if not a or not b:
            unparsed += 1
            continue
        pairs.append((a, b))
    seen, uniq = set(), []
    for p in pairs:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq, headers, unparsed


def is_noop(a, b):
    return strip_edges(a) == strip_edges(b)


def left_hits_locus(left_keys, loc):
    for lk in left_keys:
        if lk == loc["etok_key"]:
            return True
        if loc["partial"] and loc["piece"] in lk and lk in loc["etok_key"]:
            return True
    return False


def pair_matches(seed, left, right):
    lk = [key(t) for t in left.split() if key(t)]
    if not any(left_hits_locus(lk, loc) for loc in seed["loci"]):
        return False, False
    if seed["punct_only"]:
        # the model located the missing punctuation; which mark it proposes does not matter
        ok = len(punct_chars_list(right)) > len(punct_chars_list(left))
        return ok, ok
    rk = [key(t) for t in right.split() if key(t)]
    fixed = bool(seed["fix_keys"]) and all(any(fk in r for r in rk) for fk in seed["fix_keys"])
    return True, fixed


def score_detect(item, seeds, resp):
    pairs, headers, unparsed = parse_pairs(resp)
    flags = [(a, b) for a, b in pairs if not is_noop(a, b)]
    noops = [(a, b) for a, b in pairs if is_noop(a, b)]
    etext_key = key(item["error_text"])
    ekeys = {t["key"] for t in tokenize(item["error_text"])}
    seed_out, matched_flag_idx = [], set()
    for s in seeds:
        caught = fix = False
        for i, (a, b) in enumerate(flags):
            m, f = pair_matches(s, a, b)
            if m:
                caught = True
                fix = fix or f
                matched_flag_idx.add(i)
        listed_noop = False
        if not caught:
            for a, b in noops:
                lk = [key(t) for t in a.split() if key(t)]
                if any(left_hits_locus(lk, loc) for loc in s["loci"]):
                    listed_noop = True
        outcome = "caught" if caught else ("listed_no_change" if listed_noop else "missed")
        seed_out.append({"code": s["code"], "wrong": s["wrong"], "correct": s["correct"],
                         "outcome": outcome,
                         "fix_matches": (fix if caught and s["fix_keys"] or s["punct_only"] and caught else ""),
                         "ambiguous": s["ambiguous"]})
    unmatched = []
    for i, (a, b) in enumerate(flags):
        if i in matched_flag_idx:
            continue
        lk = [key(t) for t in a.split() if key(t)]
        in_text = bool(lk) and all(k in ekeys or k in etext_key for k in lk) and key(a) in etext_key
        unmatched.append({"left": a, "right": b, "in_text": in_text})
    return {"pairs": len(pairs), "flags": len(flags), "noops": len(noops),
            "headers": headers, "unparsed": unparsed,
            "matched_flags": len(matched_flag_idx), "unmatched": unmatched,
            "seed_out": seed_out}


# ----------------------------------------------------------------- CORRECT task
def pair_tokens(Es, Os, min_sim=0.45):
    """Monotone one-to-one pairing of words by spelling similarity (weighted LCS)."""
    n, m = len(Es), len(Os)
    sim = [[difflib.SequenceMatcher(None, Es[x], Os[y]).ratio() for y in range(m)] for x in range(n)]
    dp = [[0.0] * (m + 1) for _ in range(n + 1)]
    for x in range(1, n + 1):
        for y in range(1, m + 1):
            best = max(dp[x - 1][y], dp[x][y - 1])
            if sim[x - 1][y - 1] >= min_sim:
                best = max(best, dp[x - 1][y - 1] + sim[x - 1][y - 1])
            dp[x][y] = best
    x, y, pairs = n, m, []
    while x > 0 and y > 0:
        if sim[x - 1][y - 1] >= min_sim and abs(dp[x][y] - (dp[x - 1][y - 1] + sim[x - 1][y - 1])) < 1e-9:
            pairs.append((x - 1, y - 1)); x -= 1; y -= 1
        elif dp[x - 1][y] >= dp[x][y - 1]:
            x -= 1
        else:
            y -= 1
    return pairs[::-1]


def align(Ek, Ok):
    sm = difflib.SequenceMatcher(None, Ek, Ok, autojunk=False)
    emap, ins_after = {}, {}
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                emap[i1 + k] = ("eq", j1 + k)
        elif tag == "replace":
            pairs = pair_tokens(Ek[i1:i2], Ok[j1:j2]) if ((i2 - i1) > 1 or (j2 - j1) > 1) else []
            if not pairs:
                for i in range(i1, i2):
                    emap[i] = ("rep", (j1, j2))
            else:
                matched_e = {px for px, _ in pairs}
                matched_o = {py for _, py in pairs}
                for i in range(i1, i2):
                    emap[i] = ("rep", (j1 + dict(pairs)[i - i1], j1 + dict(pairs)[i - i1] + 1)) \
                        if (i - i1) in matched_e else ("del", (i, i + 1))
                prev_e, y = i1 - 1, 0
                pm = {py: px for px, py in pairs}
                while y < (j2 - j1):
                    if y in matched_o:
                        prev_e = i1 + pm[y]
                        y += 1
                    else:
                        y0 = y
                        while y < (j2 - j1) and y not in matched_o:
                            y += 1
                        ins_after[prev_e] = (j1 + y0, j1 + y)
        elif tag == "delete":
            for i in range(i1, i2):
                emap[i] = ("del", (i1, i2))
        elif tag == "insert":
            ins_after[i1 - 1] = (j1, j2)
    return emap, ins_after


def reference_ops(Ek, Ck):
    sm = difflib.SequenceMatcher(None, Ek, Ck, autojunk=False)
    return [op for op in sm.get_opcodes() if op[0] != "equal"]


def score_correct(item, seeds, resp):
    E, C, O = tokenize(item["error_text"]), tokenize(item["clean_text"]), tokenize(resp)
    Ek, Ck, Ok = [t["key"] for t in E], [t["key"] for t in C], [t["key"] for t in O]
    emap, ins_after = align(Ek, Ok)
    ops = reference_ops(Ek, Ck)
    incomplete = len(Ok) < 0.8 * len(Ek)
    locus_idx = set()
    seed_out = []
    for s in seeds:
        idxs = [l["etok"] for l in s["loci"]]
        locus_idx.update(idxs)
        op = None
        for o in ops:
            tag, i1, i2, j1, j2 = o
            if (tag != "insert" and any(i1 <= i < i2 for i in idxs)) or \
               (tag == "insert" and any(i == i1 - 1 for i in idxs)):
                op = o
                break
        outcome = "unlinked"
        model_form, reference_form = "", ""
        error_form = " ".join(E[i]["raw"] for i in idxs)
        if s["punct_only"]:
            st = emap.get(idxs[0])
            reference_form = "(adds " + "".join(sorted(s["need_punct"])) + ")"
            if st and st[0] == "eq":
                tokraw = O[st[1]]["raw"]
                model_form = tokraw
                more = len(punct_chars_list(tokraw)) > len(punct_chars_list(E[idxs[0]]["raw"]))
                outcome = "fixed_exact" if more else "unchanged"
            elif st and st[0] == "del" and (st[1][1] - st[1][0]) >= 5:
                outcome = "omitted"
            else:
                outcome = "changed_other"
        elif op is not None:
            tag, i1, i2, j1, j2 = op
            F = Ck[j1:j2]
            reference_form = " ".join(F)
            if tag == "insert":
                a = i1 - 1
                got = ins_after.get(a)
                if got:
                    model_form = " ".join(Ok[got[0]:got[1]])
                if got and Ok[got[0]:got[1]] == F:
                    outcome = "fixed_exact"
                elif got:
                    outcome = "completed" if s["code"] == "ச1" else "changed_other"
                else:
                    st, nx = emap.get(a), emap.get(a + 1)
                    if st and nx and st[0] == "eq" and nx[0] == "eq" and nx[1] == st[1] + 1:
                        outcome = "unchanged"
                    elif st and a + 1 >= len(Ek) and st[0] == "eq" and st[1] == len(Ok) - 1:
                        outcome = "unchanged"          # last word of the passage, nothing added after it
                    elif s["code"] == "ச1" and st and st[0] == "rep":
                        # broken sentence finished by turning its last word into a finite verb
                        otoks = Ok[st[1][0]:st[1][1]]
                        model_form = " ".join(otoks)
                        r_ = difflib.SequenceMatcher(None, Ek[a], "".join(otoks)).ratio()
                        outcome = "completed" if r_ < 0.85 else "changed_other"   # a mere respelling is not a fix
                    else:
                        outcome = "changed_other"
            else:
                # two seeded errors on neighbouring words form ONE reference op; when the op is a
                # one-to-one replacement, judge each seeded error on its own word only
                mine = sorted(i for i in idxs if i1 <= i < i2)
                if tag == "replace" and (i2 - i1) == (j2 - j1) and mine:
                    rng = mine
                    F = [Ck[j1 + (i - i1)] for i in mine]
                else:
                    rng = list(range(i1, i2))
                Lb = [Ek[i] for i in rng]
                mapped, seen_rep = [], set()
                big_del = False
                for i in rng:
                    st = emap.get(i)
                    if st is None:
                        continue
                    if st[0] == "eq":
                        mapped.append(Ok[st[1]])
                    elif st[0] == "rep":
                        if st[1] not in seen_rep:
                            seen_rep.add(st[1])
                            mapped.extend(Ok[st[1][0]:st[1][1]])
                    elif st[0] == "del":
                        if (st[1][1] - st[1][0]) >= 5:
                            big_del = True
                with_ins = list(mapped)
                for pos_, where in ((rng[0] - 1, "before"), (rng[-1], "after")):
                    g = ins_after.get(pos_)
                    if g:
                        seg = Ok[g[0]:g[1]]
                        with_ins = seg + with_ins if where == "before" else with_ins + seg
                model_form = " ".join(with_ins)
                reference_form = " ".join(F)
                if mapped == F or with_ins == F:
                    outcome = "fixed_exact"
                elif s["code"] == "ச1" and with_ins and with_ins != Lb and not big_del:
                    outcome = "completed"     # sentence finished: a verb other than ours, or the last word made finite
                elif big_del:
                    outcome = "omitted"
                elif mapped == Lb and with_ins == mapped:
                    outcome = "unchanged"
                else:
                    outcome = "changed_other"
        seed_out.append({"code": s["code"], "wrong": s["wrong"], "correct": s["correct"],
                         "outcome": outcome, "ambiguous": s["ambiguous"],
                         "error_form": error_form, "reference_form": reference_form,
                         "model_form": model_form,
                         "form_similarity": (round(difflib.SequenceMatcher(
                             None, "".join(reference_form.split()), "".join(model_form.split())).ratio(), 2)
                             if model_form and reference_form else "")})
    # edits the model made where nothing was seeded
    small = large = 0
    non_locus = [i for i in range(len(Ek)) if i not in locus_idx]
    for i in non_locus:
        st = emap.get(i)
        if not st or st[0] == "eq":
            continue
        if st[0] == "rep":
            o_join = "".join(Ok[st[1][0]:st[1][1]])
            r = difflib.SequenceMatcher(None, Ek[i], o_join).ratio()
            small, large = (small + 1, large) if r >= 0.6 else (small, large + 1)
        else:
            large += 1
    n_non = max(len(non_locus), 1)
    return {"tokens_in": len(Ek), "tokens_out": len(Ok), "incomplete": incomplete,
            "unreq_small": small, "unreq_large": large,
            "unreq_total": small + large, "unreq_rate": (small + large) / n_non,
            "seed_out": seed_out}


# ----------------------------------------------------------------- driver
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="runs_raw.csv")
    ap.add_argument("--items", default="items.json")
    ap.add_argument("--outdir", default="scores")
    ap.add_argument("--exclude", default="", help="comma-separated item ids to leave out")
    a = ap.parse_args()
    os.makedirs(a.outdir, exist_ok=True)

    items = {i["item_id"]: i for i in json.load(open(a.items, encoding="utf-8"))}
    raw = list(csv.DictReader(open(a.raw, encoding="utf-8-sig")))
    skip = {x.strip() for x in a.exclude.split(",") if x.strip()}
    if skip:
        raw = [r for r in raw if r["item_id"] not in skip]
        print("excluded items:", sorted(skip))

    seeds_by_item, checks = {}, []
    for iid, it in items.items():
        infos = [seed_info(it["error_text"], it["clean_text"], s) for s in it["seeded"]]
        bad = [s for s, x in zip(it["seeded"], infos) if x is None]
        for s in bad:
            checks.append("UNLOCATED seeded error  %s  %s | %s" % (iid, s["code"], s["wrong"]))
        seeds_by_item[iid] = [x for x in infos if x]
    n_seeds = sum(len(v) for v in seeds_by_item.values())
    checks.insert(0, "seeded errors located: %d of %d" % (n_seeds, sum(len(i["seeded"]) for i in items.values())))
    n_amb = sum(1 for v in seeds_by_item.values() for s in v if s["ambiguous"])
    checks.append("seeded errors whose erroneous word also occurs elsewhere in the passage (ambiguous): %d" % n_amb)
    # reference alignment: the words that differ between clean and error text must be exactly the
    # words the seeded errors sit on (runs of neighbouring words count once)
    mism = 0
    for iid, it in items.items():
        E, C = tokenize(it["error_text"]), tokenize(it["clean_text"])
        nops = len(reference_ops([t["key"] for t in E], [t["key"] for t in C]))
        union = sorted({l["etok"] for s in seeds_by_item[iid] if not s["punct_only"] for l in s["loci"]})
        runs = sum(1 for k, x in enumerate(union) if k == 0 or x != union[k - 1] + 1)
        if nops != runs:
            mism += 1
            checks.append("ALIGNMENT  %s: %d reference edits vs %d expected from seeds" % (iid, nops, runs))
    checks.append("items where reference edits != expected: %d" % mism)

    det_rows, cor_rows, seed_rows, unm_rows = [], [], [], []
    for r in raw:
        iid, m, task = r["item_id"], r["model_code"], r["task"]
        it, seeds = items[iid], seeds_by_item[iid]
        base = {"item_id": iid, "variety": r["variety"], "model_code": m,
                "model_real": r["model_real"], "seeded": len(seeds),
                "words_in_passage": len(tokenize(it["error_text"]))}
        if task == "detect":
            d = score_detect(it, seeds, r["response"])
            caught = sum(1 for s in d["seed_out"] if s["outcome"] == "caught")
            listed = sum(1 for s in d["seed_out"] if s["outcome"] == "listed_no_change")
            in_text = [u for u in d["unmatched"] if u["in_text"]]
            word_seeds = [x for x in d["seed_out"] if x["code"] != "ந1"]
            det_rows.append({**base, "caught": caught, "missed": len(seeds) - caught - listed,
                             "seeded_word": len(word_seeds),
                             "caught_word": sum(1 for x in word_seeds if x["outcome"] == "caught"),
                             "listed_no_change": listed,
                             "flags": d["flags"], "matched_flags": d["matched_flags"],
                             "unmatched_in_text": len(in_text),
                             "unmatched_not_in_text": len(d["unmatched"]) - len(in_text),
                             "noop_pairs": d["noops"], "header_echo": d["headers"],
                             "unparsed_lines": d["unparsed"]})
            for s in d["seed_out"]:
                seed_rows.append({**{k: base[k] for k in ("item_id", "variety", "model_code")},
                                  "task": "detect", **s})
            for u in d["unmatched"]:
                unm_rows.append({"item_id": iid, "variety": r["variety"], "model_code": m,
                                 "left": u["left"], "right": u["right"], "in_text": u["in_text"]})
        else:
            c = score_correct(it, seeds, r["response"])
            cnt = Counter(s["outcome"] for s in c["seed_out"])
            cor_rows.append({**base, "fixed_exact": cnt["fixed_exact"], "completed": cnt["completed"],
                             "changed_other": cnt["changed_other"], "unchanged": cnt["unchanged"],
                             "omitted": cnt["omitted"], "unlinked": cnt["unlinked"],
                             "tokens_in": c["tokens_in"], "tokens_out": c["tokens_out"],
                             "incomplete_output": c["incomplete"],
                             "unreq_edits": c["unreq_total"], "unreq_small": c["unreq_small"],
                             "unreq_large": c["unreq_large"], "unreq_rate": round(c["unreq_rate"], 4)})
            for s in c["seed_out"]:
                seed_rows.append({**{k: base[k] for k in ("item_id", "variety", "model_code")},
                                  "task": "correct", **s})

    def dump(name, rows):
        if not rows:
            return
        cols = []
        for r_ in rows:
            for k_ in r_:
                if k_ not in cols:
                    cols.append(k_)
        with open(os.path.join(a.outdir, name), "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=cols, restval="")
            w.writeheader()
            w.writerows(rows)

    dump("detect_calls.csv", det_rows)
    dump("correct_calls.csv", cor_rows)
    dump("seed_outcomes.csv", seed_rows)
    dump("unmatched_flags.csv", unm_rows)
    with open(os.path.join(a.outdir, "score_checks.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(checks) + "\n")
    print("\n".join(checks[:12]))
    print("wrote:", ", ".join(sorted(os.listdir(a.outdir))))


if __name__ == "__main__":
    main()
