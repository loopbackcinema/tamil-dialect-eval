#!/usr/bin/env python3
"""
Combine the native readers' judgements with the automatic scores.

    python apply_adjudication.py --labels adjudication_labels_all.csv --key PRIVATE_adjudication_key.csv \
        --scores scores --out TamilEval_Adjudicated.xlsx \
        --primary "Chennai=R1;Kongu=R2;Madurai=R2;General=R4"

For each variety it takes ONE primary rater's labels to complete the picture, and uses any other rater
who judged the same units to measure agreement (Cohen's kappa).

Part B labels   A  error resolved, the model's word is natural in the variety (dialect kept)
                B  error resolved, but the model's word is not the variety's (dialect flattened)
                C  error NOT resolved / new error / meaning changed
Part A labels   S  the flagged word is an acceptable form in this variety (the model's flag is a false positive)
                E  a genuine error that the writers missed
"""
import argparse
from collections import Counter
import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

NAMES = {"M1": "GPT-5", "M2": "Sarvam-105b", "M3": "Gemini-2.5-Flash"}


def kappa(a, b):
    n = len(a)
    if n == 0:
        return float("nan"), float("nan")
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / n / n
    return po, ((po - pe) / (1 - pe) if pe < 1 else float("nan"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--scores", required=True)
    ap.add_argument("--out", default="TamilEval_Adjudicated.xlsx")
    ap.add_argument("--primary", required=True, help='"Variety=rater;Variety=rater"')
    a = ap.parse_args()
    primary = dict(x.split("=", 1) for x in a.primary.split(";") if x.strip())

    lab = pd.read_csv(a.labels, encoding="utf-8-sig")
    key = pd.read_csv(a.key, encoding="utf-8-sig")
    so = pd.read_csv(f"{a.scores}/seed_outcomes.csv", encoding="utf-8-sig")
    so["model_form"] = so.model_form.fillna("")
    meaning = {"A": "resolved, dialect kept", "B": "resolved, dialect FLATTENED", "C": "NOT resolved / new error"}

    wb = Workbook()
    ws = wb.active
    ws.title = "Repairs_adjudicated"
    hdr = ["variety", "model", "seeded errors", "resolved, dialect kept (exact match + judged A)",
           "sentence completed (ச1)", "resolved, dialect FLATTENED (judged B)", "NOT resolved / new error (judged C)",
           "left unchanged", "omitted", "unjudged", "dialect-kept, STRICT (judged A counted as B)"]
    ws.append(hdr)
    printed = []
    for v, rater in primary.items():
        L = lab[(lab.variety == v) & (lab.part == "B") & (lab.rater == rater)]
        kb = key[(key.variety == v) & (key.part == "B")].copy()
        kb = kb.merge(L[["unit_id", "label"]], on="unit_id", how="left")
        kb[["code", "eform", "mform"]] = kb.unit_key.str.split("|", n=2, expand=True)
        kb["mform"] = kb.mform.fillna("")
        lookup = {(r.item_id, r.code, r.eform, r.mform): r.label for r in kb.itertuples()}
        ch = so[(so.task == "correct") & (so.variety == v)]
        n_seed = ch[ch.model_code == "M1"].shape[0]
        for m, nm in NAMES.items():
            x = ch[ch.model_code == m]
            c = Counter()
            for r in x.itertuples():
                if r.outcome == "fixed_exact":
                    c["kept"] += 1
                elif r.outcome == "completed":
                    c["completed"] += 1
                elif r.outcome == "unchanged":
                    c["unchanged"] += 1
                elif r.outcome == "omitted":
                    c["omitted"] += 1
                else:
                    l = lookup.get((r.item_id, r.code, r.error_form, r.model_form))
                    if l == "A":
                        c["kept"] += 1; c["judgedA"] += 1
                    elif l == "B":
                        c["flat"] += 1
                    elif l == "C":
                        c["bad"] += 1
                    else:
                        c["unjudged"] += 1
            ws.append([v, nm, n_seed, c["kept"], c["completed"], c["flat"], c["bad"], c["unchanged"], c["omitted"],
                       c["unjudged"], c["kept"] - c["judgedA"]])
            printed.append((v, nm, n_seed, c))

    # ---- agreement between raters on the same units
    ws2 = wb.create_sheet("Agreement")
    ws2.append(["variety", "part", "rater 1", "rater 2", "units both judged",
                "exact agreement %", "Cohen's kappa",
                "Part B only: A vs not-A, units", "A vs not-A agreement %", "A vs not-A kappa", "note"])
    collapse = {"A": "A", "B": "notA", "C": "notA", "notA": "notA"}
    for v in lab.variety.unique():
        for part in ("A", "B"):
            sub = lab[(lab.variety == v) & (lab.part == part)]
            raters = sub.rater.unique()
            for i in range(len(raters)):
                for j in range(i + 1, len(raters)):
                    s1 = sub[sub.rater == raters[i]].set_index("unit_id").label
                    s2 = sub[sub.rater == raters[j]].set_index("unit_id").label
                    both = sorted(set(s1.index) & set(s2.index))
                    if not both:
                        continue
                    notes = []
                    exact_units = [u for u in both if "notA" not in (s1[u], s2[u])]
                    if exact_units:
                        po, k = kappa([s1[u] for u in exact_units], [s2[u] for u in exact_units])
                    else:
                        po, k = float("nan"), float("nan")
                    if len(exact_units) < len(both):
                        notes.append("one rater gave only 'not A' for part of these units, so the 3-way comparison uses %d" % len(exact_units))
                    if np.isnan(k) and exact_units:
                        notes.append("no variation in the labels: kappa undefined")
                    bn, bpo, bk = "", "", ""
                    if part == "B":
                        bu = [u for u in both if s1[u] in collapse and s2[u] in collapse]
                        if bu:
                            bpo_, bk_ = kappa([collapse[s1[u]] for u in bu], [collapse[s2[u]] for u in bu])
                            bn, bpo = len(bu), round(100 * bpo_, 1)
                            bk = "" if np.isnan(bk_) else round(bk_, 2)
                    ws2.append([v, part, raters[i], raters[j], len(both),
                                "" if np.isnan(po) else round(100 * po, 1), "" if np.isnan(k) else round(k, 2),
                                bn, bpo, bk, "; ".join(notes)])

    # ---- Part A summary
    ws3 = wb.create_sheet("Flagged_words")
    ws3.append(["variety", "rater", "words judged", "S (acceptable form)", "E (real error missed by writers)",
                "? (unsure)", "judged one by one", "covered only by a one-line statement",
                "95% upper bound on E share (rule of three; only when E = 0)"])
    for (v, r), g in lab[lab.part == "A"].groupby(["variety", "rater"]):
        n = len(g)
        ws3.append([v, r, n, int((g.label == "S").sum()), int((g.label == "E").sum()), int((g.label == "?").sum()),
                    int((g.source == "explicit").sum()), int((g.source == "blanket").sum()),
                    "n/a" if (g.label == "E").sum() else "%.1f%%" % (100 * 3 / n)])

    for w in wb.worksheets:
        for c in w[1]:
            c.font = Font(name="Arial", bold=True, color="FFFFFF", size=10)
            c.fill = PatternFill("solid", fgColor="2F5496")
            c.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
        for row in w.iter_rows(min_row=2):
            for c in row:
                c.font = Font(name="Arial", size=10)
                c.alignment = Alignment(wrap_text=True, vertical="top")
        for i in range(1, w.max_column + 1):
            w.column_dimensions[get_column_letter(i)].width = 22
        w.freeze_panes = "C2"
    wb.save(a.out)

    print("\nOUTCOME OF THE SEEDED ERRORS, after the readers' judgement (correct task)")
    for v, nm, n, c in printed:
        pc = lambda k: "%3d%%" % round(100 * c[k] / n)
        print(f"  {v:8s} {nm:17s} kept {pc('kept')} | completed {pc('completed')} | FLATTENED {pc('flat')} | "
              f"not resolved/new error {pc('bad')} | unchanged {pc('unchanged')}   (n={n}, unjudged {c['unjudged']})")
    print("\nwrote", a.out)


if __name__ == "__main__":
    main()
