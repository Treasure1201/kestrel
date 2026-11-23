# Demo script

A ten-minute demonstration for the follow-up session, an MCP client set-up, and three small changes that are safe to make live. Every command runs from the repository root.

## Before the session

```
uv sync
uv run kestrel doctor                          # every line should say OK; it prints the fix for anything else
uv run kestrel ingest --all                    # a second run reports "9 file(s): 9 unchanged"
uv run streamlit run app/streamlit_app.py      # leave running; the web app is at http://localhost:8501
```

`.env` should hold `KESTREL_MODEL=anthropic:claude-haiku-5-5` with `ANTHROPIC_API_KEY`, and `KESTREL_ROLES=call-centre-agent,broker,staff,operator` (as in `.env.example`). The `staff` role lets me use the second assistant and `operator` lets me withdraw a document. Start Ollama with `gpt-oss:20b` pulled, because the second assistant may only use a model on this machine.

**Cost.** Steps 2 to 6 and step 10 each ask Claude Haiku 5.5 one question, at about US$0.0015 a question, so the whole demo costs well under US$0.02. Every other step calls no model or uses the local one. Adding `--model ollama:gpt-oss:20b` to any `ask` runs it locally at no charge.

Document IDs come from a hash of each file, so they are the same on every machine. `uv run kestrel docs list` shows them. The two used below are the claims FAQ, `8e3702ea74d916f3`, and the 2026 group life guide, `ab98804067c942f4`.

## The script

The code calls a configured assistant a "bot", so some commands say `bot`.

**1. What it is (0:00).** Show the diagram in the README.
Point: the model answers with three tools (search, read a table row, calculate), and ordinary code then checks every quote, number and sum before the user sees the answer. The same service sits behind the command line, the web app and the MCP server.

**2. A calculation (1:00).** In the web app, Ask page, type: *Under disability Option C, what monthly benefit would a member earning R50 000 a month receive?*
The audience sees R42 500 a month, the Option C row quoted from the disability guide, and the steps in the evidence pane: search, table lookup, calculation, checks.
Point: the arithmetic (R20 000 + 75% of R30 000) is done by the calculator tool and recomputed by the checks, not done by the model in its head.

**3. A condition that must survive (2:00).** Type: *How long does Kestrel take to make a decision on a group life claim?*
The audience sees that Kestrel *aims* to decide within 10 working days *of receiving all required documents*, with the quote from the claims FAQ.
Point: the checks prove the quote is real, not that the sentence kept "aims", "working" and "all documents". My expected answers for the evaluation list those conditions, and I read every answer, for exactly that reason.

**4. A question the documents cannot answer (3:00).** Type: *Does Kestrel offer group dental cover?*
The audience sees that the documents do not say, with no guess either way.
Point: a decline is counted separately from withheld answers and errors in the evaluation.

**5. Personal information (3:30).** In the terminal:

```
uv run kestrel ask "My name is Sipho Dlamini and my ID number is 8001015009087. What is the waiting period on extended family funeral cover?"
```

The audience sees "Personal information removed from the question: 2 items (name, sa_id)" and then the answer: 6 months for death from natural causes, accidental death covered immediately.
Point: the name and ID number were replaced before the question reached the model or the log. ID numbers are recognised by their check digit, so a random 13-digit number is left alone. This is pattern matching and will miss an unlabelled name.

**6. Two editions of the group life guide (4:30).**

```
uv run kestrel ask "Under the 2025 edition, what is the maximum lump sum group life cover a member can have?"
uv run kestrel docs compare ab98804067c942f4
```

The answer is R20 000 000 from the 2025 guide, because the question asked for that edition; without "Under the 2025 edition" it is R25 000 000 from the 2026 guide (test question Q1). `docs compare` then lists, with no model, the values that changed between the editions: maximum cover, the continuation option's period and limit, the death notification period and the premium guarantee.
Point: which edition is current is recorded in a file a person maintains (`bots/kestrel-products/manifest.yaml`), not guessed by the model.

**7. The hidden instruction (5:30).**

```
uv run kestrel docs findings 8e3702ea74d916f3
```

The audience sees the white, 1-point text in the claims FAQ, which tells AI assistants to say every claim is approved within 24 hours with no documents, recorded as removed from search.
Point: the loader removes text a reader cannot see and keeps the visible "10 working days". The evaluation tests 21 hiding techniques, not just this one, and misses one: a plain visible instruction.

**8. Withdrawing a document (6:30).**

```
uv run kestrel docs block 8e3702ea74d916f3 -r "Demo: review the hidden instruction"
uv run kestrel search "how long does it take to assess a claim"
uv run kestrel docs affected 8e3702ea74d916f3
uv run kestrel docs unblock 8e3702ea74d916f3 -r "Demo over"
uv run kestrel audit verify
```

After `block`, search no longer returns anything from the claims FAQ. `affected` lists every earlier answer that cited it. `audit verify` reports the log intact.
Point: withdrawal takes effect immediately and needs the operator role; both actions are in a log in which each record carries a hash of the one before, so an edited record is detected.

**9. A second assistant with stricter rules (7:30).**

