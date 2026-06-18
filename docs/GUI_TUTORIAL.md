# Marginalia Desktop GUI Tutorial

This guide is written for non-technical users. It explains the first-run setup, what each Settings field means, recommended values, local model setup, embeddings, rerank, and common troubleshooting steps.

## The Short Version

1. You must configure a question-answering model before the app can answer. A fresh install has no API key, so importing a Markdown file and asking a question may appear to do nothing until the LLM profile is configured.
2. You do not need to manually chunk Markdown, PDF, Word, or other documents. Marginalia reads and splits long files internally during analysis.
3. Embeddings are optional. Basic library search and grounded Q&A work without an embedding model. Embeddings only improve semantic recall.
4. Local models are supported when they expose an OpenAI-compatible API, such as Ollama, LM Studio, or vLLM.
5. Most users only need to configure the `Default` LLM profile. Leave `chat`, `reflect`, and `ingest` blank so they inherit `Default`.

## First-Run Setup

### 1. Open Settings

Open `Settings` from the left sidebar.

If the first-run guide says the Default profile has no API key, configure the model before importing many files.

### 2. Configure Default Under LLM Profiles

Expand `LLM profiles`, then expand `default`.

| Field | What to Enter |
| --- | --- |
| Provider | API type. Use `openai-compatible` for most compatible gateways and local models, `openai` for OpenAI, and `anthropic` for Claude. |
| Model | The exact model name from your provider or local model server. |
| Base URL | Usually needed for `openai-compatible` providers and local models. Leave empty for official OpenAI or Anthropic unless your provider says otherwise. |
| API Key | The provider key. For local servers that ignore authentication, enter any non-empty value such as `local`. |

After saving `Default`, the `chat`, `reflect`, and `ingest` profiles inherit it automatically.

### 3. Import Documents

Go to `Library` and upload Markdown, PDF, Office files, images, archives, or folders.

Long files are split internally. You do not need to prepare chunks manually.

If files were imported before an API key was configured, their analysis may have failed. After configuring the model, use Retry/Reprocess on those files.

### 4. Ask Questions

Go to `Chat`.

| Mode | Use Case |
| --- | --- |
| Auto | Recommended default for normal questions. |
| Quick | Faster, cheaper lookup for simple bounded questions. |
| Deep | Larger investigations across multiple sources. |

## Common Model Templates

### Cloud Provider or Compatible Gateway

| Field | Recommended Value |
| --- | --- |
| Provider | `openai-compatible` |
| Base URL | The provider's `/v1` endpoint, for example `https://api.example.com/v1` |
| Model | The provider's model name |
| API Key | Your provider key |

Leave `chat`, `reflect`, and `ingest` blank unless you intentionally want different models for each job.

### Official OpenAI

| Field | Recommended Value |
| --- | --- |
| Provider | `openai` |
| Base URL | Empty |
| Model | For example `gpt-4o-mini`, or another model available to your account |
| API Key | Your OpenAI API key |

### Ollama Local Model

Start Ollama first and make sure the model is installed.

| Field | Recommended Value |
| --- | --- |
| Provider | `openai-compatible` |
| Base URL | `http://127.0.0.1:11434/v1` |
| Model | The installed Ollama model name |
| API Key | `local` |

Recommended local-model limits:

| Setting | Recommended Value |
| --- | --- |
| Concurrent ingest tasks | `1` to `2` |
| Ingest LLM concurrency | `1` |
| Agent execute turn budget | `8` to `12` |
| Semantic recall | Off at first |
| Rerank | Off at first |

### LM Studio Local Model

Start the LM Studio OpenAI-compatible server first.

| Field | Recommended Value |
| --- | --- |
| Provider | `openai-compatible` |
| Base URL | `http://127.0.0.1:1234/v1` |
| Model | The model name currently served by LM Studio |
| API Key | `local` |

If local responses are unstable, lower concurrency before increasing token budgets.

## Settings Reference

### First-Run Guide

