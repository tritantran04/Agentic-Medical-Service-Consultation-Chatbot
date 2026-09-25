# Health Checkup Package Consultant Chatbot

An **agentic RAG chatbot** that helps users find a suitable health checkup package based on their described symptoms, age, or health concerns. The system uses a static package catalog as its knowledge base and an LLM-driven agent that decides, per turn, whether retrieval is needed to answer the user's question.

> **Disclaimer:** This project is intended for educational and information-retrieval purposes. It is not a substitute for diagnosis, treatment, or professional medical advice.

## Overview

The chatbot is designed to answer questions related to:

- Recommending a suitable health checkup package based on symptoms, age, or stated needs
- Providing package details (included services, price)
- General conversational questions (greetings, small talk) without unnecessary tool usage

The system does **not** send every question directly to an LLM. Instead, an agent decides, per turn, whether the question requires looking up the package catalog:

1. **Package retrieval** → the agent calls a tool that searches the package catalog using vector similarity search and returns matching packages.
2. **Direct answer** → used for questions that don't require catalog lookup (e.g. greetings, general questions), skipping retrieval entirely.

The package catalog is a static JSON file (`package_services.json`) containing package and service records; it is not scraped or synced from an external source.

## Architecture

```text
User
  |
  v
summarize_history
  |
  v
agent (LangGraph, tool-calling)
  |
  +---------------------------+
  |                           |
  v                           v
retrieve                  (no tool call)
(search_medical_packages)      |
  |                           |
  v                           |
ChromaDB vector search        |
  |                           |
  +-------------+-------------+
                |
                v
        generate_answer
                |
                v
          Final Response
```

## Main Components

### 1. LLM

The chatbot uses two models served through **Groq**:

```text
openai/gpt-oss-120b   (tool-selection, history summarization)
openai/gpt-oss-20b    (final answer generation)
```

### 2. Agentic Workflow

The agent workflow is implemented with **LangGraph**.

The main workflow is:

```text
START -> summarize_history -> agent -> retrieve (optional) -> generate_answer -> END
```

The agent has one tool, `search_medical_packages`, and decides per turn whether to call it. Limited to a single tool call per turn (no multi-step loop).

### 3. Conversation Summarization

`summarize_history` groups messages into full turns (a turn starts at the user's message and ends at the model's final answer), so a question and its answer are never split apart. The most recent `K_TURNS` turns are kept raw; older turns are summarized via the `SUMMARY_HISTORY` prompt.

### 4. RAG

The repository contains `package_services.json`, which stores the package and service catalog.

The data pipeline in `data.py`:

1. Loads `package_services.json` (packages and services).
2. Builds a text representation per package (name, description, included services).
3. Generates embeddings using:

```text
BAAI/bge-m3
```

4. Stores the embeddings in **ChromaDB**.
5. Uses cosine similarity search to retrieve the top matching packages above a similarity threshold.

The retriever is implemented as:

```python
search_packages(query)
```

### 5. Prompts

All system and instruction prompts live in `prompts.py`:

- `SYSTEM_MESSAGE` — the main system prompt defining the assistant's role, tool-selection rules, and conversation style.
- `RETRIEVAL_INSTRUCTION` / `NO_RETRIEVAL_INSTRUCTION` — short task instructions selected in `generate_answer` depending on whether the tool was used.
- `SUMMARY_HISTORY` — the prompt used by `summarize_history` to compress older turns.

### 6. Conversation Memory

The workflow uses `MemorySaver` to maintain state per `sessionId`, so multiple concurrent sessions are supported. State is kept in RAM only and does not persist across restarts.

### 7. A2A (Agent2Agent) Protocol Support

