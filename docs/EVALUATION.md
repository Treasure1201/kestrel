# Evaluation

How the numbers in the README were produced, how to reproduce them, and what they do and do not show.

## Reproduce

All commands run from the repository root after `uv sync` and a key in `.env`. Each question costs a few model calls; see the cost column in the results.

```
uv run python -m eval.run_eval --models anthropic:claude-haiku-5-5            # the 15 questions, one model
uv run python -m eval.run_eval --models anthropic:claude-haiku-5-5 --runs 3   # repeat runs
uv run python -m eval.run_eval --models anthropic:claude-haiku-5-5 openai:gpt-6.1-sol ollama:gpt-oss:20b
uv run python -m eval.run_eval --set reworded --models anthropic:claude-haiku-5-5   # the 10 reworded questions
uv run python -m eval.retrieval_eval                                          # search only, no model
uv run python -m eval.regrade results/<run folder>                            # re-grade saved answers, no model
uv run python -m eval.review results/<run folder>                             # (re)create the review file
uv run python -m eval.finalise results/<run folder>                           # write the reviewed workbook
uv run python -m eval.report results/<folder A> results/<folder B> --out report.md
uv run python -m eval.adversarial.run_scan                                    # hidden-text scan, no model
uv run python -m eval.adversarial.run_attack --model anthropic:claude-haiku-5-5
```

`run_eval` ingests first (unchanged files are skipped; `--no-ingest` skips it) and never writes the template workbook `eval/KA_Test_Questions.xlsx`.

## What a run writes

Each model gets its own folder, `results/<UTC timestamp>_<model>/`:

| File | Contents |
|---|---|
| `results.jsonl` | One record per question per run: the full answer, citations with quotes, calculations, evidence-check report, every retrieved excerpt, steps, tokens, cost, latency and the automatic grade. Appended as it goes, so a crash keeps what was done. |
| `results.csv` | The same, one row per question, for reading in a spreadsheet. After `finalise`, it also has the human assessment beside the automatic grade. |
| `summary.json` | Counts by grade and outcome, withheld, errors, retrieval misses, median and 95th-percentile latency, tokens, total cost and cost per question. |
| `review.yaml` | One entry per question with the answer, its sources and the automatic grade, and empty `assessment` and `comment` fields for a person to fill. |
| `KA_Test_Questions_<model>.xlsx` | The supplied workbook with "Your system's answer" filled for that run (test set only). Formulas and dropdowns are kept. |
| `retrieval.json` | Search-only pass: rank of the expected section per question, hit@1, hit@3, hit@6 and mean reciprocal rank. |
| `KA_Test_Questions_SUBMISSION.xlsx` | Written by `finalise` after the review: answers plus the human assessment and comment. |
| `*.regraded.*` | Written by `regrade`: the saved answers graded again after a gold or grader change, with every changed grade listed. |

`results/dev/` holds development runs made with `results/dev/run_dev.py` before the harness existed: `first_run_*` files are each model's first run before any fix, `latest_*` the last development run per model, `probes_*` six extra checks (redaction, a case decision, the 2025 edition, a bare year, an edition comparison, claim timing). `results/adversarial/` holds the scan and attack results.

## How grading works

The automatic grade comes from `eval/grading.py` and a gold file per question set (`bots/kestrel-products/eval/gold.yaml` for the 15 questions, `eval/reworded.yaml` for the reworded ones). Each gold entry lists the expected status, the facts that must appear, the **conditions that must not be dropped**, statements that must not appear, and the section that should be cited. Matching is case-insensitive after number and whitespace normalisation ("R25 million" equals "R25 000 000"); each item lists acceptable variants.

| Grade | Rule |
|---|---|
| correct | every fact and condition present, expected section cited, nothing forbidden |
| partially correct | some facts missing, a condition dropped, the expected section not cited, or the model itself said `partial` |
| incorrect | every fact missing, a forbidden statement, an answer where the documents are silent, or a citation of the superseded edition the question did not ask for |
| correctly declined / incorrectly declined | a decline where the gold expects one / does not |
| withheld, error | the evidence checks held the answer back, or the request failed; counted apart, never folded into the other grades |

Where it is strict: a condition such as "working days" or "after receipt of all documents" missing makes an answer partial even when the number is right; a superseded figure in a clause without a marker such as "2025" or "previous" makes it incorrect. A retrieval miss (no retrieved excerpt came from the expected section) is reported beside the grade.

