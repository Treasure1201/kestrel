# AI tool disclosure

I was the architect of this solution and AI coding assistants were my build team. I set the vision, the architecture and the components; the assistants wrote the detailed design, the code and the tests to that direction, and I reviewed and redirected the work as it progressed.

## What I specified

- **The shape of the system:** one governed core with three ways in (a command line, a Streamlit app and an MCP server), running locally from a single SQLite file with a local vector index and no Docker.
- **Assistants as configuration.** I did not want a one-off chatbot. A business that proves this on product guides will next want an internal policies assistant, then an IT help desk. So each assistant is a folder that declares its documents, prompt, tools, MCP servers, skills, model policy and privacy policy, and a new one needs no code.
- **The agent framework and models:** LangChain DeepAgents, so the same agent runs on local Ollama, Ollama Cloud, Anthropic and OpenAI from one `.env` setting, and the specific models to evaluate.
- **Production direction:** security and POPIA handled in the prototype itself, with the managed-cloud design (Azure, AWS, Google Cloud; PostgreSQL and a vector service) kept to a separate document.
- **The user experience:** a chat window with a separate evidence pane, and screens for indexed documents, findings, retrieval hit rates and the questions the assistant could not answer. I approved a mock-up first, then rejected parts of the first build (scrolling, an unreadable evidence panel, the colours) and had them reworked.
- **The quality bar:** every answer backed by quoted evidence, honest reporting of failures, and a test of hidden instructions in documents.

## How I worked with the tools

Claude (in Claude Code) turned my direction into a written design and plan, which I approved before any code was written. I had a second model, OpenAI Codex, review that design independently, and again review the finished code; both reviews changed the result. Claude sub-agents then built one work package each in isolated git worktrees, against interfaces fixed up front, and each change was tested before it was merged.
