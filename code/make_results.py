#!/usr/bin/env python3
"""
Build the results workbook from the scorer's output.

    python make_results.py --scores scores --sarvam-low sarvam_effort_low_log.jsonl \
                           --raw runs_raw.csv --out TamilEval_Results.xlsx [--provisional "note"]

Confidence intervals are 95% percentile bootstrap intervals that resample ITEMS
(passages), within each variety, because the errors inside one passage are not independent.
"""
import argparse, csv, json
import numpy as np, pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

NAMES = {"M1": "GPT-5", "M2": "Sarvam-105b", "M3": "Gemini-2.5-Flash"}
VAR = ["Chennai", "Kongu", "Madurai", "General"]
DIAL = ["Chennai", "Kongu", "Madurai"]
B = 4000
rng = np.random.default_rng(20260930)


def boot(df, num, den, scale=100.0):
    """ratio of sums, bootstrap over items within each variety (vectorised)."""
    g = df.groupby("item_id").agg(n=(num, "sum"), d=(den, "sum"), v=("variety", "first"))
    if df[num].isna().any():
        g["n"] = df.groupby("item_id")[num].sum(min_count=1)
        g = g.dropna(subset=["n"])
    if g.d.sum() == 0 or len(g) == 0:
        return (np.nan, np.nan, np.nan, 0, 0)
    point = scale * g.n.sum() / g.d.sum()
    tn = np.zeros(B)
    td = np.zeros(B)
    for _, gg in g.groupby("v"):
        idx = rng.integers(0, len(gg), (B, len(gg)))
        tn += gg.n.values[idx].sum(axis=1)
        td += gg.d.values[idx].sum(axis=1)
    reps = scale * tn / td
    lo, hi = np.percentile(reps, [2.5, 97.5])
    return (point, lo, hi, g.n.sum(), g.d.sum())


def style_sheet(ws, widths, header_row=1):
    thin = Side(style="thin", color="BFBFBF")
    for c in ws[header_row]:
        c.font = Font(name="Arial", bold=True, color="FFFFFF", size=10)
        c.fill = PatternFill("solid", fgColor="2F5496")
        c.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
    for row in ws.iter_rows(min_row=header_row + 1):
        for c in row:
            c.font = Font(name="Arial", size=10)
            c.alignment = Alignment(wrap_text=True, vertical="top")
            c.border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = ws.cell(row=header_row + 1, column=2)


def put(ws, rows, header):
    ws.append(header)
    for r in rows:
        ws.append(r)


