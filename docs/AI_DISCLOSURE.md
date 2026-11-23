# AI tool disclosure

I designed and directed this solution, with AI coding assistants as my build team. I set the architecture, components, providers, models and user-experience direction. The assistants turned that into detailed design, code and tests; I reviewed their work and redirected it where needed.

## What I specified

I wanted one governed core behind a command line, a Streamlit web app and an MCP server (for use by other AI tools), running on a laptop from a single SQLite file with a local vector index and no Docker. New assistants should need no new code: product guides first, then internal policies or an IT help desk, each a folder declaring its documents, instructions, tools, MCP servers, skills, model policy and privacy policy. I chose LangChain's DeepAgents framework and the models to evaluate, with one setting to switch between local Ollama, Ollama Cloud, Anthropic and OpenAI. Security and POPIA controls belong in the application; the cloud design for Azure, AWS and Google Cloud in a separate document. I required quoted evidence behind every answer, honest reporting of failures, and tests with instructions hidden in documents. For the web app I specified a chat window beside an evidence pane, with screens for documents, findings, search accuracy and unanswered questions. I approved a mock-up first and, after reviewing the build, had the scrolling, the hard-to-read evidence panel and the colours reworked.

## How I worked with the tools

Claude, in Claude Code, wrote the design and work plan from my direction; I approved both before any code was written. I asked OpenAI's Codex to review the design independently and later the finished code; both reviews changed the result. Separate Claude instances each built one work package in their own copy of the repository, against interfaces agreed before work began. Every change passed its tests before it was merged.
