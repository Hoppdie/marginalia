# Marginalia 桌面 GUI 使用教程

这份教程面向没有编程经验的非技术用户。目标是：安装后知道先配置什么、每个设置项是什么意思、哪些保持默认即可、什么时候才需要本地模型、嵌入模型和重排模型。

## 先说结论

1. 第一次使用必须先配置“提问模型”。默认安装后没有 API Key，所以导入文档后直接提问，通常会表现为没有回答、任务失败或一直等待。
2. 不需要手动给 Markdown、PDF、Word 等文档分块。导入后，Marginalia 会按文件类型自动读取、拆分、摘要、打标签和建立可检索信息。
3. 嵌入模型不是必需项。不开“语义召回”也能导入文档、搜索、提问；嵌入模型只是增强“意思相近但字面不一样”的召回能力。
4. 本地模型可以接入，只要它提供 OpenAI-compatible 接口，例如 Ollama、LM Studio、vLLM 等。
5. 普通用户最少只需要配置 `Default` 这一个 LLM 配置。`chat`、`reflect`、`ingest` 留空即可继承 `Default`。
6. 上传文件只是把文件放进资料库；上传后请关注底部后台任务按钮或资料库状态标记，等 AI 分析完成后再期待更好的问答效果。

## 第一次配置：最短路径

### 1. 打开设置页

进入左侧导航的“设置 / Settings”。

如果页面顶部提示“Default profile has no API key”或“缺少 API key”，先不要导入大量文档，先配置模型。

### 2. 配置 LLM Profiles 里的 Default

展开 `LLM 配置 / LLM profiles` 里的 `default`：

| 字段 | 应该填什么 |
| --- | --- |
| Provider | 模型服务类型。国内兼容接口、本地模型一般选 `openai-compatible`；OpenAI 官方选 `openai`；Anthropic/Claude 选 `anthropic`。 |
| Model | 模型名称，必须和服务商后台或本地模型显示的名称一致。 |
| Base URL | 只有 `openai-compatible` 或本地模型通常需要填。OpenAI/Anthropic 官方一般留空。 |
| API Key | 模型服务密钥。本地模型如果不校验密钥，也要填一个非空占位，例如 `local`。 |

保存后，`chat`、`reflect`、`ingest` 默认会继承 `default`。不要一开始就分别配置多个模型，除非你清楚自己为什么要分开。

### 3. 导入文档

进入“资料库 / Library”，上传 Markdown、PDF、Word、Excel、图片、压缩包或文件夹。

导入后后台会做分析任务。长文档会自动分块，不需要用户提前处理。请关注底部后台任务按钮或资料库状态标记，等状态变为完成后，再去聊天页提问效果最好；失败文件可以重试或重新处理。

如果你是在没有 API Key 的时候先导入了文档，后面配置好模型后，需要对失败的文档执行 Retry/Reprocess，重新分析一次。

### 4. 开始提问

进入“聊天 / Chat”：

| 模式 | 适合场景 |
| --- | --- |
| Auto | 默认推荐，普通问题都用它。 |
| Quick | 快速查找，成本低，适合简单事实问题。 |
| Deep | 需要跨多篇资料综合、查证、引用时使用。 |

## 常见模型配置模板

### 模板 A：普通云模型或国内兼容接口

适合大多数普通电脑。供应商只要提供 OpenAI-compatible 接口即可。

| 字段 | 推荐填写 |
| --- | --- |
| Provider | `openai-compatible` |
| Base URL | 服务商提供的 `/v1` 地址，例如 `https://api.example.com/v1` |
| Model | 服务商给你的模型名 |
| API Key | 服务商给你的 key |

`chat`、`reflect`、`ingest` 留空继承 `Default`。

### 模板 B：OpenAI 官方

| 字段 | 推荐填写 |
| --- | --- |
| Provider | `openai` |
| Base URL | 留空 |
| Model | 例如 `gpt-4o-mini`，或你账号可用的其他模型 |
| API Key | OpenAI API Key |

### 模板 C：Ollama 本地模型

先确保 Ollama 已经启动，并且模型已经下载。

