#!/usr/bin/env python3
"""
Compare two scored runs of the SAME items (e.g. provider-default reasoning vs reasoning minimal/off).

    python compare_conditions.py --a scores --b parity/scores --label-a "default reasoning" \
         --label-b "reasoning off" --out TamilEval_Parity_Comparison.xlsx \
         [--raw-a runs_log.jsonl --raw-b parity/runs_raw.csv]     # adds Sarvam repeatability

Because both runs used the same passages, the difference is estimated with a PAIRED bootstrap:
the same resampled items are used in both conditions, within each variety.
"""
import argparse, csv, json
import numpy as np, pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

NAMES = {"M1": "GPT-5", "M2": "Sarvam-105b", "M3": "Gemini-2.5-Flash"}
DIAL = ["Chennai", "Kongu", "Madurai"]
B = 4000
rng = np.random.default_rng(20261002)

# (label, file, numerator, denominator, scale, exclude_incomplete)
METRICS = [
    ("word-level recall (%)", "detect", "caught_word", "seeded_word"),
    ("un-seeded flags per 100 words", "detect", "unmatched_in_text", "words_in_passage"),
    ("unrequested edits (% of non-seeded words)", "correct", "unreq_edits", "nonseed"),
    ("exact repair (% of seeded errors)", "correct", "fixed_exact", "seeded"),
    ("seeded errors left unchanged (%)", "correct", "unchanged", "seeded"),
]


def load(d):
    det = pd.read_csv(f"{d}/detect_calls.csv", encoding="utf-8-sig").rename(columns={"flags": "nflags"})
    cor = pd.read_csv(f"{d}/correct_calls.csv", encoding="utf-8-sig")
    cor["nonseed"] = cor.tokens_in - cor.seeded
    cor.loc[cor.incomplete_output, "unreq_edits"] = np.nan
    return {"detect": det, "correct": cor}


def per_item(df, model, varieties, num, den):
    x = df[(df.model_code == model) & (df.variety.isin(varieties))]
    g = x.groupby("item_id").agg(n=(num, lambda s: s.sum(min_count=1)), d=(den, "sum"), v=("variety", "first"))
    return g


