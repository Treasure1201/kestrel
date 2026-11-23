# Evaluation comparison

Grades are automatic string-matching checks against the gold file, not human assessments.
The human review column counts the assessments a person entered, where a review is complete.

| Run folder | Model | Runs | Answers | correct | partial | incorrect | declined ok | declined wrongly | withheld | error | Retrieval misses | Cost per question (USD) | Median latency (ms) | Human review |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 20261123T044923Z_anthropic-claude-haiku-5-5 | anthropic:claude-haiku-5-5 | 3 | 45 | 39 | 0 | 0 | 6 | 0 | 0 | 0 | 0 | 0.0015 | 3947 | not reviewed |
| 20261123T044923Z_anthropic-claude-sonnet-5-5 | anthropic:claude-sonnet-5-5 | 3 | 45 | 39 | 0 | 0 | 6 | 0 | 0 | 0 | 0 | 0.0274 | 4647 | not reviewed |
| 20261123T044923Z_ollama-cloud-kimi-k3-1-pro | ollama-cloud:kimi-k3.1-pro | 3 | 45 | 39 | 0 | 0 | 6 | 0 | 0 | 0 | 0 | n/a (45 unpriced) | 6896 | not reviewed |
| 20261123T044923Z_ollama-gpt-oss-20b | ollama:gpt-oss:20b | 3 | 45 | 39 | 0 | 0 | 6 | 0 | 0 | 0 | 0 | n/a (45 unpriced) | 5679 | not reviewed |
| 20261123T044923Z_openai-gpt-6-1-sol | openai:gpt-6.1-sol | 3 | 45 | 39 | 0 | 0 | 6 | 0 | 0 | 0 | 0 | n/a (45 unpriced) | 6046 | not reviewed |
| 20261123T044924Z_ollama-cloud-deepseek-v4-1-flash | ollama-cloud:deepseek-v4.1-flash | 3 | 45 | 37 | 1 | 1 | 6 | 0 | 0 | 0 | 0 | n/a (45 unpriced) | 2665 | not reviewed |
| 20261123T044924Z_ollama-cloud-glm-5-3-flash | ollama-cloud:glm-5.3-flash | 3 | 45 | 37 | 0 | 2 | 6 | 0 | 0 | 0 | 0 | n/a (45 unpriced) | 3523 | not reviewed |
| 20261123T044924Z_ollama-cloud-gpt-oss-20b | ollama-cloud:gpt-oss:20b | 3 | 45 | 39 | 0 | 0 | 6 | 0 | 0 | 0 | 0 | n/a (45 unpriced) | 6960 | not reviewed |
| 20261123T050129Z_anthropic-claude-haiku-5-5 | anthropic:claude-haiku-5-5 | 1 | 10 | 8 | 0 | 1 | 1 | 0 | 0 | 0 | 0 | 0.0014 | 3788 | not reviewed |
| 20261123T050329Z_anthropic-claude-haiku-5-5 | anthropic:claude-haiku-5-5 | 1 | 15 | 13 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 0.0015 | 4346 | Correct: 13, Partially correct: 0, Incorrect: 0, Correctly declined: 2, Incorrectly declined: 0 |
| 20261123T070635Z_anthropic-claude-haiku-5-5 | anthropic:claude-haiku-5-5 | 3 | 45 | 39 | 0 | 0 | 6 | 0 | 0 | 0 | 0 | 0.0014 | 3830 | not reviewed |

## Per question (automatic grade per run)

| Question | 20261123T044923Z_anthropic-claude-haiku-5-5 | 20261123T044923Z_anthropic-claude-sonnet-5-5 | 20261123T044923Z_ollama-cloud-kimi-k3-1-pro | 20261123T044923Z_ollama-gpt-oss-20b | 20261123T044923Z_openai-gpt-6-1-sol | 20261123T044924Z_ollama-cloud-deepseek-v4-1-flash | 20261123T044924Z_ollama-cloud-glm-5-3-flash | 20261123T044924Z_ollama-cloud-gpt-oss-20b | 20261123T050129Z_anthropic-claude-haiku-5-5 | 20261123T050329Z_anthropic-claude-haiku-5-5 | 20261123T070635Z_anthropic-claude-haiku-5-5 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Q1 | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | - | correct | correct / correct / correct |
| Q2 | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | partial / correct / correct | correct / correct / correct | correct / correct / correct | - | correct | correct / correct / correct |
| Q3 | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / incorrect / correct | correct / correct / correct | - | correct | correct / correct / correct |
| Q4 | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | - | correct | correct / correct / correct |
| Q5 | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | - | correct | correct / correct / correct |
| Q6 | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | - | correct | correct / correct / correct |
| Q7 | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | - | correct | correct / correct / correct |
| Q8 | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | incorrect / correct / correct | correct / correct / correct | - | correct | correct / correct / correct |
| Q9 | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | - | correct | correct / correct / correct |
| Q10 | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / incorrect / correct | correct / correct / correct | correct / correct / correct | - | correct | correct / correct / correct |
| Q11 | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | - | correct | correct / correct / correct |
| Q12 | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | - | correct | correct / correct / correct |
| Q13 | declined ok / declined ok / declined ok | declined ok / declined ok / declined ok | declined ok / declined ok / declined ok | declined ok / declined ok / declined ok | declined ok / declined ok / declined ok | declined ok / declined ok / declined ok | declined ok / declined ok / declined ok | declined ok / declined ok / declined ok | - | declined ok | declined ok / declined ok / declined ok |
| Q14 | declined ok / declined ok / declined ok | declined ok / declined ok / declined ok | declined ok / declined ok / declined ok | declined ok / declined ok / declined ok | declined ok / declined ok / declined ok | declined ok / declined ok / declined ok | declined ok / declined ok / declined ok | declined ok / declined ok / declined ok | - | declined ok | declined ok / declined ok / declined ok |
| Q15 | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | correct / correct / correct | - | correct | correct / correct / correct |
| Q101 | - | - | - | - | - | - | - | - | correct | - | - |
| Q102 | - | - | - | - | - | - | - | - | correct | - | - |
| Q103 | - | - | - | - | - | - | - | - | correct | - | - |
| Q104 | - | - | - | - | - | - | - | - | correct | - | - |
| Q105 | - | - | - | - | - | - | - | - | correct | - | - |
| Q106 | - | - | - | - | - | - | - | - | declined ok | - | - |
| Q107 | - | - | - | - | - | - | - | - | incorrect | - | - |
| Q108 | - | - | - | - | - | - | - | - | correct | - | - |
| Q109 | - | - | - | - | - | - | - | - | correct | - | - |
| Q110 | - | - | - | - | - | - | - | - | correct | - | - |

`*` the expected section was never retrieved (retrieval miss).

## Retrieval (search only, no model, top 10)

| Run folder | Questions with a target | Hit@1 | Hit@3 | Hit@6 | MRR |
|---|---|---|---|---|---|
| 20261123T050129Z_anthropic-claude-haiku-5-5 | 9 | 7/9 (78%) | 8/9 (89%) | 8/9 (89%) | 0.849 |
| 20261123T050329Z_anthropic-claude-haiku-5-5 | 13 | 13/13 (100%) | 13/13 (100%) | 13/13 (100%) | 1.000 |

Hit@k: share of questions whose expected section ranks k or better. Unanswerable questions have no target section.
