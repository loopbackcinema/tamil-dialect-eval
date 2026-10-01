# Known issues (v0.1)

1. **GEN-05 reference text contains a real error.** The phrase `பகுதியைச் சென்று` (case suffix) was confirmed as an error
   by two readers. The models flagged it correctly; in the analysis it is a correct flag. Planned fix: v0.2.
2. **Consonant-doubling errors (ஒ2, ம1; 7 seeded errors, all written Tamil)** depend partly on the writer's style.
3. **Collateral flags.** A seeded error can make a neighbouring correct word look wrong to a model (example: a seeded
   masculine verb made a model propose changing a correct feminine pronoun).
4. **Orthography confound.** The dialect passages use colloquial spellings; flags may react to spelling and not to dialect.
5. **CHN-23 and CHN-24** had their error text corrected after the first model run; both were re-run in both conditions.
   `runs/main/runs_log.jsonl` still contains the superseded failed attempts of the first Sarvam pass as lines.
6. **Settings field.** Records written before the settings field was added (M1 and M3 in the main run) have an empty
   `params` field; their settings are those in `code/models.json`.
7. **Sarvam-105b with reasoning on** did not return an answer in 85 of 192 calls (`runs/sarvam_reasoning_low/`).
8. **Reader judgements.** Some labels for flagged words were given as one summary statement (`source = blanket`).
   Second readings exist for part of the samples only (see the paper).
9. **Run-to-run variation** was measured only for Sarvam-105b (identical settings in both conditions).