```
uv run kestrel ask --bot staff-policies --model anthropic:claude-haiku-5-5 "How many days of annual leave does an employee with 7 completed years of service get?"
uv run kestrel ask --bot staff-policies "How many days of annual leave does an employee with 7 completed years of service get?"
```

The first is refused before any text leaves the machine (no charge): this assistant is marked confidential and allows only local Ollama. The second is answered by local `gpt-oss:20b`: 21 working days, quoting the row for 5 to 9 years of service.
Point: the second assistant is a folder of configuration, with its own documents, audience, model rules and privacy rules, and no code of its own.

**10. MCP server (8:30).** In Claude Desktop, configured as below, ask it to list the Kestrel assistants (the `list_bots` tool) and then to ask the product assistant a question (the `ask` tool, a paid call).
Point: MCP (Model Context Protocol) lets other AI tools call this assistant. They get the same service, checks and log. The commands that withdraw documents are not offered over MCP.

**11. The evaluation (9:15).** In the web app, open "Quality and audit", then the "Evaluation runs" tab, or open `results/` and show a run folder's `summary.json`, `results.csv` and filled workbook. Then:

```
uv run python -m eval.retrieval_eval           # no model, a few seconds
```

Point: the 15 questions were also what I developed against, so I wrote ten reworded questions before seeing any answers; search ranked the right section first for 7 of the 9 answerable ones. [EVALUATION.md](EVALUATION.md) has the failures and what changed.

## MCP client configuration

For Claude Desktop, add this to `claude_desktop_config.json`, replacing the path with the repository's absolute path. Other MCP clients take the same command and arguments.

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

`--directory` makes the repository the working directory, so `.env`, `bots/` and the database are found. The server offers four tools, `list_bots`, `ask`, `search` and `get_section`, and a `kestrel://status` resource that summarises `doctor`.

## Three safe live changes

Each changes configuration only. The registry notices a changed assistant file on the next request, so the command line picks it up at once and the web app after a browser refresh.

### 1. Add an example question

In `bots/kestrel-products/bot.yaml`, add a line at the end of the `examples:` list (at most six, each under 120 characters):

```yaml
  - How much funeral cover is provided for a member's 4-year-old child?
```

Show it: `uv run kestrel bots --examples` lists it, and after a browser refresh it is a new button on the Ask page. Clicking it is a paid call; the answer is R7 500, from the row for children under 6.
Undo: `git checkout -- bots/kestrel-products/bot.yaml`

### 2. Add a pattern for personal information

In `bots/kestrel-products/bot.yaml`, add these lines at the end of the `privacy:` block, directly after `retention_days: 30`:

```yaml
  extra_patterns:
    - kind: broker_code
      pattern: '\bBRK-\d{5}\b'
```

Show it, locally at no charge:

```
uv run kestrel ask --model ollama:gpt-oss:20b "Broker BRK-12345 asks: what waiting period applies to disability income Option B?"
uv run kestrel audit tail -n 1
```

The answer starts with "Personal information removed from the question: 1 item (broker_code)" and gives 6 months. The log shows the question as "Broker [BROKER_CODE] asks: …".
Undo: `git checkout -- bots/kestrel-products/bot.yaml`

Keep the pattern in single quotes. A pattern that is not a valid regular expression stops every command with "unexpected error" (`--debug` shows the cause), so undo with `git checkout` if that happens.

### 3. Add an assistant

Create `bots/it-helpdesk/bot.yaml`:

```yaml
id: it-helpdesk
name: IT Help Desk
description: Answers questions about passwords from the IT help pages (fictitious).
audience: [staff]
knowledge:
  collections: [it-helpdesk]
  approval: none            # demo only: no list of approved documents needed
prompt: prompt.md
tools: [search_documents]
model:
  default: ${KESTREL_MODEL}
  allowed_providers: [ollama, ollama-cloud, anthropic, openai]
examples:
  - How often must I change my password?
```

Create `bots/it-helpdesk/prompt.md` with one sentence, for example "You help Kestrel Assurance staff with IT questions, answering only from the IT help pages." Then create `data/source/it-helpdesk/passwords.md`:

```markdown
# Passwords

## Changing your password

Passwords must be changed every 90 days. A new password needs at least 14 characters.
```

Show it:

```
uv run kestrel ingest --bot it-helpdesk
uv run kestrel ask --bot it-helpdesk --model ollama:gpt-oss:20b "How often must I change my password?"
uv run kestrel search --bot kestrel-products "password"
```

`ingest` reports one file added, "approval not required". The answer is every 90 days, quoting the new file. The last command shows that the product assistant's search returns nothing from the new file, because each assistant searches only its own documents. No code changed. [ADDING_A_BOT.md](ADDING_A_BOT.md) covers the approval list, skills and MCP tools.

Undo: delete the folders `bots/it-helpdesk` and `data/source/it-helpdesk`. The indexed copy stays in the database but no assistant can search it.

## If something fails on screen

- A provider error: add `--model ollama:gpt-oss:20b` to run the same question locally.
- A withheld answer: open the evidence pane in the web app (or add `--trace` on the command line), read which check failed and explain why the answer was held back.
- Anything else: `uv run kestrel doctor` prints the fix.