def ci(t, dp=1):
    if np.isnan(t[0]):
        return "n/a"
    return "%.*f  [%.*f – %.*f]" % (dp, t[0], dp, t[1], dp, t[2])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scores", default="scores")
    ap.add_argument("--raw", default="runs_raw.csv")
    ap.add_argument("--sarvam-low", default="")
    ap.add_argument("--out", default="TamilEval_Results.xlsx")
    ap.add_argument("--provisional", default="")
    ap.add_argument("--note3", default="", help="replaces the caveat about reasoning settings")
    a = ap.parse_args()

    det = pd.read_csv(f"{a.scores}/detect_calls.csv", encoding="utf-8-sig").rename(columns={"flags": "nflags"})
    cor = pd.read_csv(f"{a.scores}/correct_calls.csv", encoding="utf-8-sig")
    so = pd.read_csv(f"{a.scores}/seed_outcomes.csv", encoding="utf-8-sig")
    cor["nonseed"] = cor.tokens_in - cor.seeded
    cor.loc[cor.incomplete_output, "unreq_edits"] = np.nan
    n_items = det.item_id.nunique()

    wb = Workbook()

    # ---------------- README
    ws = wb.active
    ws.title = "ReadMe"
    lines = []
    if a.provisional:
        lines.append(("PROVISIONAL — " + a.provisional, True))
    lines += [
        ("Tamil AI evaluation — benchmark v0.1 results (%d items, 3 models, 2 tasks)" % n_items, True),
        ("", False),
        ("Two tasks, run on the same error-seeded passages:", True),
        ("  detect  — the model lists the wrong words as 'wrong | correct'.", False),
        ("  correct — the model rewrites the passage, told to fix grammar errors but keep dialect forms.", False),
        ("", False),
        ("Measures", True),
        ("  recall (word-level)   share of seeded errors whose word the model flagged. Punctuation errors (ந1) are reported "
         "separately: the detect prompt asks for wrong WORDS, so it cannot elicit a missing comma.", False),
        ("  un-seeded flags /100 words   words the model flagged that were NOT seeded errors. These are CANDIDATE false "
         "positives. Until native writers adjudicate them, some may be genuine errors the writers missed.", False),
        ("  exact repair          the model's word equals the writers' reference word. A strict LOWER bound: valid repairs in "
         "another form (e.g. a different verb ending) are not counted.", False),
        ("  completed             broken sentence (ச1) finished with any verb — there is no single correct verb.", False),
        ("  changed_other         the model changed the error but to a form other than ours. Needs human review: it can be a "
         "valid repair, a repair that flattened the dialect, or a miss.", False),
        ("  unrequested edits     share of NON-seeded words the model altered in the correct task although told to keep dialect forms.", False),
        ("", False),
        ("Caveats that belong in the paper", True),
        ("  1. Seeded errors and reference forms were verified by the writers of each variety and cross-checked for spelling by one "
         "reviewer. This is not independent expert validation of every error.", False),
        ("  2. Passages differ across varieties (different writers, topics, length). 'Dialect vs General' is a comparison of "
         "passage sets, not a controlled minimal-pair design.", False),
        ("  3. " + (a.note3 or "Sarvam-105b could not answer with reasoning on (see sheet Sarvam_reasoning_low); it was run with "
         "reasoning off. GPT-5 and Gemini ran with provider-default reasoning. A reasoning-off run of all three is the "
         "parity check."), False),
        ("  4. GPT-5 cannot be set to temperature 0; Sarvam's output is not bit-reproducible even at 0.", False),
        ("  5. Confidence intervals: 95% bootstrap over items (passages), within variety.", False),
    ]
    for t, bold in lines:
        ws.append([t])
        ws.cell(row=ws.max_row, column=1).font = Font(name="Arial", size=10, bold=bold)
        ws.cell(row=ws.max_row, column=1).alignment = Alignment(wrap_text=True, vertical="top")
    ws.column_dimensions["A"].width = 130

    # ---------------- Detect
    ws = wb.create_sheet("Detect")
    rows = []
    for m, nm in NAMES.items():
        r1, r2, r3 = [nm, "recall, word-level (%)"], [nm, "un-seeded flags per 100 words"], [nm, "all flags per 100 words"]
        for v in VAR + ["3 dialects pooled"]:
            sel = det[(det.model_code == m) & ((det.variety == v) if v in VAR else det.variety.isin(DIAL))]
            r1.append(ci(boot(sel, "caught_word", "seeded_word")))
            r2.append(ci(boot(sel, "unmatched_in_text", "words_in_passage")))
            r3.append(ci(boot(sel, "nflags", "words_in_passage")))
        rows += [r1, r2, r3]
    put(ws, rows, ["model", "measure"] + VAR + ["3 dialects pooled"])
    style_sheet(ws, [20, 30, 24, 24, 24, 24, 26])
    ws.append([])
    ws.append(["How the model lists words (whole run)"])
    ws.cell(row=ws.max_row, column=1).font = Font(name="Arial", bold=True, size=10)
    g = det.groupby("model_code").agg(
        seeded=("seeded", "sum"), caught=("caught", "sum"), listed_no_change=("listed_no_change", "sum"),
        flags=("nflags", "sum"), matched=("matched_flags", "sum"), unm_in=("unmatched_in_text", "sum"),
        unm_not=("unmatched_not_in_text", "sum"), noop=("noop_pairs", "sum"),
        header=("header_echo", "sum"), unparsed=("unparsed_lines", "sum"))
    ws.append(["model", "seeded errors", "located", "listed but left unchanged (x | x)", "flags (non-identical pairs)",
               "flags that hit a seeded error", "un-seeded flags, word in text", "flags, word NOT in text (garbled)",
               "identical-side pairs (ignored)", "header lines echoed", "lines not parseable"])
    hdr = ws.max_row
    for c in ws[hdr]:
        c.font = Font(name="Arial", bold=True, size=10)
        c.alignment = Alignment(wrap_text=True)
    for m, nm in NAMES.items():
        x = g.loc[m]
        ws.append([nm] + [int(x[k]) for k in ["seeded", "caught", "listed_no_change", "flags", "matched", "unm_in",
                                              "unm_not", "noop", "header", "unparsed"]])

    # ---------------- Correct
    ws = wb.create_sheet("Correct")
    c = so[so.task == "correct"]
    rows = []
    for m, nm in NAMES.items():
        for v in VAR + ["3 dialects pooled"]:
            x = c[(c.model_code == m) & ((c.variety == v) if v in VAR else c.variety.isin(DIAL))]
            n = len(x)
            cnt = x.outcome.value_counts()
            pc = lambda k: round(100 * cnt.get(k, 0) / n, 1)
            sel = cor[(cor.model_code == m) & ((cor.variety == v) if v in VAR else cor.variety.isin(DIAL))]
            rows.append([nm, v, n, pc("fixed_exact"), pc("completed"), pc("changed_other"), pc("unchanged"),
                         pc("omitted"), ci(boot(sel, "unreq_edits", "nonseed"))])
    put(ws, rows, ["model", "variety", "seeded errors", "exact repair %", "sentence completed %",
                   "changed, other form %", "left unchanged %", "omitted %",
                   "unrequested edits, % of non-seeded words  [95% CI]"])
    style_sheet(ws, [20, 18, 12, 12, 14, 16, 14, 11, 30])
    inc = cor[cor.incomplete_output]
    ws.append([])
    ws.append(["Incomplete outputs (model returned < 80%% of the passage; excluded from the edit rate): %d  %s" % (
        len(inc), dict(inc.model_code.map(NAMES).value_counts()))])

    # ---------------- By error type
    ws = wb.create_sheet("ByErrorType")
    LABEL = {"ப1": "ப1 gender", "த1": "த1 animacy", "க1": "க1 tense", "எ1": "எ1 number", "ந1": "ந1 punctuation",
             "இ1": "இ1 person", "ஒ1": "ஒ1 missing ஒற்று", "ச1": "ச1 broken sentence", "வ1": "வ1 case suffix",
             "ம1": "ம1 sandhi", "ஒ2": "ஒ2 extra ஒற்று"}
    d = so[so.task == "detect"]
    rows = []
    for code, lab in LABEL.items():
        n = int(((d.model_code == "M1") & (d.code == code)).sum())
        r = [lab, n]
        for m in NAMES:
            x = d[(d.model_code == m) & (d.code == code)]
            r.append("%d/%d = %d%%" % ((x.outcome == "caught").sum(), len(x), round(100 * (x.outcome == "caught").mean())))
        for m in NAMES:
            x = c[(c.model_code == m) & (c.code == code)]
            k = x.outcome.isin(["fixed_exact", "completed"]).sum()
            t = (x.outcome != "unchanged").sum()
            r.append("%d/%d = %d%%  (changed: %d%%)" % (k, len(x), round(100 * k / len(x)), round(100 * t / len(x))))
        rows.append(r)
    put(ws, rows, ["error type", "n"] + ["DETECT located — " + v for v in NAMES.values()]
        + ["CORRECT exact/completed — " + v for v in NAMES.values()])
    style_sheet(ws, [22, 6, 18, 18, 20, 30, 30, 30])
    ws.append([])
    ws.append(["ந1: the detect prompt asks for wrong WORDS, so a missing comma is outside what it elicits (0 located by GPT-5 and Gemini)."])
    ws.append(["Small groups (ம1 n=4, ஒ2 n=3) are shown for completeness only."])

    # ---------------- Sarvam with reasoning on
    if a.sarvam_low:
        recs = [json.loads(l) for l in open(a.sarvam_low, encoding="utf-8") if l.strip()]
        last = {}
        for r in recs:
            last[(r["item_id"], r["task"])] = r
        rs = list(last.values())
        ws = wb.create_sheet("Sarvam_reasoning_low")
        ws.append(["Sarvam-105b with reasoning_effort='low' and a 12,000-token ceiling: calls that returned NO answer"])
        ws.cell(row=1, column=1).font = Font(name="Arial", bold=True, size=10)
        ws.append([])
        ws.append(["group", "calls", "no answer", "% no answer"])
        hdr = ws.max_row
        for cc in ws[hdr]:
            cc.font = Font(name="Arial", bold=True, size=10)
        def add(label, sel):
            n = len(sel); f = sum(1 for r in sel if r["error"])
            ws.append([label, n, f, round(100 * f / n, 1) if n else ""])
        add("all calls", rs)
        for t in ("correct", "detect"):
            add("task: " + t, [r for r in rs if r["task"] == t])
        for v in VAR:
            add("variety: " + v, [r for r in rs if r["variety"] == v])
        ws.column_dimensions["A"].width = 30
        ws.append([])
        ws.append(["Failure kinds: " + str(dict(pd.Series(["timeout" if "Timeout" in r["error"] else
                   ("empty, hit token ceiling" if "finish=length" in r["error"] else
                    ("empty, stopped" if "EMPTY" in r["error"] else "other")) for r in rs if r["error"]]).value_counts()))])
        ws.append(["Reasoning is conducted in English about Tamil forms and does not terminate; calls that did answer are a "
                   "self-selected, easier subset. These data are therefore not used for the model comparison."])

    # ---------------- Format / cost
    ws = wb.create_sheet("Format_and_cost")
    raw = pd.read_csv(a.raw, encoding="utf-8-sig")
    raw["completion_tokens"] = pd.to_numeric(raw.completion_tokens, errors="coerce")
    raw["reasoning_tokens"] = pd.to_numeric(raw.reasoning_tokens, errors="coerce")
    rows = []
    for m, nm in NAMES.items():
        for t in ("correct", "detect"):
            x = raw[(raw.model_code == m) & (raw.task == t)]
            rows.append([nm, t, len(x), int(x.completion_tokens.mean()) if x.completion_tokens.notna().any() else "",
                         int(x.reasoning_tokens.mean()) if x.reasoning_tokens.notna().any() else "n/a"])
    put(ws, rows, ["model", "task", "responses", "mean completion tokens", "mean reasoning tokens"])
    style_sheet(ws, [20, 10, 12, 20, 20])
    ws.append([])
    ws.append(["GPT-5 'completion tokens' INCLUDE its reasoning tokens; Gemini's figure EXCLUDES its thinking tokens "
               "(shown separately). Do not compare the two columns across providers."])

    wb.save(a.out)
    print("wrote", a.out)


if __name__ == "__main__":
    main()
