# Tamil dialect evaluation benchmark — v0.1 (frozen snapshot)

Does an AI model treat a regional Tamil form as an error? 96 short passages (24 each in Chennai, Kongu and
Madurai Tamil and in written Tamil as control), 294 seeded grammatical errors, three models, two tasks,
two reasoning conditions. This repository holds the data, prompts, code, raw model responses, scores and the
readers' judgements behind the paper.

**Status: public archive accompanying the paper. Data: CC BY 4.0 (see `DATA_LICENSE.txt`). Code: MIT.** DOI: https://doi.org/10.5281/zenodo.24346687

Also available at: [Hugging Face](https://huggingface.co/datasets/FeelEd/tamil-dialect-eval) · [Zenodo](https://doi.org/10.5281/zenodo.24346687) · [AIKosh](https://aikosh.indiaai.gov.in/web/datasets/details/tamil_dialect_eval.html)

## Layout
| path | content |
|---|---|
| `data/items.json` | the 96 passages: reference text, text with seeded errors, each seeded error and its type |
| `data/prompts_used.txt` | the two prompts, verbatim (frozen before the run) |
| `code/` | run, score, compare and adjudication scripts; `models.json` and `models_parity.json` are the two conditions |
| `runs/main/runs_log.jsonl` | every call of the main-condition run (failed attempts are kept as lines; the last successful line per item/model/task is the response) |
| `runs/reasoning_off/runs_raw.csv` | the reasoning-off run |
| `runs/sarvam_reasoning_low/` | Sarvam-105b with reasoning on: 85 of 192 calls returned no answer (supplementary) |
| `scores/` | automatic scores for both conditions |
| `adjudication/` | readers' judgements, pseudonymised (R1–R6) |
| `results/`, `figures/` | result workbooks and figures |

## Reproduce
```
python code/check_items.py data/items.json
python code/score_runs.py --raw runs/reasoning_off/runs_raw.csv --items data/items.json --outdir scores/reasoning_off
python code/make_results.py --scores scores/reasoning_off --raw runs/reasoning_off/runs_raw.csv --out results/test.xlsx
```
Re-running the models needs your own API keys as environment variables (`code/run_models.py`, `code/models.json`).
The main run's `runs_raw.csv` can be rebuilt from `runs/main/runs_log.jsonl`.

## Not in this repository
The key that links flagged words to models (it unblinds the readers' task while it is still in progress), the
list of adjudication units, the readers' identities, consent forms and correspondence.

## Models and dates
`gpt-5`, `gemini-2.5-flash`, `sarvam-105b`, called through the providers' APIs on 30 Sep – 1 Oct 2026. Exact snapshot
identifiers were not recorded; the strings above are the aliases used.

See `DATASET_CARD.md` for composition and `KNOWN_ISSUES.md` for defects found so far.