def paired(ga, gb, scale=100.0):
    """point estimates for A and B and a paired-bootstrap CI for B - A."""
    items = sorted(set(ga.index) & set(gb.index))
    ga, gb = ga.loc[items].copy(), gb.loc[items].copy()
    ok = ga.n.notna() & gb.n.notna()          # incomplete outputs dropped from BOTH conditions
    ga, gb = ga[ok], gb[ok]
    pa = scale * ga.n.sum() / ga.d.sum()
    pb = scale * gb.n.sum() / gb.d.sum()
    ta, tb = np.zeros(B), np.zeros(B)
    da, db = np.zeros(B), np.zeros(B)
    for v in ga.v.unique():
        ia = ga[ga.v == v]
        ib = gb.loc[ia.index]
        idx = rng.integers(0, len(ia), (B, len(ia)))
        ta += ia.n.values[idx].sum(1); da += ia.d.values[idx].sum(1)
        tb += ib.n.values[idx].sum(1); db += ib.d.values[idx].sum(1)
    diff = scale * tb / db - scale * ta / da
    lo, hi = np.percentile(diff, [2.5, 97.5])
    return pa, pb, pb - pa, lo, hi, len(ga)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True); ap.add_argument("--b", required=True)
    ap.add_argument("--label-a", default="condition A"); ap.add_argument("--label-b", default="condition B")
    ap.add_argument("--out", default="TamilEval_Parity_Comparison.xlsx")
    ap.add_argument("--raw-a", default=""); ap.add_argument("--raw-b", default="")
    ap.add_argument("--repeat-models", default="M2",
                    help="model codes whose settings were identical in both runs (repeatability check)")
    a = ap.parse_args()
    A, Bd = load(a.a), load(a.b)

    rows = []
    for m, nm in NAMES.items():
        for metric, kind, num, den in METRICS:
            for grp, vs in (("3 dialects pooled", DIAL), ("General", ["General"])):
                pa, pb, d, lo, hi, n = paired(per_item(A[kind], m, vs, num, den),
                                              per_item(Bd[kind], m, vs, num, den))
                sig = "yes" if (lo > 0 or hi < 0) else "no"
                rows.append([nm, metric, grp, round(pa, 1), round(pb, 1), round(d, 1),
                             "[%.1f – %.1f]" % (lo, hi), sig, n])
    hdr = ["model", "measure", "passages", a.label_a, a.label_b, "difference (B − A)",
           "95% CI of the difference (paired)", "CI excludes 0?", "items"]
    df = pd.DataFrame(rows, columns=hdr)

    wb = Workbook(); ws = wb.active; ws.title = "Conditions"
    ws.append(hdr)
    for r in rows: ws.append(r)
    for c in ws[1]:
        c.font = Font(name="Arial", bold=True, color="FFFFFF", size=10)
        c.fill = PatternFill("solid", fgColor="2F5496")
        c.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.font = Font(name="Arial", size=10)
    for i, w in enumerate([18, 42, 18, 18, 18, 16, 24, 12, 8], 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "C2"

    if a.raw_a and a.raw_b:
        def load_raw(path):
            out = {}
            if path.endswith(".jsonl"):
                for l in open(path, encoding="utf-8"):
                    if l.strip():
                        r = json.loads(l)
                        if not r["error"]:
                            out[(r["item_id"], r["model_code"], r["task"])] = r["response"]
            else:
                for r in csv.DictReader(open(path, encoding="utf-8-sig")):
                    if not r["error"]:
                        out[(r["item_id"], r["model_code"], r["task"])] = r["response"]
            return out
        ra, rb = load_raw(a.raw_a), load_raw(a.raw_b)
        ws2 = wb.create_sheet("Repeatability")
        ws2.append(["Same model, SAME settings, same prompt, run twice — how often does the answer repeat? "
                    "(only models whose settings were identical in both runs)"])
        ws2.append([])
        ws2.append(["model", "task", "responses compared", "byte-identical", "% identical",
                    "seeded errors compared", "same outcome in both runs", "% same outcome"])
        soa = pd.read_csv(f"{a.a}/seed_outcomes.csv", encoding="utf-8-sig")
        sob = pd.read_csv(f"{a.b}/seed_outcomes.csv", encoding="utf-8-sig")
        for m in a.repeat_models.split(","):
            nm = NAMES[m]
            for t in ("correct", "detect"):
                keys = [k for k in ra if k[1] == m and k[2] == t and k in rb]
                same = sum(1 for k in keys if ra[k].strip() == rb[k].strip())
                x = soa[(soa.model_code == m) & (soa.task == t)][["item_id", "code", "wrong", "outcome"]]
                y = sob[(sob.model_code == m) & (sob.task == t)][["item_id", "code", "wrong", "outcome"]]
                j = x.merge(y, on=["item_id", "code", "wrong"], suffixes=("_a", "_b"))
                agree = int((j.outcome_a == j.outcome_b).sum())
                ws2.append([nm, t, len(keys), same, round(100 * same / max(len(keys), 1), 1),
                            len(j), agree, round(100 * agree / max(len(j), 1), 1)])
        for c in ws2[3]:
            c.font = Font(name="Arial", bold=True, size=10)
        for i, w in enumerate([18, 10, 18, 16, 12, 22, 24, 16], 1):
            ws2.column_dimensions[get_column_letter(i)].width = w

    wb.save(a.out)
    pd.set_option("display.width", 220); pd.set_option("display.max_colwidth", 44)
    print(df.to_string(index=False))
    print("\nwrote", a.out)


if __name__ == "__main__":
    main()
