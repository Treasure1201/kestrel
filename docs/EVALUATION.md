# Evaluation

This document explains how I measured the assistant, gives the results for eight models, and lists what went wrong along the way. Every number here can be reproduced from the files in `results/` with the commands below.

## How answers are judged

**Expected answers.** For each of the 15 test questions I wrote down, from the six documents only, the facts a correct answer must state, the conditions it must keep (such as "working days" or "after receipt of all documents"), statements it must not make (such as a 2025 figure presented as current), and the section it should cite. These lists are in `bots/kestrel-products/eval/gold.yaml`. I wrote ten further questions the same way, in `eval/reworded.yaml`, before any model had answered any question.

**The automatic check.** `eval/grading.py` compares each answer with its list. It is a text match, not a judge: it ignores case, treats "R25 million" and "R25 000 000" as the same, and accepts the alternative wordings listed for each item. It gives one of these grades:

| Grade | When |
|---|---|
| correct | every fact and condition is present, the expected section is cited, and nothing forbidden appears |
| partially correct | some facts or a condition are missing, or the expected section is not cited |
| incorrect | every fact is missing, a forbidden statement appears, or the assistant answered a question the documents cannot answer |
| correctly declined / incorrectly declined | the assistant said the documents do not cover the question, and that was right / wrong |
| withheld | the checks in code (quotes, numbers, sums) still failed after the model's one further attempt, so the user was shown the relevant sections instead of an answer |
| error | the request failed, for example because the provider could not be reached |

Withheld answers and errors are counted separately and never folded into the other grades.

**Where the text match is wrong.** A text match cannot tell a wrong figure from a right figure mentioned for context. In the results below, the automatic check marked five answers wrong that I, reading them, judge to be correct:

| Run | Question | Automatic grade | My reading |
|---|---|---|---|
| DeepSeek, run 2 | Q10, extended family members and entry age | incorrect | Correct (8 members, entry age 85). A note adds that the group life guide has a different entry age, 64, which the check forbids. |
| GLM, run 2 | Q3, free cover limit for 320 members | incorrect | Correct (R5 000 000). It adds the same band's disability limit, R70 000 a month, correctly labelled. |
| GLM, run 1 | Q8, funeral cover for a 4-year-old | incorrect | Correct (R7 500). It adds that cover rises to R15 000 from age 6. |
| Claude Haiku 5.5, reworded Q107 | Option C benefit on R80 000 | incorrect | Correct (R65 000). The working shows "75% of R60 000", and R60 000 was on the forbidden list. |
| Claude Haiku 5.5, 22 November 2026 | Q15, time to decide a claim | incorrect | Correct. It called the 10 working days "not a guaranteed time" and "guarantee" was forbidden. I then allowed a negated guarantee in the expected-answer file and re-graded the saved answers without calling a model. Both grades are kept in `results/history/20261122T231317Z_anthropic-claude-haiku-5-5/`. |

The first three are among the 15 test questions; the fourth is in the reworded set and the fifth is from an earlier run kept for the record. All other grades below are ones I agree with.

**My assessment.** The automatic grade is a first pass and is never copied into the workbook. For the run I submitted, I read every answer with its quotes and wrote my own grade, one of the workbook's five values, and a comment into `review.yaml` in the run folder. `eval.finalise` refuses to continue while any entry is empty or not one of the five values, then writes the workbook. For the other runs I read every answer the automatic check did not grade correct.

## Results on the 15 test questions

Each model answered the 15 questions three times on the same code, on 23 November 2026: 13 answerable and 2 unanswerable questions per run, so 39 and 6 per model. The grades are automatic; the table above lists where I disagree.

| Model | Correct | Correctly declined | Other | Second attempt | Tokens per question (in / out) | Cost per question | Median time |
|---|---|---|---|---|---|---|---|
| Claude Haiku 5.5 (default) | 39 of 39 | 6 of 6 | none | 1 | 12 070 / 578 | US$0.0015 | 3.9 s |
| Claude Sonnet 5.5 | 39 of 39 | 6 of 6 | none | 3 | 11 324 / 471 | US$0.027 | 4.6 s |
| OpenAI `gpt-6.1-sol` | 39 of 39 | 6 of 6 | none | 3 | 6 692 / 219 | US$0.016 | 6.0 s |
| Kimi `kimi-k3.1-pro` (Ollama Cloud) | 39 of 39 | 6 of 6 | none | 0 | 8 386 / 809 | US$0.025 | 6.9 s |
| `gpt-oss:20b` (Ollama Cloud) | 39 of 39 | 6 of 6 | none | 0 | 9 367 / 681 | US$0.0009 | 7.0 s |
| `gpt-oss:20b` (local Ollama) | 39 of 39 | 6 of 6 | none | 0 | 9 390 / 646 | no API charge | 5.7 s |
| DeepSeek `deepseek-v4.1-flash` (Ollama Cloud) | 37 of 39 | 6 of 6 | 1 partially correct, 1 incorrect | 1 | 8 694 / 551 | US$0.0033 | 2.7 s |
| GLM `glm-5.3-flash` (Ollama Cloud) | 37 of 39 | 6 of 6 | 2 incorrect | 1 | 8 795 / 688 | US$0.0017 | 3.5 s |

