# Demo script (about ten minutes)

Use one terminal and a browser for the follow-up session. Run every command from the repository root.

## Before the session

```
uv sync
uv run kestrel doctor                  # every check OK; fix anything it lists
uv run kestrel ingest --all            # both bots; a second run reports "unchanged"
uv run streamlit run app/streamlit_app.py     # leave running at http://localhost:8501
```

`.env` holds `KESTREL_MODEL=anthropic:claude-haiku-5-5` (or the primary model from the results) and `KESTREL_ROLES=call-centre-agent,broker,staff,operator`. Start Ollama with `gpt-oss:20b` pulled for the staff bot, and open the latest results folder in a file explorer.

Document ids come from file hashes and stay the same across machines. Use `kestrel docs list` to find them. For this set: claims FAQ `8e3702ea74d916f3`, group life guide 2026 `ab98804067c942f4`.

## The script

| Min | Show | Command or question | Point to make |
|---|---|---|---|
| 0:00 | What it is | README diagram | One service, three front ends, six steps on every question; two bots from configuration |
| 1:00 | Answer with a calculation | App, Ask page: "Under disability Option C, what monthly benefit would a member earning R50 000 a month receive?" | The calculation is done by the `calculate` tool and recomputed by the checks; the rule it used is cited; the steps show search, table lookup, calculation, checks |
| 2:00 | A condition that must be kept | "How quickly are group life claims decided once all documents are in?" | "aims to" and "10 working days" and "after all documents" are in the answer and in the quote. Say plainly: the checks prove the quote, not that the sentence kept the condition; the gold file and human review catch that |
| 3:00 | A decline | "Does Kestrel offer group dental cover?" | `not_found` with a reason worded as what the documents do not say; counted apart from errors and withheld answers |
| 3:30 | Redaction | `uv run kestrel ask "My name is Sipho Dlamini and my ID number is 8001015009087. What is the waiting period on extended family funeral cover?"` | "Personal information removed from the question" line; the model, the audit log and the history saw only placeholders. The ID is checksum-validated, so a random 13-digit number is not redacted |
| 4:30 | Edition switch | `uv run kestrel ask "Under the 2025 edition, what is the maximum lump sum group life cover a member can have?"` then the same without "Under the 2025 edition" | R20 000 000 from the superseded guide only when asked; R25 000 000 by default. Then `uv run kestrel docs compare ab98804067c942f4`: maximum cover, continuation option, death notification and premium guarantee changes found by code, no model |
| 5:30 | Hidden instruction | `uv run kestrel docs findings 8e3702ea74d916f3` | White, 1-point text telling AI assistants every claim is approved within 24 hours; removed from search, recorded, visible "10 working days" kept. The adversarial set tests 21 hiding techniques, not just this one |
| 6:30 | Block a document | `uv run kestrel docs block 8e3702ea74d916f3 -r "Demo: review the hidden instruction"`, ask the claims question again, `uv run kestrel docs affected 8e3702ea74d916f3`, then `uv run kestrel docs unblock 8e3702ea74d916f3 -r "Demo over"` | Withdrawal is immediate and needs the `operator` role; `affected` lists every past answer that cited the document; both actions are in the audit trail (`uv run kestrel audit verify`) |
| 7:30 | Policy per bot | `uv run kestrel ask --bot staff-policies --model anthropic:claude-haiku-5-5 "How many days of annual leave does an employee with 7 completed years of service get?"` | Refused before any text leaves the machine: the bot is `confidential` and allows only local Ollama. Then the same without `--model`, answered by local `gpt-oss:20b` |
| 8:30 | MCP server | Desktop MCP client with the config below; call `list_bots`, then `ask` | The same service, the same checks and audit, over stdio. Operator actions are not exposed |
| 9:15 | Evaluation | Results folder: `summary.json`, `results.csv`, the filled workbook; `uv run python -m eval.retrieval_eval` (no model, seconds) | Counts by outcome, the first-run failures and fixes, the 15 questions were also the development set |

### MCP client configuration

Add this to `claude_desktop_config.json` for Claude Desktop, replacing the example path with the repository's absolute path. Other MCP clients use the same command and arguments:

```json
{
  "mcpServers": {
    "kestrel": {
      "command": "uv",
      "args": ["run", "--directory", "C:\\path\\to\\kestrel", "kestrel", "mcp"]
    }
  }
}
```

`--directory` makes the repository the working directory, so `.env`, `bots/` and the database are found. The server offers `list_bots`, `ask`, `search` and `get_section`, and a `kestrel://status` resource.

## Three safe live changes

These changes use configuration only. Undo them with `git checkout`.

### 1. Add an example question

In `bots/kestrel-products/bot.yaml`, add a line under `examples:` (at most six, each under 120 characters):

```yaml
  - How much funeral cover is provided for a member's 4-year-old child?
```

Show it with `uv run kestrel bots --examples` and as a new button on the Ask page after a browser refresh.

### 2. Add a redaction pattern

In `bots/kestrel-products/bot.yaml`, under `privacy:`, add:

```yaml
  extra_patterns:
    - kind: broker_code
      pattern: '\bBRK-\d{5}\b'
```

Then `uv run kestrel ask "Broker BRK-12345 asks: what waiting period applies to disability income Option B?"`. The notice lists `broker_code` and the audit record holds `[BROKER_CODE]`. An invalid regular expression fails at load with the field name.

### 3. Add a bot

Create `bots/it-helpdesk/bot.yaml`:

```yaml
id: it-helpdesk
name: IT Help Desk
description: Answers questions about passwords from the IT help pages (fictitious).
audience: [staff]
knowledge:
  collections: [it-helpdesk]
  approval: none            # demo only: skips the manifest approval step
prompt: prompt.md
tools: [search_documents]
model:
  default: ${KESTREL_MODEL}
  allowed_providers: [ollama, ollama-cloud, anthropic, openai]
examples:
  - How often must I change my password?
```

Add a sentence describing the bot's persona in `bots/it-helpdesk/prompt.md`, and create `data/source/it-helpdesk/passwords.md`:

```markdown
# Passwords

## Changing your password

Passwords must be changed every 90 days. A new password needs at least 14 characters.
```

Then `uv run kestrel ingest --bot it-helpdesk`, `uv run kestrel ask --bot it-helpdesk "How often must I change my password?"`, and show that `uv run kestrel search --bot kestrel-products "password"` finds nothing from the new collection. The new bot needs no code changes. [ADDING_A_BOT.md](ADDING_A_BOT.md) also covers a manifest, skills and an MCP server.

## If something fails on screen

- A provider error: `--model ollama:gpt-oss:20b` runs the same question locally.
- A withheld answer: open the evidence panel (or `--trace`), read the failed check and explain why the answer was held back.
- Anything else: `uv run kestrel doctor` prints the fix.