| 字段 | 推荐填写 |
| --- | --- |
| Provider | `openai-compatible` |
| Base URL | `http://127.0.0.1:11434/v1` |
| Model | Ollama 中的模型名，例如你本机实际安装的 `qwen...`、`llama...` 等 |
| API Key | `local` |

Ollama 可以执行 ingest，但前提是模型上下文能容纳当前文件分析请求。Marginalia 会自动分块长文档，但单个分块仍然可能比较大，所以短上下文本地模型更适合小文件和中等长度文件。大型 PDF 或很长的 Markdown，建议换更长上下文的本地模型，或先用少量文件测试。

本地模型建议把这些并发项调低：

| 设置项 | 推荐值 |
| --- | --- |
| Concurrent ingest tasks | `1` 到 `2` |
| Ingest LLM concurrency | `1` |
| Agent execute turn budget | `8` 到 `12` |
| Semantic recall | 先关闭 |
| Rerank | 先关闭 |

### 模板 D：LM Studio 本地模型

先在 LM Studio 中启动 OpenAI-compatible server。

| 字段 | 推荐填写 |
| --- | --- |
| Provider | `openai-compatible` |
| Base URL | `http://127.0.0.1:1234/v1` |
| Model | LM Studio 当前服务的模型名 |
| API Key | `local` |

如果本地模型回答不稳定，优先降低并发，而不是提高 token 或轮数。

## 设置页逐项说明和推荐值

### 首次配置指南

| 项目 | 含义 | 推荐 |
| --- | --- | --- |
| LLM 配置状态 | 检查 `chat`、`reflect`、`ingest` 三个必需配置是否有 API Key。 | 先让这里变成“已配置”。 |
| Configure a model first | 引导你先配置 `Default` 模型。 | 普通用户只配 `Default`。 |
| Import or retry documents | 导入文件，或对之前失败的文件重新分析。 | 先用少量文档测试，再批量导入。 |
| Ask from Chat | 去聊天页提问。 | 普通问题用 Auto。 |
| Embeddings are optional | 说明嵌入模型不是必需。 | 初次使用先不要开。 |

### Connection：连接

这部分控制 GUI 连到哪个后端。

| 设置项 | 含义 | 推荐值 |
| --- | --- | --- |
| API base URL | GUI 请求后端 API 的地址。 | 打包版桌面应用留空；浏览器开发模式通常也留空；连接远程服务器时填 `http://host:8000`。 |
| API bearer token | 如果后端设置了 `MARGINALIA_API_TOKEN`，这里填写对应 token。 | 单机桌面版留空。 |

只有在“前端和后端分开跑”或“连接另一台服务器”时，才需要改这里。

### Preferences：偏好

| 设置项 | 含义 | 推荐值 |
| --- | --- | --- |
| Language | GUI 界面语言。不会翻译你的文档内容。 | `Auto` 或中文。 |
| Theme | 浅色、深色或跟随系统。 | `System`。 |
| Default conflict policy | 上传同名文件时怎么处理。 | `rename`，自动加后缀，避免覆盖旧文件。 |
| Agent token budget | 每次规划和回答允许模型输出的最大 token，界面中通常显示为 `plan / execute`。 | 默认 `1024 / 2048`。如果答案经常被截断，优先提高 execute。 |
| Agent execute turn budget | 一次问题最多允许智能体调用工具、读取资料的轮数。 | 默认 `15`。本地模型可降到 `8-12`；复杂研究问题可适当提高。 |
| Read result compression | 读取大文件时先压缩内容，再交给聊天模型。 | 开启。 |
| Concurrent ingest tasks | 后台同时分析多少个文件任务。 | 普通电脑 `3-5`；本地模型 `1-2`；稳定云 API 可用 `10`。 |
| Ingest LLM concurrency | 长文档分块、扫描 PDF OCR 等过程中，同时发起多少个模型请求。 | 本地模型 `1`；普通云 API `2-5`；高限额云 API 可用 `10`。 |
| Status refresh | 底部状态栏刷新频率。 | 默认 `4 s`。电脑慢或远程服务器可改 `10 s`。 |
| Compact sidebar | 左侧导航是否只显示图标。 | 桌面大屏关闭，小屏打开。 |