| Item | Meaning | Recommended |
| --- | --- | --- |
| LLM status | Checks whether `chat`, `reflect`, and `ingest` can resolve an API key. | Make this ready before large imports. |
| Configure a model first | Tells you to configure the `Default` model. | Most users only configure `Default`. |
| Import or retry documents | Import files, or reprocess files that failed before the model was configured. | Test with a small folder first. |
| Ask from Chat | Use the Chat page after analysis finishes. | Use Auto for normal questions. |
| Embeddings are optional | Explains that embeddings are not required for basic Q&A. | Leave off at first. |

### Connection

| Setting | Meaning | Recommended |
| --- | --- | --- |
| API base URL | Where the GUI sends backend API requests. | Leave empty in the packaged desktop app. Use `http://host:8000` only for a remote backend. |
| API bearer token | Token used when the backend sets `MARGINALIA_API_TOKEN`. | Leave empty for a local single-user desktop setup. |

Only change Connection when the GUI talks to a separate backend.

### Preferences

| Setting | Meaning | Recommended |
| --- | --- | --- |
| Language | UI language. It does not translate documents. | Auto, or a fixed language when needed. |
| Theme | Light, dark, or system appearance. | System. |
| Default conflict policy | What happens when an uploaded file has the same name as an existing file. | `rename`, so existing files are kept. |
| Agent token budget | Maximum model output tokens for planning and execution steps. | Keep `1024 / 2048`; raise execute first if answers are cut off. |
| Agent execute turn budget | Maximum tool-using investigation rounds per question. | `15`; use `8-12` for local models. |
| Read result compression | Compresses very large file reads before sending them to the chat model. | Enabled. |
| Concurrent ingest tasks | Number of background file-analysis tasks at once. | `3-5` for typical computers, `1-2` for local models, `10` for stable cloud APIs. |
| Ingest LLM concurrency | Parallel LLM calls for long document chunks and scanned-PDF OCR pages. | `1` for local models, `2-5` for normal cloud APIs, `10` for high-rate-limit APIs. |
| Status refresh | How often the bottom status bar refreshes. | `4 s`. |
| Compact sidebar | Icon-only navigation. | Off on desktop, on for small screens. |

### Retrieval

Retrieval settings improve search quality, but they are not required for first use.

#### Embedding Recall

| Setting | Meaning | Recommended |
| --- | --- | --- |
| Semantic recall | Adds vector-similar documents to recall candidates. | Off at first. Enable only after setting an embedding key and rebuilding the index. |
| Embedding provider | Embedding API type. | `openai-compatible` for most providers. |
| Embedding API key | Separate key for embedding calls. | Empty unless Semantic recall is enabled. |
| Embedding base URL | Embedding endpoint. | Keep the default DashScope-compatible URL or use your provider's `/v1` endpoint. |
| Embedding model | Embedding model name. | Default `text-embedding-v4`. |
| Embedding dimensions | Vector size produced by the embedding model. Must match the model. | `1024` for `text-embedding-v4`. |
| Embedding batch size | Number of texts embedded per request. | `10` for cloud APIs, `2-5` for weak local services. |
| Semantic recall limit | Number of vector candidates added before evidence selection. | `100`. |
| Semantic index backend | Where the semantic index is stored. | `auto`. |
| Semantic index / Rebuild | Current index state and rebuild button. | Rebuild after changing provider, model, or dimensions. |

Old files do not automatically get a new semantic index after you enable embeddings. Click Rebuild or reprocess the files.

#### Rerank

| Setting | Meaning | Recommended |
| --- | --- | --- |
| Rerank enabled | Enables a second-stage ranking model. | Off at first. Enable only if retrieval quality is insufficient. |
| Rerank API key | Separate key for rerank calls. | Empty unless Rerank is enabled. |
| Rerank base URL | Rerank endpoint. | Keep default for the default service, or follow provider docs. |
| Rerank model | Rerank model name. | Default `qwen3-rerank`. |
| Rerank top N | Number of candidates sent into reranking. | `80`. |
| Rerank max doc chars | Maximum characters per candidate passed to the reranker. | `1800`. |
| Rerank concurrency | Parallel rerank requests. | `5-10` for cloud APIs, `1-3` for local services. |
| Evidence selection | How final evidence is selected. | `quota` for source diversity; use `rerank` only if you strongly trust the reranker. |