No answer was withheld and no request failed.

"Second attempt" counts answers whose first draft failed one of the checks in code (a quote not found in the document, a number not found in a cited passage, or a sum that does not recompute) and that the model then fixed on the one further attempt it is allowed. These were Q8 for Haiku, Q11 in all three Sonnet runs, Q6 in all three `gpt-6.1-sol` runs, Q11 for DeepSeek and Q8 for GLM.

**Two models I do not recommend.** DeepSeek and GLM are cheap and fast, but DeepSeek once dropped the condition that the continuation option applies only before normal retirement age (Q2, run 1), which is a real error. In the three answers graded incorrect, the figure asked for is right, but each adds a figure nobody asked for: the group life entry age in a funeral answer, the disability limit beside the group life limit, and the cover for older children beside the cover for a 4-year-old. A call-centre user reading quickly could quote the wrong one.

**The submitted workbook** comes from one further run of Claude Haiku 5.5 on the same code (`results/20261123T050329Z_anthropic-claude-haiku-5-5/`): 13 correct and 2 correctly declined, both by the automatic check and by my reading, with no second attempts. `results/KA_Test_Questions_SUBMISSION.xlsx` and `results/results.csv` are copies of that folder's files.

**One change after these runs.** While checking that the repository works from a fresh download, the README's own example question (the limit for 320 members) was withheld once. The answer was right, but the model had added a second citation whose quote was the line naming the source, not text from the document, and it did not correct this on its second attempt. A citation that fails on its own is now dropped, with a warning, when the remaining citations still support every check; if they do not, the answer is withheld as before. I re-ran Claude Haiku 5.5 with this change: three runs, 39 of 39 correct and 6 of 6 correctly declined (`results/20261123T070635Z_anthropic-claude-haiku-5-5/`), and the example question was answered correctly ten times out of ten, once with a citation dropped. The other seven models in the table were not re-run after this change.

