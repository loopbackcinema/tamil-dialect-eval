#!/usr/bin/env python3
"""
Build the adjudication sheets that native writers fill in.

    python make_adjudication.py --scores scores --raw runs_raw.csv --items items.json --outdir adjudication

PART A  un-seeded words a model flagged.  Writer says: S = acceptable form, E = a real error the
        writers missed, ? = unsure.  (Model identity is hidden.)
PART B  places where a model changed a seeded error to a form other than our reference.
        Writer says: A = error fixed and dialect kept, B = error fixed but dialect changed,
        C = error not fixed / wrong, ? = unsure.

Units are listed in a RANDOM order (fixed seed).  Any top-down prefix is therefore a valid random
sample, so the flagged-word false-positive rate can be estimated from however far the writer gets.

Writes, per variety:   <V>_adjudication.xlsx   <V>_priority.json   (-> docx/PDF via build_adjudication_docx.js)
and one internal file  adjudication_key.csv   that maps every unit back to models. Keep that one private.
"""
import argparse, csv, difflib, html, json, os, random, sys
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import score_runs as sr
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

VARIETIES = {"Chennai": ("CHN", "சென்னை"), "Kongu": ("KON", "கொங்கு"),
             "Madurai": ("MDU", "மதுரை"), "General": ("GEN", "பொது (எழுத்து) தமிழ்")}
TYPE_TA = {"ப1": "பால்", "த1": "திணை", "க1": "காலம்", "எ1": "எண்", "இ1": "இடம்",
           "ஒ1": "ஒற்று விடுபட்டது", "ஒ2": "மிகை ஒற்று", "ம1": "புணர்ச்சி",
           "வ1": "வேற்றுமை உருபு", "ச1": "அறுந்த சொற்றொடர்", "ந1": "நிறுத்தற்குறி"}
PRIORITY_A = 150
WINDOW = 8


def mark(tokens, i1, i2, w=WINDOW):
    """Context window of raw tokens with the flagged span in 【 】."""
    a, b = max(0, i1 - w), min(len(tokens), i2 + w)
    left = " ".join(t["raw"] for t in tokens[a:i1])
    mid = " ".join(t["raw"] for t in tokens[i1:i2])
    right = " ".join(t["raw"] for t in tokens[i2:b])
    return ("… " if a > 0 else "") + (left + " " if left else "") + "【" + mid + "】" + \
           (" " + right if right else "") + (" …" if b < len(tokens) else "")


def find_seq(keys, target):
    for i in range(len(keys) - len(target) + 1):
        if keys[i:i + len(target)] == target:
            return i
    return None