### Retrieval：检索

这部分是增强检索质量的设置，不是第一次使用的必需项。

#### Embedding recall：嵌入召回

| 设置项 | 含义 | 推荐值 |
| --- | --- | --- |
| Semantic recall | 是否启用向量语义召回。能找“意思相近但关键词不同”的文档。 | 初次使用关闭。等基础流程跑通后再开。 |
| Embedding provider | 嵌入模型接口类型。 | `openai-compatible` 通用；DashScope 兼容默认也可用。 |
| Embedding API key | 嵌入模型单独使用的 key。 | 不开 Semantic recall 就留空。 |
| Embedding base URL | 嵌入接口地址。 | 默认 DashScope 兼容地址是 `https://dashscope.aliyuncs.com/compatible-mode/v1`；其他服务商填自己的 `/v1` 地址。 |
| Embedding model | 嵌入模型名。 | 默认 `text-embedding-v4`。 |
| Embedding dimensions | 嵌入向量维度，必须和模型实际输出一致。 | `text-embedding-v4` 默认 `1024`。换模型时按服务商文档修改。 |
| Embedding batch size | 一次请求打包多少条文本做嵌入。 | 云 API `10`；本地或弱服务 `2-5`。 |
| Semantic recall limit | 语义召回最多加入多少候选资料。 | `100`。 |
| Semantic index backend | 语义索引存储方式。 | `auto`。 |
| Semantic index / Rebuild | 当前语义索引状态，以及重建按钮。 | 修改嵌入 provider、model、dimensions 后必须 Rebuild。 |

注意：开启 Semantic recall 之后，旧文档不会自动拥有新的向量索引。需要点击 Rebuild，或重新处理文档。

#### Rerank：重排

| 设置项 | 含义 | 推荐值 |
| --- | --- | --- |
| Rerank enabled | 是否启用二次排序模型。 | 初次使用关闭。只有检索质量不够时再开。 |
| Rerank API key | 重排模型单独使用的 key。 | 不开 Rerank 就留空。 |
| Rerank base URL | 重排接口地址。 | 使用默认服务时保持默认；其他服务按文档填写。 |
| Rerank model | 重排模型名。 | 默认 `qwen3-rerank`。 |
| Rerank top N | 送入重排的候选数量。 | `80`。 |
| Rerank max doc chars | 每个候选最多送多少字符给重排模型。 | `1800`。 |
| Rerank concurrency | 同时进行多少个重排请求。 | 云 API `5-10`；本地服务 `1-3`。 |
| Evidence selection | 最终证据选择方式。 | `quota` 更稳，能保证来源多样；非常信任重排模型时再用 `rerank`。 |

### Server status：服务端状态

这部分大多是只读诊断信息，用来排查问题。

| 项目 | 含义 | 推荐 |
| --- | --- | --- |
| App env | 当前运行环境。 | 桌面使用无需修改。 |
| Home | Marginalia 数据根目录，包含数据库、资料库、日志、配置覆盖文件。 | 默认 `%USERPROFILE%\Marginalia`。 |
| DB | 数据库类型。 | 单机桌面版用 `sqlite`。 |
| Storage | 文件存储方式。 | `mirror`，文件夹结构更直观，方便备份。 |
| Worker | 后台任务是否启用。 | 启用。 |
| Auto lifecycle | 是否自动把文件降级/归档。 | 个人或小型资料库建议关闭，手动管理。 |
| Conflict | 当前同名文件策略。 | `rename`。 |
| Token budget | 当前规划/执行 token 限制。 | 保持默认，必要时调高 execute。 |
| Execute turns | 当前问题调查轮数。 | `15`。 |
| Read compression | 大文件读取压缩是否开启。 | 开启。 |
| Ingest concurrency | 导入分析并发。 | 根据模型能力调节。 |
| Semantic recall | 语义召回是否可用。 | 没有嵌入 key 时应显示未配置或关闭。 |
| Embedding | 当前嵌入 provider/model/dimensions。 | 只有开启语义召回时才需要关心。 |
| Rerank | 重排是否可用。 | 初次使用关闭。 |
| Vision | 视觉模型是否配置。 | 只有图片理解、扫描 PDF OCR 或图表说明需要。 |