The chatbot is exposed as an [A2A](https://a2a-protocol.org/)-compliant agent:

- An **Agent Card** describing the agent's skill and endpoint.
- An **A2A Server** (`common/`, built on Starlette/Uvicorn) exposing the required JSON-RPC methods.
- `task_manager.py` bridging incoming A2A tasks to `ServiceAgent`.
- Streaming (SSE) and JWT-signed push notifications.

### 8. Web UI

A minimal chat interface under `Flask/`, built with Flask, calling `ServiceAgent` directly for manual testing in a browser.

## Project Structure

```text
project_root/
│
├── agent/
│   ├── __init__.py
│   ├── __main__.py           # Entry point: starts the A2A server
│   ├── agent.py               # LangGraph agent, tools, and workflow
│   ├── data.py                 # Package data loading and ChromaDB setup
│   ├── prompts.py              # System prompt, tool-instruction prompts, summary prompt
│   ├── package_services.json   # Package/service catalog (data source)
│   └── vectors/                 # Persisted ChromaDB collection (created on first run)
│
├── task_manager.py             # AgentTaskManager: bridges A2A tasks to ServiceAgent
│
├── common/                     # A2A protocol plumbing (types, server, task manager base, auth)
│   ├── types.py
│   ├── server/
│   │   ├── __init__.py
│   │   ├── server.py
│   │   ├── task_manager.py
│   │   └── utils.py
│   └── utils/
│       ├── push_notification_auth.py
│       └── in_memory_cache.py
│
├── Flask/
│   ├── app.py                  # Minimal web chat UI
│   └── templates/
│       └── index.html
│
├── requirements.txt
└── README.md
```

## Technologies

- **Python**
- **LangChain** / **LangGraph**
- **Groq** (`openai/gpt-oss-120b`, `openai/gpt-oss-20b`)
- **sentence-transformers** (`BAAI/bge-m3`)
- **ChromaDB**
- **A2A Protocol** (Agent Card + A2A Server, Starlette/Uvicorn, Server-Sent Events)
- **Flask**
- **RAG (Retrieval-Augmented Generation)**
- **Agentic Workflow / Tool Calling**

## Requirements

Recommended environment:

- Python 3.10+
- Groq API key
- Sufficient disk space for the `BAAI/bge-m3` model (~2.2 GB) and the local Chroma vector database
- **Enough free RAM to load `BAAI/bge-m3`** — this has been observed to fail with a memory allocation error on machines with limited free RAM; closing other memory-heavy applications, or switching to a smaller embedding model, can resolve this (see Limitations)

## Installation

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Environment Variables

Create a `.env` file in the project root (not committed to the repository):

```env
GROQ_API_KEY=your_groq_api_key
```

## Run the Chatbot

```bash
python -m agent.agent      # Command-line chat, no A2A — quickest way to test
python -m agent             # A2A server (default localhost:10000)
python Flask/app.py         # Web UI at localhost:5000
```

On first run, this will also download the `BAAI/bge-m3` model and build the Chroma index from `package_services.json`. The A2A Agent Card is available at `http://localhost:10000/.well-known/agent-card.json`; tasks are sent via JSON-RPC (`tasks/send`, `tasks/sendSubscribe`) to `http://localhost:10000/`.

Example user query:

```text
Tôi 50 tuổi, hay bị tức ngực và khó thở, nên khám gói nào?
```

## Key Features

- **Agentic tool selection** using LangGraph — retrieval only runs when the agent decides it's needed
- **Catalog-based RAG** with vector similarity search (ChromaDB)
- **Turn-aware history summarization**, compressing older conversation turns without splitting a question from its own answer
- **Per-session conversation memory** via distinct `thread_id`/`sessionId` values
- **A2A-compliant** — Agent Card, A2A Server, streaming, and push notifications, so other agents can call this chatbot over HTTP
- **Minimal web chat UI** for manual testing

## Limitations

- `openai/gpt-oss-120b` / `openai/gpt-oss-20b` on Groq occasionally fail with a `tool_use_failed` error (a known, currently unresolved Groq/gpt-oss compatibility issue, unrelated to this codebase). Retrying the question usually resolves it.
- `BAAI/bge-m3` requires a fair amount of RAM to load; on constrained machines this can fail with a memory allocation error.
- Retrieval is limited to a single tool call per turn (no multi-step ReAct-style loop).
- No retry logic or observability/tracing around LLM and tool calls.
- `SCORE_THRESHOLD` (0.35) has not been tuned against real usage data.
- `MemorySaver` keeps conversation state in RAM only — not persisted across restarts.
- The package catalog is a static file with no external sync mechanism.
- Prototype for learning and research — not intended as a medical diagnostic system.

## Author

Tran Tri Tan