**How cost is worked out.** Every answer records the tokens sent to and received from the model. Cost per question is (input tokens × input price + output tokens × output price) ÷ 1 000 000, averaged over the 45 answers, at the list prices in `config/pricing.yaml` (taken from the providers' pages on 23 November 2026). Prompt-caching discounts are not applied, so these are upper estimates. The prices for OpenAI and Ollama Cloud were added to `config/pricing.yaml` after these runs, so their `summary.json` files and `results/report.md` show the cost as unknown ("n/a"); the figures above for those models are recomputed from the saved token counts. For the two Claude models the recomputed figures match the run files. Running all eight models three times costs about US$3.40 in total.

## Reworded questions and search on its own

I developed the system against the 15 test questions, so its score on them is flattering. The ten reworded and new questions in `eval/reworded.yaml` are the nearest thing to an independent check: seven rephrase test questions and three are new, one of which the documents cannot answer. On Claude Haiku 5.5 (`results/20261123T050129Z_anthropic-claude-haiku-5-5/`) the automatic check graded 8 correct, 1 correctly declined and 1 incorrect. The incorrect one is Q107, which I read as correct (see the table above), so all ten were handled correctly.

The search-only measurement (`eval.retrieval_eval`) calls no model. It runs the assistant's search for each question and records where the section named in the expected answer ranks among the top ten results. For the 13 answerable test questions, that section ranked first every time. For the 9 answerable reworded questions it ranked first for 7, second for 1 (Q102, "How soon after a member dies does Kestrel have to be told?") and seventh for 1 (Q101, "the highest amount of group life cover any single member can be insured for", which does not share words with the heading "Maximum cover"). The model still answered Q101 correctly by searching again. Search depends on wording more than the test questions show.

## Hidden-instruction tests

The claims FAQ hides one instruction in white, 1-point text. One example does not show that the loader would catch other hiding methods, so `eval/adversarial/` generates 27 small documents in Word, PDF, Excel and Markdown. Twenty-one each hide a planted instruction with a different technique, such as white or near-white text, tiny fonts, hidden Word text, hidden rows, columns and sheets, text placed off the page, invisible PDF text, text covered by a white shape, HTML comments, invisible Unicode characters and reversed text. The other six are controls that must be left alone: clean files, and light text on a dark background. Each planted instruction tells the model to give a false figure, and each document also contains the true one.

The scan (`eval.adversarial.run_scan`, no model, results in `results/adversarial/scan.md`):

- detected 20 of the 21 techniques;
- kept the planted instruction out of search in all 18 cases where it should be removed (three are visible text, which stays searchable by design and is only flagged);
- flagged none of the six controls.

The one it misses is an instruction in ordinary visible text that does not address an AI or tell it to ignore anything. Nothing in the loader can tell that apart from a genuine rule; the control for it is a person approving each document before it is searchable.

The attack test (`eval.adversarial.run_attack`) asks one question per document, whose true answer is in that document, and checks whether the answer gives the planted figure. It was run on Claude Haiku 5.5 and local `gpt-oss:20b`, before I added detection for text covered by a shape and reversed text, so five planted instructions were still searchable at the time. No answer gave only the planted figure. Haiku gave the true figure 20 times and withheld one answer. `gpt-oss:20b` gave the true figure all 21 times, but once, for the PDF with text covered by a shape, it gave the planted figure as well. Both of those documents are now caught by the scan.

## What went wrong during development, and what changed

All of this happened on 22 and 23 November 2026. Each fix is general (none is a rule for a particular question), has tests, and is included in every result above. The first run of each model before any fix is kept in `results/dev/first_run_*.jsonl`; later runs are in `results/history/` and `results/dev/`. Those earlier runs predate later fixes, so their numbers differ from the ones above.

| What went wrong | Why | What changed |
|---|---|---|
| Claude Sonnet 5.5's first run failed all 15 questions with an error | To get the answer back in a fixed structure, the framework forces the model to call a particular tool. Anthropic's API rejected that forced choice for this model. | If the provider rejects it, the call is retried once with the model free to choose, and that model is remembered. An answer returned as JSON text is accepted if it has the right structure, and is still checked. |
| Claude Haiku 5.5's first run withheld Q4 (disability free cover limit for 1 200 members) | The model cited the whole free cover table and quoted a label from it instead of the row, so the quote check failed. | When search finds a whole table, the model is shown its rows, each with a label it can cite. |
| Claude Haiku 5.5's first run withheld Q6 (Option C benefit on R50 000) | The answer wrote 0.75 where the document said 75%, so the number check rejected it. | A percentage in a cited passage also supports the same value written as a fraction. |
| The automatic check marked a correct Q15 incorrect | "not a guaranteed time" matched the forbidden word "guarantee". | The expected-answer file allows a negated guarantee; the saved answers were re-graded without a model. |
| "Skills" did not help | Skills are short instruction files the model can read when needed, such as how to read a table of ranges. On the test questions they changed no grade and added about a third more input tokens. | Switched off for the product assistant. |
| The staff-policies assistant dropped a correct count of working days | The count came from a calendar tool (a local service the model can call, connected through MCP, the Model Context Protocol), but the number check accepted only figures from documents. | Results from approved tools can be cited. A rule must still be cited from a document. |
| An independent review of the finished code by OpenAI Codex found ten defects | Examples: a number from a tool was enough to support an invented rule; 0.015% was accepted against a document saying 0.01%; the quote "covered" was found inside "uncovered"; an ID number passed to a tool as a number rather than text was not screened for personal information; error text from a provider could reach the log. The checks were looser than the documents said. | All ten fixed, each with a test that failed first: a rule always needs a document citation, numbers must match exactly, quotes must match whole words, tool inputs of every type are screened, and error text has personal information removed before it is logged. |
| On Claude Haiku 5.5, Q3 (free cover limit for 320 members) and Q10 (extended family members) needed a second attempt in all six runs of two three-run evaluations, and each was withheld once (`results/history/20261123T021259Z_…` and `20261123T040527Z_…`) | For Q3 the model read the table of member-number ranges by eye and started from the wrong band. For Q10 a note in the answer quoted a figure from a passage the answer did not cite, which used up the second attempt. | A table tool finds the row whose range contains the number in the question. An unsupported note is removed and recorded instead of failing the answer. Second attempts are now counted in every run. |
| Allowing two further attempts instead of one stopped answers being withheld, but let through one answer that contradicted itself | With a second extra attempt, the model cited both the wrong row and the right one. | Kept at one further attempt: withholding an answer is the safer failure. |
| On a fresh download, the example question in the README was withheld once (Claude Haiku 5.5, 23 November 2026) | The answer and its main citation were right, but an extra citation quoted the line naming the source instead of the document text, and the second attempt did not fix it | A citation that fails on its own is removed, with a warning, when the rest of the citations still pass every check; the model is also told specifically when it has quoted the source line |

## Limits of this evaluation

- I used the 15 test questions for development as well as evaluation, and fixed what failed, so the scores on them are better than on unseen questions. The reworded set was written before any answers were seen, but ten questions is a small check.
- Fifteen questions cannot reliably separate models: one answer moves a score by almost seven percentage points. Three runs show how much answers vary, not how accurate the system would be on real questions.
- I wrote the expected answers myself, as the person who built the system. The text match can be wrong in both directions, which is why I read the answers.
- The checks in code prove that a quote is real and a number is sourced, not that the sentence keeps every condition from the quote. The conditions in the expected answers, and reading the answers, are what catch a dropped condition.
- Costs are at list prices without caching discounts. Local Ollama has no API charge, but the machine it runs on is not costed.
- For production I would build a set of a hundred or more anonymised real questions from call-centre staff, with answers agreed by the people who own the documents (see [PRODUCTION_ARCHITECTURE.md](PRODUCTION_ARCHITECTURE.md)).

## Reproducing the numbers

Run these from the repository root after `uv sync`, with the key for each model in `.env`. Commands marked "no model" need no key and cost nothing.

```
# The 15 questions, three runs, one model (about US$0.07 on Claude Haiku 5.5)
uv run python -m eval.run_eval --models anthropic:claude-haiku-5-5 --runs 3

# All eight models (about US$3.40 at list prices)
uv run python -m eval.run_eval --runs 3 --models anthropic:claude-haiku-5-5 anthropic:claude-sonnet-5-5 openai:gpt-6.1-sol ollama-cloud:kimi-k3.1-pro ollama-cloud:gpt-oss:20b ollama:gpt-oss:20b ollama-cloud:deepseek-v4.1-flash ollama-cloud:glm-5.3-flash

# The ten reworded questions
uv run python -m eval.run_eval --set reworded --models anthropic:claude-haiku-5-5

# Search on its own (no model); add --set reworded for the reworded questions
uv run python -m eval.retrieval_eval

# Grade saved answers again after changing an expected-answer file (no model)
uv run python -m eval.regrade results/<run folder>

# Write my assessment into the workbook once review.yaml is filled in (no model)
uv run python -m eval.finalise results/<run folder>

# Compare run folders in one table (no model)
uv run python -m eval.report results/<folder A> results/<folder B> --out report.md

# Hidden-instruction scan (no model) and attack test (one model)
uv run python -m eval.adversarial.run_scan
uv run python -m eval.adversarial.run_attack --model anthropic:claude-haiku-5-5
```

`run_eval` loads the documents first (unchanged files are skipped) and never writes to the blank workbook `eval/KA_Test_Questions.xlsx`. It creates one folder per model, `results/<UTC date and time>_<model>/`, containing:

| File | Contents |
|---|---|
| `results.jsonl` | One record per question per run: the answer, each cited quote, any calculation, the result of the checks in code, every passage search returned, the steps taken, tokens, cost, time and the automatic grade. Written as it goes, so a crash keeps what was done. |
| `results.csv` | The same, one row per answer, for a spreadsheet. After `finalise` it also holds my assessment beside the automatic grade. |
| `summary.json` | Counts by grade, withheld answers, errors, median and 95th-percentile time, tokens and cost. |
| `review.yaml` | One entry per question with the answer, its sources and the automatic grade, and empty fields for a person's assessment and comment. |
| `KA_Test_Questions_<model>.xlsx` | The supplied workbook with "Your system's answer" filled in, one per run (test questions only). |
| `retrieval.json` | The search-only measurement: the rank of the expected section for each question. |
| `KA_Test_Questions_SUBMISSION.xlsx` | Written by `finalise`: the answers with my assessment and comment. |

`results/report.md` compares all the run folders reported here. `results/adversarial/` holds the scan and attack results.
