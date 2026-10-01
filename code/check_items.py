#!/usr/bin/env python3
"""
Consistency check for items.json.  Run it after ANY edit to the items:

    python check_items.py items.json

For every item it verifies, in order:
  1. every seeded 'wrong' form occurs in the error text
  2. every seeded 'correct' form occurs in the clean text
  3. the wrong form does not also sit, as a whole word, in the clean text
  4. undoing every seeded error in the error text gives the clean text EXACTLY
     (this is the one that catches an edit applied to one text but not the other)
Exit code is 1 if anything is wrong.
"""
import json, re, sys, unicodedata
nfc = lambda s: unicodedata.normalize("NFC", s)
items = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "items.json", encoding="utf-8"))
problems = 0
for it in items:
    e, c, iid = nfc(it["error_text"]), nfc(it["clean_text"]), it["item_id"]
    undone = e
    for s in it["seeded"]:
        w, r = nfc(s["wrong"]), nfc(s["correct"])
        if w not in e:
            print(iid, "1. wrong form not in error text:", s["code"], w); problems += 1
        if r not in c:
            print(iid, "2. correct form not in clean text:", s["code"], r); problems += 1
        if re.search(r"(^|[\s“”,.!?;])" + re.escape(w) + r"([\s“”,.!?;]|$)", c):
            print(iid, "3. wrong form also in clean text:", s["code"], w); problems += 1
        if w in undone:
            undone = undone.replace(w, r, 1)
    if undone != c:
        print(iid, "4. error text with seeds undone != clean text"); problems += 1
print(len(items), "items,", sum(i["seeded_count"] for i in items), "seeded errors ->",
      "ALL CONSISTENT" if not problems else "%d PROBLEM(S)" % problems)
sys.exit(1 if problems else 0)
