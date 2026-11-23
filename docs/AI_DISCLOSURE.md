# AI tool disclosure

I designed and directed this solution, with AI coding assistants as my build team. I chose the architecture, components, providers and models, and set the user-experience direction. The assistants turned that direction into detailed design, code and tests. I reviewed their work and redirected it as needed.

## What I specified

I wanted one governed core behind a command line, a Streamlit app and an MCP server. It had to run locally from a single SQLite file with a local vector index and no Docker.

I also wanted the business to be able to add assistants without changing code. Product guides would be one use case; internal policies and an IT help desk would follow. Each assistant therefore has a folder declaring its documents, prompt, tools, MCP servers, skills, model policy and privacy policy.

I chose LangChain DeepAgents and specified the models to evaluate. One `.env` setting selects local Ollama, Ollama Cloud, Anthropic or OpenAI. Security and POPIA controls belong in the local application; the managed-cloud design (Azure, AWS, Google Cloud; PostgreSQL and a vector service) belongs in a separate document.

For the interface, I specified a chat window with a separate evidence pane, plus screens for indexed documents, findings, retrieval hit rates and unanswered questions. I approved a mock-up before implementation. When I reviewed the interface, I had the scrolling, unreadable evidence panel and colours reworked.

I required quoted evidence behind every answer, honest reporting of failures and tests of hidden instructions in documents.

## How I worked with the tools

Claude (in Claude Code) wrote the design and plan from my direction. I approved them before code was written. I asked OpenAI Codex to review the design independently and to review the finished code; both reviews changed the result.

Claude sub-agents each built a work package in an isolated git worktree, against interfaces fixed up front. Each change was tested before merging.
