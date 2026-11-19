# Dataset card — Tamil dialect evaluation benchmark v0.1

## Composition
96 passages of three to four lines: 24 in each of Chennai, Kongu and Madurai Tamil and 24 in written Tamil
(control). Mean length in words: Chennai 38.5, Kongu 53.8, Madurai 48.4, General 42.9. Each passage exists twice: a reference text and
a copy with seeded errors. 294 seeded errors, by type:

| error type | n |
|---|---|
| ப1 | 52 |
| க1 | 42 |
| த1 | 42 |
| எ1 | 38 |
| ந1 | 29 |
| ஒ1 | 25 |
| இ1 | 24 |
| ச1 | 23 |
| வ1 | 12 |
| ம1 | 4 |
| ஒ2 | 3 |

## Collection
One native-speaker writer per variety composed the passages (no passage was copied from a published source or
produced with an AI tool; each writer confirmed this in a signed statement). Spellings of the dialect passages
were reviewed by one cross-variety reviewer. The seeded errors were designed with the help of an AI model
(Claude, Anthropic) and each writer confirmed that the seeded forms are errors. Claude models were therefore not
evaluated. Writers assigned their rights in the passages to the dataset compiler under a written agreement.

## Judgements
Six readers judged (a) words that the models flagged but that were not seeded errors and (b) repairs that differed
from the reference form. Reader identities are withheld; labels carry a pseudonym (R1–R6). Column `source` shows
whether a label was given word by word (`explicit`), as a summary statement for many words (`blanket`) or as one
paragraph for a group (`holistic`).

## Intended use
Evaluating whether language models keep or flatten Tamil regional forms when asked to correct or detect grammar errors.
Not intended for training.

## Limitations
Four passage sets differ in topic and writer; dialect spellings are the writers' conventions; consonant-doubling errors
are partly a matter of style; one run per model and condition. See `KNOWN_ISSUES.md`.
