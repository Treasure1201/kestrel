# Evaluation

I used repeated model runs, automatic grading and human review to assess the answers. This document records the results, the failures and the commands to reproduce them.

## Reproduce

Run these commands from the repository root after `uv sync`, with a key in `.env`. Each question uses a few model calls; the results include cost.

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

A missing condition such as "working days" or "after receipt of all documents" makes an answer partial even if the number is right. A superseded figure without a marker such as "2025" or "previous" makes it incorrect. Retrieval misses, where no excerpt came from the expected section, are reported alongside the grade.

String matching also produces false alarms. One correct answer described the 10 working days as "not a guaranteed time" and matched the forbidden word "guarantee". I changed the gold file to allow a negated guarantee and re-graded the saved run offline. Both grades remain in the run folder.

## Human review

The automatic grade is a screening signal and is never copied into the workbook. A person reads each answer with its quotes in `results.csv` and writes one of the five workbook values (Correct, Partially correct, Incorrect, Correctly declined, Incorrectly declined) in `review.yaml`. `eval.finalise` refuses to run while any entry is empty or invalid, then writes `KA_Test_Questions_SUBMISSION.xlsx` and adds the human assessment to `results.csv`. `eval.report` shows the automatic and human counts side by side.

## Results

I ran each model on the final code three times: 15 questions per run, 13 answerable and 2 not, giving 39 and 6 per model. These are automatic grades; I read every answer not graded correct.

| Model | Correct | Correctly declined | Other | Needed a repair | Cost per question | Median time |
|---|---|---|---|---|---|---|
| Claude Haiku 5.5 (default) | 39 of 39 | 6 of 6 | 0 | 1 | US$0.0015 | 3.9 s |
| Claude Sonnet 5.5 | 39 of 39 | 6 of 6 | 0 | 3 | US$0.027 | 4.6 s |
| OpenAI `gpt-6.1-sol` | 39 of 39 | 6 of 6 | 0 | 3 | US$0.016 | 6.0 s |
| Kimi `kimi-k3.1-pro` (Ollama Cloud) | 39 of 39 | 6 of 6 | 0 | 0 | US$0.025 | 6.9 s |
| `gpt-oss:20b` (Ollama Cloud) | 39 of 39 | 6 of 6 | 0 | 0 | US$0.0009 | 7.0 s |
| `gpt-oss:20b` (local Ollama) | 39 of 39 | 6 of 6 | 0 | 0 | no API charge | 5.7 s |
| DeepSeek `deepseek-v4.1-flash` (Ollama Cloud) | 37 of 39 | 6 of 6 | 1 partial, 1 graded incorrect | 1 | US$0.0033 | 2.7 s |
| GLM `glm-5.3-flash` (Ollama Cloud) | 37 of 39 | 6 of 6 | 2 graded incorrect | 1 | US$0.0017 | 3.5 s |

No answer was withheld or errored. DeepSeek's partial answer is a real miss: it dropped the condition "before normal retirement age". The three answers graded incorrect give the right figure but volunteer a neighbouring one the gold answers forbid (for example the disability limit beside the group life limit), so I do not recommend those two models. "Needed a repair" counts answers whose first draft failed an evidence check and was corrected. The submitted workbook is one further Haiku run on the final commit: 13 correct, 2 correctly declined, no repairs. All runs: [results/report.md](../results/report.md).

- Reworded set (10 questions written without sight of any model answer), Claude Haiku 5.5: 8 graded correct, 1 correctly declined and 1 graded incorrect, which is a grader false alarm (the answer, R65 000, is right). Search alone ranked the needed section first for 13 of 13 test questions but only 7 of 9 reworded ones, so retrieval depends on wording more than the headline shows.
- Cost per question is measured from token counts at the providers' list prices on 23 November 2026 (`config/pricing.yaml` names the sources): about US$0.0015 on Claude Haiku 5.5 (roughly 12 000 input and 600 output tokens over two or three model calls), up to US$0.027 on Claude Sonnet 5.5. Caching discounts are not modelled, so these are upper estimates. Local Ollama has no API charge.
- Saved runs in `results/history/` include the evidence behind the fixes.

## First-run failures and what changed

I kept each model's first run before making fixes (`results/dev/first_run_*.jsonl`). The fixes apply generally and have tests; I added no question-specific rules.

