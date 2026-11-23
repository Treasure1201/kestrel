# AI tool disclosure

I directed the work and made the decisions; AI coding assistants did most of the writing. Claude, in Claude Code, acted as lead: it read the brief and the documents, wrote the design and the implementation plan, and reviewed and integrated every change. Before any code was written I asked a second model, OpenAI Codex, for an independent critical review of the design. Its findings changed the design, including the plan's first milestone (a complete, submittable slice) and the wording of the evidence checks.

Implementation was done by AI sub-agents (Claude Opus), one work package each, in an isolated git worktree with fixed file ownership, coding against contracts frozen up front. Each handed back a report with test results, which the lead checked before merging.

**What I decided.** SQLite with sqlite-vec and no Docker; three front ends over one service; a working second bot to prove that bots are configuration; the model matrix. I approved a mock-up of the Streamlit app before it was built, rejected parts of the first build (scrolling, the readability of the evidence panel, the colour scheme), which were reworked, and asked for the six data items in the mock-up to be completed. I asked how hidden instructions were handled, which led to the adversarial test set.

**Where the AI was wrong, and what was done.**

- The first design called answers "verified", but an answer could pass every check while dropping a condition; it became "evidence checks passed" and the gap is measured in evaluation.
- Claude Sonnet failed all 15 first-run questions because the provider rejected a forced tool choice; fixed with a general retry.
- The automatic grader marked a correct answer wrong for saying the target was "not a guaranteed time".
- Skills for the product bot changed no grade and cost about a third more input tokens, so they were switched off.
- The evidence checks stripped a correct figure from an approved tool until tool results were made citable.
- The operator role was the same as the staff role until review.
- The scanner missed three hiding techniques in the adversarial set; two are now fixed.

Fixes were general and covered by tests, never rules for one question.