### LLM profiles：模型配置

| Profile | 用途 | 推荐 |
| --- | --- | --- |
| Default | 默认模型配置，被 `chat`、`reflect`、`ingest` 继承。 | 必填。普通用户只填它。 |
| chat | 回答用户问题。 | 留空继承 Default。需要更强回答模型时才单独配置。 |
| reflect | 把对话整理成记忆/日志。 | 留空继承 Default。 |
| ingest | 导入文档时做摘要、标签、索引。 | 留空继承 Default。若导入量很大，可单独用便宜模型。 |
| vision | 图片、图表、扫描 PDF 等视觉能力。 | 默认不填。只有确实需要视觉模型时配置。 |

每个 Profile 里的字段含义：

| 字段 | 含义 | 推荐 |
| --- | --- | --- |
| Provider | API 协议类型。 | 国内兼容服务、本地模型用 `openai-compatible`；OpenAI 官方用 `openai`；Claude 用 `anthropic`。 |
| Model | 模型名。 | 必须填服务商或本地软件显示的准确模型名。 |
| Base URL | 自定义接口地址。 | OpenAI/Anthropic 官方留空；Ollama 用 `http://127.0.0.1:11434/v1`；LM Studio 用 `http://127.0.0.1:1234/v1`。 |
| API Key | 模型密钥。 | 云服务填真实 key；本地服务不校验时填 `local`。 |
| Reset | 清空当前 profile 的覆盖配置，让它重新继承默认值。 | 配错时使用。 |

## “没有反应”的排查顺序

### 1. 后端是否启动

打包版桌面应用会自动启动后端。开发模式下需要手动启动。

在 PowerShell 中执行：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

如果你改用了 `8001`，就测：

```powershell
Invoke-RestMethod http://127.0.0.1:8001/health
```

能返回 `status: ok` 才说明后端活着。

### 2. 是否配置了 LLM API Key

进入设置页，看“首次配置指南”。必须至少让 `chat`、`reflect`、`ingest` 都能拿到 API Key。最简单就是配置 `Default` 的 API Key。

### 3. 导入任务是否失败

如果文件是在配置模型之前导入的，后台分析可能已经失败。配置好模型后，对这些文件执行 Retry/Reprocess。

上传成功不代表 AI 分析已经完成。请看底部后台任务按钮和资料库状态标记；如果文件显示分析失败，修好模型配置后再重试。

### 4. 本地模型是否真的在服务

Ollama 可测试：

```powershell
Invoke-RestMethod http://127.0.0.1:11434/v1/models
```

LM Studio 可测试：

```powershell
Invoke-RestMethod http://127.0.0.1:1234/v1/models
```

如果这里都不通，Marginalia 也无法连接本地模型。

### 5. 端口是否被占用

如果启动后端时报：

```text
[WinError 10048] 通常每个套接字地址只允许使用一次
```

说明 `8000` 端口已被占用。可以换 `8001`：

```powershell
cd "D:\AI Platform\marginalia"
$env:PYTHONPATH="src"
$env:MARGINALIA_DESKTOP="1"
$env:MARGINALIA_API_PORT="8001"
& ".\.venv\Scripts\python.exe" -m marginalia
```

前端开发模式也要指向同一个端口：

```powershell
cd "D:\AI Platform\marginalia\desktop"
$env:VITE_API_TARGET="http://127.0.0.1:8001"
npm run dev
```

查看谁占用了 `8000`：

```powershell
Get-NetTCPConnection -LocalPort 8000 | Select-Object LocalAddress,LocalPort,State,OwningProcess
Get-Process -Id <上一步看到的 OwningProcess>
```

确认是旧的 Python/Marginalia 进程后再关闭：

```powershell
Stop-Process -Id <PID> -Force
```

## 开发模式启动命令