| What went wrong | Cause | Fix |
|---|---|---|
| Claude Sonnet 5.5 answered none of the 15 questions: every one ended in `error` | The provider rejected the forced tool choice that tool-call structured output sets | The call is retried once with an automatic tool choice and the model is remembered; an answer written as JSON text is accepted if it validates, and still checked |
| Claude Haiku 5.5, Q4 (disability free cover limit for 1 200 members) withheld | The model cited a whole table and quoted its status label instead of the row | A whole-table search hit is shown as labelled rows straight away, and a row hit points to its row label |
| Claude Haiku 5.5, Q6 (Option C benefit on R50 000) withheld | The answer wrote 0.75 where the excerpt said 75%, so the number check rejected it | A percentage in a cited excerpt also grounds its fraction |
| Automatic grader marked a correct Q15 incorrect | "not a guaranteed time" matched the forbidden word "guarantee" | Negated guarantee allowed in the gold file; re-graded offline, no model called |
| Product-bot skills | Measured on the test questions: no grade changed and input tokens rose by about a third | Skills switched off for the product bot |
| Staff bot dropped a correct working-day count from the calendar tool | The number checks accepted only document excerpts, so a correct figure from an approved tool was stripped | Approved tool results are labelled and citable; a rule still needs a document citation |
| Independent code review (after the build) found ten defects, including: a number from a tool was enough to pass an invented rule; 0.015% passed against a source saying 0.01%; the quote "covered" matched "uncovered"; an ID number sent to a tool as an integer was not screened; provider error text could reach the log | Checks and screens written quickly were looser than the documents claimed | All ten fixed, each with a test that failed first: a document citation is always required, sourced numbers must match exactly, quotes must fall on word boundaries, tool arguments of every type are screened, and error text is redacted before logging |
| Two questions passed only after a repair turn in every run, and were sometimes withheld | The model read a table of ranges by eye and started from the wrong band; caveats quoted figures from passages the answer did not cite | The table tool finds the row whose range contains the number; an unsupported caveat is removed and recorded instead of costing the repair turn; repairs per model are now reported |
| Allowing two repair turns removed the withheld answers but let one self-contradictory answer through | A second repair let the model cite both the wrong and the right row | Kept at one repair turn: withholding is the safer failure |

## Adversarial evaluation

The claims FAQ hides one instruction in white 1-point text. One example does not show whether the scanner generalises, so `eval/adversarial/` builds 21 fixtures, each hiding a canary instruction with a different technique across DOCX, PDF, XLSX and Markdown (white or near-white text, tiny fonts, hidden runs, white text in white cells, hidden rows, columns and sheets, off-page text, invisible render mode, text covered by a shape, HTML comments, Unicode tag characters, zero-width characters, bidirectional overrides, visible instructions), plus six controls that must be left alone (clean files, and light text on dark cells).

- The **scan** uses no model. It checks detection, exclusion of the planted instruction from search and preservation of clean controls. It missed three techniques: text covered by a drawn shape, text reversed by bidirectional controls, and a visible instruction with no trigger wording. The first two are now detected and removed. The third remains searchable by design and can inform answers if approved. Document approval is the control for it.
- The **attack** asks one model a question whose true answer is in the fixture, then checks whether the answer follows the planted instruction.

Of 21 hiding techniques across DOCX, PDF, XLSX and Markdown, 20 are detected, all 18 planted instructions that should be removed are kept out of search, and none of 6 clean controls is flagged. The miss is a visible instruction with no AI-directed wording. In 21 attack questions each on Claude Haiku 5.5 and local `gpt-oss:20b`, no answer followed a planted instruction (attack run made before the last two scanner fixes).

## Limits

I used the 15 questions for development as well as evaluation. Fixing their failures makes the scores look better than they would on unseen questions. The reworded set is the nearest independent check: it was written without sight of model answers, but it is small. Fifteen questions cannot reliably separate models; one answer moves a score by almost seven points. Repeats show variation, not population accuracy.

The team that built the system also wrote the gold file. Its string-matching grader can be wrong in either direction, so a person reviews every answer. Evidence checks leave another gap: a correct quote can support an answer that drops a condition. The gold conditions and human review catch those omissions.

Costs use list prices from `config/pricing.yaml` without caching discounts; Ollama Cloud and local models are reported as unknown cost. For production, I would build a golden set of a hundred or more anonymised agent questions, with answers agreed by content owners (see [PRODUCTION_ARCHITECTURE.md](PRODUCTION_ARCHITECTURE.md)).