def best_window(Okeys, Otoks, Ewin_keys, centre):
    n = max(len(Ewin_keys), 1)
    best, bs = None, -1
    for k in range(max(0, centre - 16), min(len(Okeys), centre + 16) + 1):
        seg = Okeys[k:k + n]
        if not seg:
            continue
        r = difflib.SequenceMatcher(None, " ".join(Ewin_keys), " ".join(seg)).ratio()
        if r > bs:
            bs, best = r, k
    if best is None:
        return "(பதிலில் இந்தப் பகுதி காணப்படவில்லை)"
    a = best
    return ("… " if a > 0 else "") + " ".join(t["raw"] for t in Otoks[a:a + n]) + \
           (" …" if a + n < len(Otoks) else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scores", default="scores")
    ap.add_argument("--raw", default="runs_raw.csv")
    ap.add_argument("--items", default="items.json")
    ap.add_argument("--outdir", default="adjudication")
    ap.add_argument("--seed", type=int, default=20260930)
    a = ap.parse_args()
    os.makedirs(a.outdir, exist_ok=True)

    items = {i["item_id"]: i for i in json.load(open(a.items, encoding="utf-8"))}
    raw = {(r["item_id"], r["model_code"], r["task"]): r
           for r in csv.DictReader(open(a.raw, encoding="utf-8-sig"))}
    unm = pd.read_csv(f"{a.scores}/unmatched_flags.csv", encoding="utf-8-sig")
    unm["in_text"] = unm.in_text.astype(str) == "True"
    so = pd.read_csv(f"{a.scores}/seed_outcomes.csv", encoding="utf-8-sig")

    # ------------------------------------------------ PART A units
    A, phrase_level, unlocated, b_skipped = defaultdict_list(), 0, 0, 0
    u = unm[unm.in_text].copy()
    u["lkey"] = u.left.map(lambda s: " ".join(sr.key(t) for t in s.split() if sr.key(t)))
    key_rows = []
    for (iid, lkey), g in u.groupby(["item_id", "lkey"]):
        lk = lkey.split()
        if len(lk) > 3:
            phrase_level += 1
            key_rows.append({"part": "A", "unit_id": "", "variety": items[iid]["variety"], "item_id": iid,
                             "unit_key": lkey, "models": ",".join(sorted(set(g.model_code))),
                             "status": "phrase_level_not_adjudicated"})
            continue
        E = sr.tokenize(items[iid]["error_text"])
        Ek = [t["key"] for t in E]
        pos, span = find_seq(Ek, lk), len(lk)
        if pos is None:                       # a fragment inside one passage word, e.g. before an attached ”னு
            frag = "".join(lk)
            pos = next((i for i, k in enumerate(Ek) if frag in k), None)
            span = 1
        if pos is None:
            unlocated += 1
            continue
        rights, seen = [], set()
        for r_ in g.right:
            r_ = str(r_).strip()
            if r_ and r_ not in seen:
                seen.add(r_)
                rights.append(r_)
        A[items[iid]["variety"]].append({
            "item_id": iid, "unit_key": lkey, "context": mark(E, pos, pos + span),
            "flagged": g.left.value_counts().index[0],
            "suggested": " / ".join(rights[:3]), "models": ",".join(sorted(set(g.model_code)))})

    # ------------------------------------------------ PART B units
    Bm = defaultdict_list()
    b = so[(so.task == "correct") & (so.outcome == "changed_other")].copy()
    b["model_form"] = b.model_form.fillna("")
    for (iid, code, eform, mform), g in b.groupby(["item_id", "code", "error_form", "model_form"]):
        it = items[iid]
        E = sr.tokenize(it["error_text"])
        et = [t["raw"] for t in E]
        toks = eform.split()
        i1 = next((i for i in range(len(et)) if et[i:i + len(toks)] == toks), None)
        if i1 is None:
            i1 = next((i for i, t in enumerate(et) if toks and t == toks[0]), None)
        if i1 is None:
            b_skipped += 1
            continue
        i2 = i1 + max(1, len(toks))
        a_, b_ = max(0, i1 - WINDOW), min(len(E), i2 + WINDOW)
        ewin = [t["key"] for t in E[a_:b_]]
        model = g.model_code.iloc[0]
        O = sr.tokenize(raw[(iid, model, "correct")]["response"])
        Bm[it["variety"]].append({
            "item_id": iid, "code": code, "type_ta": TYPE_TA.get(code, code),
            "error_form": eform, "reference": g.reference_form.iloc[0], "model_form": mform,
            "context": mark(E, i1, i2),
            "model_sentence": best_window([t["key"] for t in O], O, ewin, i1),
            "models": ",".join(sorted(set(g.model_code))),
            "unit_key": f"{code}|{eform}|{mform}"})

    # ------------------------------------------------ order, number, write
    rng = random.Random(a.seed)
    summary = []
    for variety, (pref, ta) in VARIETIES.items():
        ua = sorted(A[variety], key=lambda x: (x["item_id"], x["unit_key"]))
        rng.shuffle(ua)
        for n, x in enumerate(ua, 1):
            x["unit_id"] = f"A-{pref}-{n:03d}"
            x["rank"] = n
            x["priority"] = n <= PRIORITY_A
        ub = sorted(Bm[variety], key=lambda x: (x["item_id"], x["code"], x["model_form"]))
        for n, x in enumerate(ub, 1):
            x["unit_id"] = f"B-{pref}-{n:03d}"
        for x in ua:
            key_rows.append({"part": "A", "unit_id": x["unit_id"], "variety": variety, "item_id": x["item_id"],
                             "unit_key": x["unit_key"], "models": x["models"],
                             "status": "priority" if x["priority"] else "later"})
        for x in ub:
            key_rows.append({"part": "B", "unit_id": x["unit_id"], "variety": variety, "item_id": x["item_id"],
                             "unit_key": x["unit_key"], "models": x["models"], "status": "all"})
        write_xlsx(os.path.join(a.outdir, f"{pref}_adjudication.xlsx"), ta, ua, ub)
        write_json(os.path.join(a.outdir, f"{pref}_priority.json"), ta, ua, ub)
        summary.append((variety, len(ua), min(len(ua), PRIORITY_A), len(ub)))

    with open(os.path.join(a.outdir, "adjudication_key.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["part", "unit_id", "variety", "item_id", "unit_key", "models", "status"])
        w.writeheader()
        w.writerows(key_rows)
    print("variety  A-units  A-priority  B-units")
    for s in summary:
        print("%-8s %7d %11d %8d" % s)
    print("phrase-level flags (more than 3 words, not adjudicated):", phrase_level,
          "| could not be located in the passage:", unlocated,
          "| repair units that could not be located:", b_skipped)


def defaultdict_list():
    import collections
    return collections.defaultdict(list)


# ------------------------------------------------------------------ xlsx
FONT = "Nirmala UI"


def write_xlsx(path, ta, ua, ub):
    wb = Workbook()
    ws = wb.active
    ws.title = "படிக்க"
    for line in README_TA(ta):
        ws.append([line])
    ws.column_dimensions["A"].width = 120
    for row in ws.iter_rows():
        for c in row:
            c.font = Font(name=FONT, size=11)
            c.alignment = Alignment(wrap_text=True, vertical="top")
    thin = Side(style="thin", color="BFBFBF")
    hfill = PatternFill("solid", fgColor="2F5496")
    yel = PatternFill("solid", fgColor="FFF2CC")

    def sheet(title, header, rows, widths, label_col, options):
        w = wb.create_sheet(title)
        w.append(header)
        for c in w[1]:
            c.font = Font(name=FONT, bold=True, color="FFFFFF", size=10)
            c.fill = hfill
            c.alignment = Alignment(wrap_text=True, vertical="center")
        for r in rows:
            w.append(r)
        for row in w.iter_rows(min_row=2):
            for c in row:
                c.font = Font(name=FONT, size=11)
                c.alignment = Alignment(wrap_text=True, vertical="top")
                c.border = Border(left=thin, right=thin, top=thin, bottom=thin)
        for i, wd in enumerate(widths, 1):
            w.column_dimensions[get_column_letter(i)].width = wd
        w.freeze_panes = "B2"
        if rows:
            dv = DataValidation(type="list", formula1='"%s"' % ",".join(options), allow_blank=True)
            w.add_data_validation(dv)
            col = get_column_letter(label_col)
            dv.add(f"{col}2:{col}{len(rows) + 1}")
            for r in range(2, len(rows) + 2):
                w.cell(row=r, column=label_col).fill = yel
        return w

    sheet("A_சொற்கள்", ["எண்", "சூழல் (சொல் 【 】-க்குள்)", "AI கேள்வி கேட்ட சொல்", "AI தந்த மாற்று வடிவம்", "முன்னுரிமை", "உங்கள் பதில் (S / E / ?)", "குறிப்பு"],
          [[x["unit_id"], x["context"], x["flagged"], x["suggested"], "முதலில்" if x["priority"] else "பிறகு", "", ""] for x in ua],
          [12, 66, 20, 26, 12, 16, 30], 6, ["S", "E", "?"])
    sheet("B_திருத்தங்கள்", ["எண்", "பிழை வகை", "சூழல் (செருகிய பிழை 【 】-க்குள்)", "சரியான வடிவம் (நாங்கள்)",
                             "AI எழுதிய வடிவம்", "AI-யின் வாக்கியம்", "உங்கள் பதில் (A / B / C / ?)", "குறிப்பு"],
          [[x["unit_id"], x["type_ta"], x["context"], x["reference"], x["model_form"] or "(தெளிவாக இல்லை)",
            x["model_sentence"], "", ""] for x in ub],
          [12, 14, 55, 22, 22, 55, 16, 30], 7, ["A", "B", "C", "?"])
    wb.save(path)


def README_TA(ta):
    return [
        f"{ta} வழக்கு — AI-யின் பதில்களைப் பார்த்துத் தீர்ப்பளித்தல்",
        "",
        "இந்தப் பணி ஏன்: AI அமைப்புகள் உங்கள் பத்திகளில் சில சொற்களைப் 'பிழை' என்று சொன்னன. அவை உண்மையிலேயே பிழையா, அல்லது உங்கள் வழக்கில் இயல்பான வடிவமா என்பதை நீங்கள் மட்டுமே சொல்ல முடியும்.",
        "AI எது என்பது இங்கே மறைக்கப்பட்டிருக்கிறது. பெயர்களைப் பற்றிக் கவலைப்பட வேண்டாம்.",
        "",
        "பகுதி A — சொற்கள் (தாள்: A_சொற்கள்)",
        "  【 】-க்குள் இருக்கும் சொல் நாங்கள் செருகிய பிழை அல்ல. AI அதைப் பிழை என்றது. உங்கள் வழக்கில்:",
        "    S = இது சரியான வடிவம், பிழை அல்ல",
        "    E = இது உண்மையிலேயே பிழை (நாங்கள் கவனிக்கவில்லை)",
        "    ? = உறுதியாகத் தெரியவில்லை",
        "  அந்த வாக்கியத்தில் நாங்கள் செருகிய வேறு பிழைகள் இருக்கலாம். அவற்றைப் பற்றிக் கேட்கவில்லை. 【 】-க்குள் உள்ள சொல்லை மட்டும் பாருங்கள்.",
        "  வரிசை தற்செயலானது. மேலிருந்து கீழே செய்யுங்கள். முதல் 150 'முதலில்' என்று குறிக்கப்பட்டுள்ளன. அவற்றை முடித்தால் போதும். மீதி நேரம் இருந்தால் தொடருங்கள்.",
        "",
        "பகுதி B — திருத்தங்கள் (தாள்: B_திருத்தங்கள்)",
        "  இங்கே நாங்கள் செருகிய பிழையை AI வேறு வடிவத்தில் திருத்தியிருக்கிறது. நீங்கள் சொல்ல வேண்டியது:",
        "    A = பிழை தீர்ந்தது, வட்டார வடிவம் அப்படியே இருக்கிறது",
        "    B = பிழை தீர்ந்தது, ஆனால் வட்டார வடிவம் மாறிவிட்டது (பொது / வேறு வடிவம் ஆகிவிட்டது)",
        "    C = பிழை தீரவில்லை, அல்லது புதிய தவறு வந்துவிட்டது",
        "    ? = உறுதியாகத் தெரியவில்லை",
        "",
        "பதில் சொல்லும் முறை: Excel-ல் மஞ்சள் பெட்டியில் தேர்வு செய்யுங்கள். அல்லது WhatsApp-ல் இப்படி அனுப்புங்கள்:  A-CHN-001 S,  A-CHN-002 E,  B-CHN-001 A",
    ]


# ------------------------------------------------------------------ json (-> docx -> pdf)
def write_json(path, ta, ua, ub):
    pri = [x for x in ua if x["priority"]]
    json.dump({"title": ta, "readme": README_TA(ta)[2:], "priority_n": len(pri),
               "A": [{"id": x["unit_id"], "context": x["context"], "flagged": x["flagged"],
                      "suggested": x["suggested"]} for x in pri],
               "B": [{"id": x["unit_id"], "type": x["type_ta"], "context": x["context"],
                      "reference": x["reference"], "model_form": x["model_form"],
                      "model_sentence": x["model_sentence"]} for x in ub]},
              open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