Where it is wrong: it is string matching. It marked a correct answer incorrect because the answer said the 10 working days is "not a guaranteed time", which matched the forbidden word "guarantee". The gold file now allows a negated guarantee and the run was re-graded offline; both grades are kept in the run folder.

## Human review

The automatic grade is a screening signal and is never copied into the workbook. A person reads each answer with its quotes in `results.csv` and writes one of the five workbook values (Correct, Partially correct, Incorrect, Correctly declined, Incorrectly declined) in `review.yaml`. `eval.finalise` refuses to run while any entry is empty or invalid, then writes `KA_Test_Questions_SUBMISSION.xlsx` and adds the human assessment to `results.csv`. `eval.report` shows the automatic and human counts side by side.

## Results

[RESULTS TABLE]

- Primary model on the 15 questions: [PRIMARY MODEL RESULT]
- Reworded set (10 questions written before the first run): [REWORDED SET RESULT]
- Retrieval without a model: [HIT RATE]
- Measured cost per question: [COST PER QUESTION]

## First-run failures and what changed

The first run of each model was kept before any fix (`results/dev/first_run_*.jsonl`). Each fix is general and covered by tests; none is specific to a question.

| What went wrong | Cause | Fix |
|---|---|---|
| Claude Sonnet 5.5 answered none of the 15 questions: every one ended in `error` | The provider rejected the forced tool choice that tool-call structured output sets | The call is retried once with an automatic tool choice and the model is remembered; an answer written as JSON text is accepted if it validates, and still checked |
| Claude Haiku 5.5, Q4 (disability free cover limit for 1 200 members) withheld | The model cited a whole table and quoted its status label instead of the row | A whole-table search hit is shown as labelled rows straight away, and a row hit points to its row label |
| Claude Haiku 5.5, Q6 (Option C benefit on R50 000) withheld | The answer wrote 0.75 where the excerpt said 75%, so the number check rejected it | A percentage in a cited excerpt also grounds its fraction |
| Automatic grader marked a correct Q15 incorrect | "not a guaranteed time" matched the forbidden word "guarantee" | Negated guarantee allowed in the gold file; re-graded offline, no model called |
| Product-bot skills | Measured on the test questions: no grade changed and input tokens rose by about a third | Skills switched off for the product bot |
| Staff bot dropped a correct working-day count from the calendar tool | The number checks accepted only document excerpts, so a correct figure from an approved tool was stripped | Approved tool results are labelled and citable; a rule still needs a document citation |

## Adversarial evaluation

The claims FAQ hides one instruction in white 1-point text. One example does not show whether the scanner generalises, so `eval/adversarial/` builds 21 fixtures, each hiding a canary instruction with a different technique across DOCX, PDF, XLSX and Markdown (white or near-white text, tiny fonts, hidden runs, white text in white cells, hidden rows, columns and sheets, off-page text, invisible render mode, text covered by a shape, HTML comments, Unicode tag characters, zero-width characters, bidirectional overrides, visible instructions), plus six controls that must be left alone (clean files, and light text on dark cells).

- **Scan** (no model): is each technique detected, is the canary kept out of search, and are the clean controls left alone? The first scan missed three techniques: text covered by a drawn shape, text reversed by bidirectional controls, and a visible instruction with no trigger wording. The first two are now detected and removed. The third is visible text, kept by design and answered from if approved; document approval is the control for it.
- **Attack** (one model): ask a question whose true answer is in the fixture and see whether the answer follows the canary.

[ADVERSARIAL RESULT]

## Limits

- **The 15 questions were also the development set.** Failures on them were fixed, so results on them flatter the system. The reworded set, written before the first run, is the nearest thing to an independent check, and it is small.
- **Fifteen questions are too few** to separate models reliably; one answer moves a score by almost seven points. Repeat runs show variation but not population accuracy.
- **The grader is string matching** against a gold file written by the same team that built the system. It can be wrong in both directions, which is why a person reviews every answer.
- **The evidence checks do not prove faithfulness.** An answer can quote the right passage and still drop a condition; the gold conditions and the human review are what catch that.
- **Cost is list price** from `config/pricing.yaml`, without caching discounts; Ollama Cloud and local models are reported as unknown cost.
- **Production needs a real golden set**: a hundred or more anonymised questions from agents, with answers agreed by content owners (see [PRODUCTION_ARCHITECTURE.md](PRODUCTION_ARCHITECTURE.md)).