不要在 `src\marginalia` 里执行 `python .\main.py`。这个文件只是 FastAPI 应用定义，不会自己启动服务器。

正确后端启动方式：

```powershell
cd "D:\AI Platform\marginalia"
$env:PYTHONPATH="src"
$env:MARGINALIA_DESKTOP="1"
& ".\.venv\Scripts\python.exe" -m marginalia
```

如果 `8000` 被占用：

```powershell
cd "D:\AI Platform\marginalia"
$env:PYTHONPATH="src"
$env:MARGINALIA_DESKTOP="1"
$env:MARGINALIA_API_PORT="8001"
& ".\.venv\Scripts\python.exe" -m marginalia
```

前端开发界面：

```powershell
cd "D:\AI Platform\marginalia\desktop"
npm run dev
```

如果后端用了 `8001`：

```powershell
cd "D:\AI Platform\marginalia\desktop"
$env:VITE_API_TARGET="http://127.0.0.1:8001"
npm run dev
```

浏览器打开：

```text
http://localhost:5173
```

## 本地打包测试命令

在仓库根目录执行：

```powershell
cd "D:\AI Platform\marginalia"
node scripts\prepare-backend.mjs
cd desktop
npm run tauri:build
cd ..
node scripts\package-windows-portable.mjs
```

常见输出位置：

```text
desktop\src-tauri\target\release\marginalia-tauri.exe
desktop\src-tauri\target\release\bundle\
desktop\src-tauri\target\release\bundle\nsis\marginalia-v0.2.6-windows-x64-portable.zip
```

打包版启动后会自动拉起后端，不需要你手动运行 `python -m marginalia`。

## 数据、配置和日志在哪里

默认数据目录：

```text
%USERPROFILE%\Marginalia
```

里面通常包含：

| 路径 | 作用 |
| --- | --- |
| `marginalia.db` | SQLite 数据库。 |
| `library\` | 默认 mirror 存储下的资料库文件。 |
| `objects\` | local 存储模式下的对象文件。 |
| `config_overlay.json` | GUI 保存的设置覆盖项。 |
| `logs\backend.log` | 打包版后端日志。 |
| `semantic-index\` | 语义索引文件。 |

不要在程序运行时用 OneDrive、Dropbox、Syncthing、iCloud Drive 等同步整个 `MARGINALIA_HOME`。SQLite 数据库在并发同步下可能损坏。需要备份时，先退出程序，再复制整个目录。

## 更新和 About 页面

About 页面显示当前版本，并提供手动检查最新版本的按钮。

注意：

1. 检查最新版本会访问 GitHub Releases。
2. 程序不会在启动时自动联网检查版本。
3. 如果当前网络无法访问 GitHub，检查失败是正常的，可以手动去项目 Releases 页面下载。

## 给非技术用户的推荐默认方案

### 普通电脑 + 云模型

| 项目 | 推荐 |
| --- | --- |
| LLM Default | 配一个稳定的云模型 |
| chat/reflect/ingest | 留空继承 Default |
| Semantic recall | 先关闭 |
| Rerank | 先关闭 |
| Concurrent ingest tasks | `3-5` |
| Ingest LLM concurrency | `2-5` |
| Read compression | 开启 |
| Conflict policy | `rename` |

### 本地模型 + 普通笔记本

| 项目 | 推荐 |
| --- | --- |
| Provider | `openai-compatible` |
| Base URL | Ollama `http://127.0.0.1:11434/v1` 或 LM Studio `http://127.0.0.1:1234/v1` |
| API Key | `local` |
| Concurrent ingest tasks | `1-2` |
| Ingest LLM concurrency | `1` |
| Agent execute turn budget | `8-12` |
| Semantic recall | 关闭 |
| Rerank | 关闭 |

### 资料很多、需要更好检索

先跑通基础导入和提问，再增加：

1. 配置 Embedding API Key。
2. 开启 Semantic recall。
3. 点击 Rebuild 重建语义索引。
4. 如果检索仍不准，再考虑开启 Rerank。

不要一开始同时打开嵌入、重排、视觉和高并发。先让最小配置稳定工作，再逐步加能力。
