# Adding a bot

A bot is a folder of configuration plus a folder of documents. No code changes are needed. This guide uses the demonstration bot `staff-policies` (three fictitious Kestrel Assurance staff policies) as the worked example.

```mermaid
flowchart LR
    D["data/source/staff-policies/<br/>the documents"] --> I["kestrel ingest<br/>parse, scan, manifest check"]
    M["bots/staff-policies/manifest.yaml<br/>approved files and hashes"] --> I
    I --> DB[("SQLite<br/>collection: staff-policies")]
    Y["bots/staff-policies/bot.yaml<br/>who, what, which model, which tools"] --> A["Agent for this bot"]
    P["prompt.md and skills/"] --> A
    C["config/mcp_servers.yaml<br/>approved servers"] --> A
    DB --> A
    A --> Q["kestrel ask --bot staff-policies"]
```

## 1. Create the folders

```
bots/staff-policies/            bot.yaml, prompt.md, manifest.yaml, skills/, eval/gold.yaml
data/source/staff-policies/     the documents (DOCX, PDF, XLSX or Markdown)
```

The bot id, the folder name and the `id:` field must be the same: lower-case words joined by hyphens.

## 2. Drop in the documents

Put the files in `data/source/<collection>/`. Headings and numbered sections become the citation locators (`section 2 "Annual leave", table 1`), so documents with clear headings give clearer citations. Tables are kept whole and also row by row, which is what lets the assistant cite the one row it relied on.

## 3. Write `bot.yaml`

| Field | What it does | `staff-policies` |
|---|---|---|
| `id`, `name`, `description` | Identity shown in every front end | `staff-policies`, "Staff Policy Helper" |
| `audience` | Roles allowed to see and use the bot; checked on every call | `[staff]` |
| `knowledge.collections` | The only documents the bot can search (filtered in SQL) | `[staff-policies]` |
| `knowledge.approval` | `required`: a file is searchable only if listed in the manifest with its hash. `none`: every file is searchable | `required` |
| `prompt` | Persona and domain guidance, appended to the shared core rules | `prompt.md` |
| `tools` | Built-in read-only tools: `search_documents`, `get_table`, `calculate` | all three |
| `mcp_servers` | Names of centrally approved servers from `config/mcp_servers.yaml` | `[workday-calendar]` |
| `skills` | Folders holding `<skill>/SKILL.md` procedures, read-only | `[skills/]` |
| `model.default` | `<provider>:<model>`; `${KESTREL_MODEL}` uses the global setting | `ollama:gpt-oss:20b` |
| `model.allowed_providers` | Providers this bot may send text to | `[ollama]` |
| `model.max_model_calls`, `max_searches`, `timeout_s` | Budget per question | 12, 6, 300 s |
| `privacy.classification` | `confidential` allows on-device destinations only (models and MCP servers) | `confidential` |
| `privacy.on_personal_information` | `redact` replaces personal details; `block` refuses the question | `block` |
| `privacy.audit_question_text` | `redacted` stores the screened question; `none` stores only a digest | `none` |
| `privacy.retention_days` | Audit rows older than this are pruned | 30 |
| `verification.on_failure` | `withhold` hides an answer that fails the evidence checks; `warn` shows it with a warning | `withhold` |
| `evaluation.gold` | Gold answers for `eval.run_eval` | `eval/gold.yaml` |

Unknown fields are rejected with the field name, so a typo fails loudly. (There is no `examples:` field.)

## 4. Approve the documents

With `approval: required`, list each file in `manifest.yaml` with its SHA-256, a family, an edition and an effective date. A file whose bytes change no longer matches and drops to `pending` until someone re-approves it. Compute a hash with:

```
uv run python -c "import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" data/source/staff-policies/KA_Staff_Leave_Policy.md
```

Use `approval: none` only for scratch or test bots: it skips the human approval step.

## 5. Optional: skills and MCP servers

- **Skill**: `bots/<id>/skills/<skill-name>/SKILL.md`, starting with front matter whose `name` equals the folder name and a one-line `description`. The staff bot's `leave-entitlement` skill explains how to pick the right band from the leave table. Skills are guidance, never evidence.
- **MCP server**: a person first approves it in `config/mcp_servers.yaml` (command, allowed tools, `on-device` or `external`, and a read-only statement). The bot then opts in by name. A `confidential` bot refuses an `external` server.

## 6. Ingest and ask

```
uv run kestrel ingest --bot staff-policies
uv run kestrel ask --bot staff-policies "How many days of annual leave does an employee with 7 completed years of service get?"
```

Ingest reports each file's status (`current`, `pending`, `blocked`) and any hidden-text findings.

## 7. Evaluate

Write `eval/gold.yaml` in the same format as the product bot's (expected status, facts, conditions, forbidden statements, expected source), then:

```
uv run python -m eval.run_eval --bot staff-policies --set reworded --gold bots/staff-policies/eval/gold.yaml --models ollama:gpt-oss:20b
```

## Things to know

- A question containing a full date ("11 March 2026") or a bare year is answered with a request to clarify whether it means an edition. Write dates without the year, or say "as of 2026-03-11".
- A number that comes only from an MCP tool (for example a working-day count) is not in any cited excerpt, so the evidence checks reject it and a `withhold` bot hides or drops it. Quote the policy rule; treat the tool result as context.
- The `staff` role is also the operator role (ingest, block, audit). Choose a different audience role for a bot that operators should not automatically see.
