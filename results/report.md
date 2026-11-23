# Evaluation comparison

Grades are automatic string-matching checks against the gold file, not human assessments.
The human review column counts the assessments a person entered, where a review is complete.

| Run folder | Model | Runs | Answers | correct | partial | incorrect | declined ok | declined wrongly | withheld | error | Retrieval misses | Cost per question (USD) | Median latency (ms) | Human review |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 20261123T020503Z_anthropic-claude-haiku-5-5 | anthropic:claude-haiku-5-5 | 1 | 15 | 13 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 0.0015 | 3585 | not reviewed |
| 20261123T020618Z_ollama-gpt-oss-20b | ollama:gpt-oss:20b | 1 | 6 | 4 | 1 | 1 | 0 | 0 | 0 | 0 | 0 | n/a (6 unpriced) | 8730 | not reviewed |
| 20261123T021259Z_anthropic-claude-haiku-5-5 | anthropic:claude-haiku-5-5 | 3 | 45 | 39 | 0 | 0 | 6 | 0 | 0 | 0 | 0 | 0.0015 | 4270 | not reviewed |
| 20261123T021639Z_anthropic-claude-sonnet-5-5 | anthropic:claude-sonnet-5-5 | 1 | 15 | 13 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 0.0288 | 5887 | not reviewed |
| 20261123T021639Z_ollama-cloud-deepseek-v4-1-flash | ollama-cloud:deepseek-v4.1-flash | 1 | 15 | 13 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | n/a (15 unpriced) | 4372 | not reviewed |
| 20261123T021639Z_ollama-cloud-gpt-oss-20b | ollama-cloud:gpt-oss:20b | 1 | 15 | 13 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | n/a (15 unpriced) | 6365 | not reviewed |
| 20261123T021639Z_ollama-cloud-kimi-k3-1-pro | ollama-cloud:kimi-k3.1-pro | 1 | 15 | 13 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | n/a (15 unpriced) | 13932 | not reviewed |
| 20261123T021639Z_ollama-gpt-oss-20b | ollama:gpt-oss:20b | 1 | 15 | 13 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | n/a (15 unpriced) | 3530 | not reviewed |
| 20261123T021639Z_openai-gpt-6-1-sol | openai:gpt-6.1-sol | 1 | 15 | 13 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | n/a (15 unpriced) | 6468 | not reviewed |
| 20261123T022843Z_anthropic-claude-haiku-5-5 | anthropic:claude-haiku-5-5 | 1 | 10 | 8 | 0 | 1 | 1 | 0 | 0 | 0 | 0 | 0.0015 | 4471 | not reviewed |

## Per question (automatic grade per run)

| Question | 20261123T020503Z_anthropic-claude-haiku-5-5 | 20261123T020618Z_ollama-gpt-oss-20b | 20261123T021259Z_anthropic-claude-haiku-5-5 | 20261123T021639Z_anthropic-claude-sonnet-5-5 | 20261123T021639Z_ollama-cloud-deepseek-v4-1-flash | 20261123T021639Z_ollama-cloud-gpt-oss-20b | 20261123T021639Z_ollama-cloud-kimi-k3-1-pro | 20261123T021639Z_ollama-gpt-oss-20b | 20261123T021639Z_openai-gpt-6-1-sol | 20261123T022843Z_anthropic-claude-haiku-5-5 |
|---|---|---|---|---|---|---|---|---|---|---|
| Q1 | correct | correct | correct / correct / correct | correct | correct | correct | correct | correct | correct | - |
| Q2 | correct | correct | correct / correct / correct | correct | correct | correct | correct | correct | correct | - |
| Q3 | correct | correct | correct / correct / correct | correct | correct | correct | correct | correct | correct | - |
| Q4 | correct | incorrect | correct / correct / correct | correct | correct | correct | correct | correct | correct | - |
| Q5 | correct | partial | correct / correct / correct | correct | correct | correct | correct | correct | correct | - |
| Q6 | correct | correct | correct / correct / correct | correct | correct | correct | correct | correct | correct | - |
| Q7 | correct | - | correct / correct / correct | correct | correct | correct | correct | correct | correct | - |
| Q8 | correct | - | correct / correct / correct | correct | correct | correct | correct | correct | correct | - |
| Q9 | correct | - | correct / correct / correct | correct | correct | correct | correct | correct | correct | - |
| Q10 | correct | - | correct / correct / correct | correct | correct | correct | correct | correct | correct | - |
| Q11 | correct | - | correct / correct / correct | correct | correct | correct | correct | correct | correct | - |
| Q12 | correct | - | correct / correct / correct | correct | correct | correct | correct | correct | correct | - |
| Q13 | declined ok | - | declined ok / declined ok / declined ok | declined ok | declined ok | declined ok | declined ok | declined ok | declined ok | - |
| Q14 | declined ok | - | declined ok / declined ok / declined ok | declined ok | declined ok | declined ok | declined ok | declined ok | declined ok | - |
| Q15 | correct | - | correct / correct / correct | correct | correct | correct | correct | correct | correct | - |
| Q101 | - | - | - | - | - | - | - | - | - | correct |
| Q102 | - | - | - | - | - | - | - | - | - | correct |
| Q103 | - | - | - | - | - | - | - | - | - | correct |
| Q104 | - | - | - | - | - | - | - | - | - | correct |
| Q105 | - | - | - | - | - | - | - | - | - | correct |
| Q106 | - | - | - | - | - | - | - | - | - | declined ok |
| Q107 | - | - | - | - | - | - | - | - | - | incorrect |
| Q108 | - | - | - | - | - | - | - | - | - | correct |
| Q109 | - | - | - | - | - | - | - | - | - | correct |
| Q110 | - | - | - | - | - | - | - | - | - | correct |

`*` the expected section was never retrieved (retrieval miss).

## Retrieval (search only, no model, top 10)

| Run folder | Questions with a target | Hit@1 | Hit@3 | Hit@6 | MRR |
|---|---|---|---|---|---|
| 20261123T020503Z_anthropic-claude-haiku-5-5 | 13 | 13/13 (100%) | 13/13 (100%) | 13/13 (100%) | 1.000 |
| 20261123T020618Z_ollama-gpt-oss-20b | 5 | 4/5 (80%) | 5/5 (100%) | 5/5 (100%) | 0.900 |
| 20261123T021259Z_anthropic-claude-haiku-5-5 | 13 | 13/13 (100%) | 13/13 (100%) | 13/13 (100%) | 1.000 |
| 20261123T022843Z_anthropic-claude-haiku-5-5 | 9 | 7/9 (78%) | 8/9 (89%) | 8/9 (89%) | 0.849 |

Hit@k: share of questions whose expected section ranks k or better. Unanswerable questions have no target section.
