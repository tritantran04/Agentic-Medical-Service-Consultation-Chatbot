# Medical Service Consultation Chatbot (A2A)

An agent that **recommends health check-up packages** to users, built on Google's **A2A (Agent-to-Agent) protocol**. The agent takes a user's symptoms, age, or health-check needs, retrieves matching packages from an internal dataset using RAG, and replies in Vietnamese with the package name, price, and included services.

> **Note:** This is an advisory chatbot only — it does not replace a doctor's diagnosis.

## Overview

The agent is packaged as an **A2A Server**: it exposes an `AgentCard` at `/.well-known/agent.json` and `/.well-known/agent-card.json`, accepts tasks over JSON-RPC (`tasks/send`, `tasks/sendSubscribe`, `tasks/get`, `tasks/cancel`, push notifications, etc.), and can be called by other A2A clients/agents as a "Service Agent".

Flow for a single question:

1. The user describes their symptoms / age / health-check needs.
2. The agent (LangGraph) decides whether package data needs to be looked up.
3. If so, it calls the `search_medical_packages` tool to query the vector store.
4. It generates the final answer based on the retrieval result (or answers directly if no lookup was needed).

## Architecture

```text
Client (A2A)
   |
   v
A2AServer (Starlette) --- /.well-known/agent.json
   |
   v
AgentTaskManager (task_manager.py)
   |
   v
ServiceAgent (agent/agent.py) — LangGraph
   |
   +-------------------------+
   |                         |
   v                         v
summarize_history          agent (route)
   |                         |
   v                 +-------+-------+
 (keeps the last      |               |
  K_TURNS turns,       v               v
  summarizes the    retrieve      generate_answer
  older ones)      (search_medical      |
                       _packages)        |
                           |             |
                           +------> generate_answer
                                         |
                                         v
                                  Final Response
```

## Main Components

### 1. A2A Protocol Layer (`common/`)

- `common/types.py`: defines the JSON-RPC / A2A data types (`Task`, `Message`, `Artifact`, `AgentCard`, `AgentSkill`, the various `tasks/send`, `tasks/sendSubscribe`, push-notification request/response types, etc.).
- `common/server/server.py`: `A2AServer` built on Starlette, routes JSON-RPC requests to the corresponding `TaskManager` handler, and supports both regular and streaming (SSE, via `EventSourceResponse`) responses.
- `common/server/task_manager.py`: `InMemoryTaskManager` — manages tasks, conversation history, and the SSE event queue in memory.
- `common/client/`: client helpers (`A2AClient`, `A2ACardResolver`) for calling another A2A server.
- `common/utils/push_notification_auth.py`: signs/verifies push notifications with a JWK, and exposes the `/.well-known/jwks.json` endpoint.

### 2. Task Manager (`task_manager.py`)

`AgentTaskManager` (a subclass of `InMemoryTaskManager`) implements the A2A handlers:

- `on_send_task` / `on_send_task_subscribe`: receive a request, call `ServiceAgent.invoke` or `.stream`, and update the task status (`WORKING`, `INPUT_REQUIRED`, `COMPLETED`).
- Verifies the push-notification URL before storing it (`set_push_notification_info`).
- Returns the result as a `Message` or an `Artifact`, depending on whether the agent has finished or still needs more input.

### 3. Business Agent (`agent/agent.py`)

Built with **LangGraph**, using an LLM served through **Groq** (`langchain_groq.ChatGroq`):

- `llm_main` (`openai/gpt-oss-120b`): decides whether to call a tool and summarizes conversation history.
- `llm_answer` (`openai/gpt-oss-20b`): generates the final answer.

Graph nodes:

- `summarize_history`: groups messages into **complete question-answer turns** (including any tool-call steps within that turn), keeps the most recent `K_TURNS` turns as-is, and summarizes older turns via the `SUMMARY_HISTORY` prompt — instead of trimming by a raw message count, which avoids splitting a question from its own answer.
- `agent`: the LLM decides whether to call the `search_medical_packages` tool or answer directly.
- `retrieve`: a `ToolNode` that runs the package-search tool.
- `generate_answer`: generates the final answer, forcing the LLM to return valid JSON matching the `ResponseFormat` schema (`status`, `message`), which is then parsed into the reply sent to the user.

`ServiceAgent` wraps this graph and exposes `invoke()` (synchronous) and `stream()` (asynchronous, emitting status updates such as "Đang tìm gói khám phù hợp..." / "Searching for a matching package...").