### Server Status

These are mostly read-only diagnostics.

| Item | Meaning | Recommended |
| --- | --- | --- |
| App env | Runtime environment. | No change needed for desktop use. |
| Home | Data root containing database, library files, logs, and GUI overlay settings. | Default per-user folder. |
| DB | Database engine. | `sqlite` for desktop/single user. |
| Storage | How imported files are stored. | `mirror` for readable folders and easier backups. |
| Worker | Whether background analysis runs. | Enabled. |
| Auto lifecycle | Whether files are automatically demoted or archived. | Disabled for personal and small libraries. |
| Conflict | Current duplicate-name policy. | `rename`. |
| Token budget | Current plan/execute token limits. | Keep default unless answers are cut off. |
| Execute turns | Current investigation-round limit. | `15`. |
| Read compression | Whether large reads are compressed. | Enabled. |
| Semantic recall | Whether semantic recall is usable. | Should be off or unconfigured until embedding is set. |
| Embedding | Current embedding provider/model/dimensions. | Only important when semantic recall is enabled. |
| Rerank | Whether rerank is usable. | Off at first. |
| Vision | Whether the optional vision model is configured. | Configure only for image understanding, scanned PDFs, or figure captions. |

### LLM Profiles

| Profile | Purpose | Recommended |
| --- | --- | --- |
| Default | Base model settings inherited by `chat`, `reflect`, and `ingest`. | Required. Most users only fill this. |
| chat | Answers user questions. | Inherit Default unless you want a stronger chat model. |
| reflect | Summarizes conversations into memory/journal notes. | Inherit Default. |
| ingest | Summarizes, tags, and indexes imported files. | Inherit Default; use a cheaper model only if ingest cost is high. |
| vision | Optional model for images, figures, and scanned-PDF assistance. | Leave unset unless needed. |

Per-profile fields:

| Field | Meaning | Recommended |
| --- | --- | --- |
| Provider | API dialect. | `openai-compatible` for compatible gateways and local models, `openai` for OpenAI, `anthropic` for Claude. |
| Model | Model name. | Use the exact provider or local-server model name. |
| Base URL | Custom endpoint. | Empty for official OpenAI/Anthropic. Ollama: `http://127.0.0.1:11434/v1`. LM Studio: `http://127.0.0.1:1234/v1`. |
| API Key | Credential sent to the model provider. | Real key for cloud services, `local` for local servers that ignore auth. |
| Reset | Clears overrides so the profile inherits defaults again. | Use when a profile is misconfigured. |

## Troubleshooting: "Nothing Happens"

### 1. Check Backend Health

Packaged desktop builds start the backend automatically. In development mode, start it yourself.

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

If you use port `8001`:

```powershell
Invoke-RestMethod http://127.0.0.1:8001/health
```

The backend is reachable only if this returns `status: ok`.

### 2. Check the LLM API Key

Open Settings and check the first-run guide. The required profiles are `chat`, `reflect`, and `ingest`. The easiest fix is to set the `Default` API key.

### 3. Reprocess Files Imported Before the Key Was Set

Files imported before model setup may have failed analysis. Configure the model, then Retry/Reprocess those files.

### 4. Check Local Model Servers

Ollama:

```powershell
Invoke-RestMethod http://127.0.0.1:11434/v1/models
```

LM Studio:

```powershell
Invoke-RestMethod http://127.0.0.1:1234/v1/models
```

If these do not respond, Marginalia cannot connect to the local model either.

### 5. Port 8000 Is Busy

If the backend says:

```text
[WinError 10048] Only one usage of each socket address is normally permitted
```

Use port `8001`:

```powershell
cd "D:\AI Platform\marginalia"
$env:PYTHONPATH="src"
$env:MARGINALIA_DESKTOP="1"
$env:MARGINALIA_API_PORT="8001"
& ".\.venv\Scripts\python.exe" -m marginalia
```

For frontend development, point Vite to the same port:

```powershell
cd "D:\AI Platform\marginalia\desktop"
$env:VITE_API_TARGET="http://127.0.0.1:8001"
npm run dev
```

To inspect port `8000`:

```powershell
Get-NetTCPConnection -LocalPort 8000 | Select-Object LocalAddress,LocalPort,State,OwningProcess
Get-Process -Id <OwningProcess>
```

After confirming it is an old Python/Marginalia process:

```powershell
Stop-Process -Id <PID> -Force
```

## Development Startup Commands

Do not run `python .\main.py` from `src\marginalia`. That file defines the FastAPI app but does not start the server.

Start the backend from the repository root:

```powershell
cd "D:\AI Platform\marginalia"
$env:PYTHONPATH="src"
$env:MARGINALIA_DESKTOP="1"
& ".\.venv\Scripts\python.exe" -m marginalia
```

If port `8000` is unavailable:

```powershell
cd "D:\AI Platform\marginalia"
$env:PYTHONPATH="src"
$env:MARGINALIA_DESKTOP="1"
$env:MARGINALIA_API_PORT="8001"
& ".\.venv\Scripts\python.exe" -m marginalia
```

Start the frontend:

```powershell
cd "D:\AI Platform\marginalia\desktop"
npm run dev
```

If the backend uses `8001`:

```powershell
cd "D:\AI Platform\marginalia\desktop"
$env:VITE_API_TARGET="http://127.0.0.1:8001"
npm run dev
```

Open:

```text
http://localhost:5173
```

## Local Packaging Test

Run from the repository root:

```powershell
cd "D:\AI Platform\marginalia"
node scripts\prepare-backend.mjs
cd desktop
npm run tauri:build
cd ..
node scripts\package-windows-portable.mjs
```

Common output paths:

```text
desktop\src-tauri\target\release\marginalia-tauri.exe
desktop\src-tauri\target\release\bundle\
desktop\src-tauri\target\release\bundle\nsis\marginalia-v0.2.5-windows-x64-portable.zip
```

Packaged builds start the backend automatically. You do not need to run `python -m marginalia` manually.

## Data, Config, and Logs

Default data directory:

```text
%USERPROFILE%\Marginalia
```

Typical contents:

| Path | Purpose |
| --- | --- |
| `marginalia.db` | SQLite database. |
| `library\` | Library files for the default `mirror` storage backend. |
| `objects\` | Object files for `local` storage mode. |
| `config_overlay.json` | Settings saved from the GUI. |
| `logs\backend.log` | Packaged backend log. |
| `semantic-index\` | Semantic index files. |

Do not sync a running `MARGINALIA_HOME` with OneDrive, Dropbox, Syncthing, iCloud Drive, or similar tools. SQLite can be corrupted by concurrent file sync. Exit Marginalia first, then copy the whole directory for backup.

## Recommended Defaults For Non-Technical Users

### Typical Computer + Cloud Model

| Item | Recommended |
| --- | --- |
| LLM Default | Configure one stable cloud model |
| chat/reflect/ingest | Inherit Default |
| Semantic recall | Off at first |
| Rerank | Off at first |
| Concurrent ingest tasks | `3-5` |
| Ingest LLM concurrency | `2-5` |
| Read compression | Enabled |
| Conflict policy | `rename` |

### Local Model + Laptop

| Item | Recommended |
| --- | --- |
| Provider | `openai-compatible` |
| Base URL | Ollama `http://127.0.0.1:11434/v1` or LM Studio `http://127.0.0.1:1234/v1` |
| API Key | `local` |
| Concurrent ingest tasks | `1-2` |
| Ingest LLM concurrency | `1` |
| Agent execute turn budget | `8-12` |
| Semantic recall | Off |
| Rerank | Off |

### Large Library, Better Retrieval Needed

First make the basic import and chat workflow stable. Then:

1. Configure an Embedding API key.
2. Enable Semantic recall.
3. Click Rebuild to build the semantic index.
4. If retrieval is still not good enough, consider enabling Rerank.

Do not enable embeddings, rerank, vision, and high concurrency all at once on first setup. Start small, confirm it works, then add capabilities.