Conversation memory uses LangGraph's `MemorySaver`, keyed by the `sessionId`/`thread_id` passed in with each task — so each conversation session has its own state.

### 4. Data & Retrieval (`agent/data.py`, `agent/package_services.json`)

- `package_services.json` contains two lists: `packages` (health-check packages: id, name, description, price, `services_ids`, and optionally `included_package_ids` for a package that bundles the services of other packages) and `services` (individual medical services).
- `build_package_text`: combines the name + cleaned description + names of included services (resolved recursively through `included_package_ids`) into the text used for embedding.
- Embeddings are generated with **Sentence-Transformers**, using the **`BAAI/bge-m3`** model.
- Vectors are stored in **ChromaDB** (`PersistentClient`, collection `medical_packages`, cosine metric).
- `build_or_load_index`: hashes the source data to detect changes automatically and rebuilds the index only when needed, avoiding re-embedding everything on every startup.
- `search_packages`: queries the top-`k` results (default 3), filters by a similarity threshold (`SCORE_THRESHOLD = 0.35`), and returns the matching package `id`s.
- `get_package_by_id`: returns detailed descriptions (name, description, price, included services) for the given package ids, to be fed to the LLM as context.

### 5. Prompts (`agent/prompts.py`)

- `SYSTEM_MESSAGE`: defines the assistant's role as a health-package advisor, the rules for when to call the tool, a requirement not to invent package/price information, and a reminder that this is advisory only, not a substitute for a doctor's diagnosis.
- `RETRIEVAL_INSTRUCTION` / `NO_RETRIEVAL_INSTRUCTION`: guide answer generation depending on whether retrieval results are available.
- `SUMMARY_HISTORY`: instructs the model to summarize older conversation turns concisely (3–5 sentences).

## Project Structure

```text
.
├── agent/
│   ├── __main__.py            # Builds the AgentCard and runs the A2AServer
│   ├── agent.py                # LangGraph agent (ServiceAgent), search_medical_packages tool
│   ├── data.py                 # Data loading, embeddings, ChromaDB, retrieval
│   ├── prompts.py               # System prompt and task-instruction prompts
│   └── package_services.json   # Package and service data
├── common/
│   ├── client/                 # A2A client (for calling other agents)
│   ├── server/                 # A2A server, task manager, utils
│   ├── utils/                  # Cache, push-notification auth (JWK)
│   └── types.py                 # JSON-RPC/A2A data types
├── task_manager.py             # AgentTaskManager: bridges the A2A server and ServiceAgent
└── requirement.txt             # Dependency list
```

## Tech Stack

- **Python**
- **LangChain / LangGraph**
- **Groq** (`langchain_groq.ChatGroq`) — LLMs: `openai/gpt-oss-120b`, `openai/gpt-oss-20b`
- **Sentence-Transformers** — `BAAI/bge-m3` embeddings
- **ChromaDB** — vector store
- **A2A Protocol** — `AgentCard`, JSON-RPC tasks, SSE streaming, push notifications (JWK)
- **Starlette / Uvicorn** — HTTP server
- **Pydantic**

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/tritantran04/Medical-Service-Consultation-Chatbot.git
cd Medical-Service-Consultation-Chatbot
```

### 2. Install dependencies

```bash
pip install -r requirement.txt
```

### 3. Environment variables

Create a `.env` file in the project root:

```env
GROQ_API_KEY=your_groq_api_key
```

Do not commit the `.env` file or expose your API key.

## Running the Agent

Start the A2A server (defaults to `localhost:10000`):

```bash
python -m agent --host localhost --port 10000
```

On startup, the agent will:

- Check/build the ChromaDB index from `package_services.json` (`build_or_load_index`).
- Register the `AgentCard` (skill: `health_service_selection`) at `/.well-known/agent.json` and `/.well-known/agent-card.json`.
- Be ready to accept A2A requests (`tasks/send`, `tasks/sendSubscribe`, ...) at the root `/` endpoint.

You can also test the agent logic standalone (bypassing the A2A server) with:

```bash
python -m agent.agent
```

which starts an interactive terminal chat (type `exit`/`quit`/`q` to stop).

## Limitations

- Answer quality depends on the data in `package_services.json`; the agent is instructed not to invent packages or prices outside this dataset.
- `TaskManager` currently keeps task state in memory (`InMemoryTaskManager`), so data is lost on server restart.
- This project is intended for learning/research purposes and is not a medical diagnostic system.

## Author

**Tran Tri Tan**
