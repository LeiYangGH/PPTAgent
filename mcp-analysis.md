# PPTAgent 项目 MCP 全景分析

## 概览

本项目包含 **两套独立的 MCP（Model Context Protocol）生态**，分别面向不同的使用场景和用户群体：

| 生态 | 用途 | 使用方 | 传输方式 |
|------|------|--------|----------|
| **deeppresenter 工具服务器** | 内部 Agent 工具链 | DeepPresenter 各 Agent（Research、PPTAgent、Design 等） | stdio（本地）+ Docker |
| **pptagent-mcp** | 对外模板化 PPT 生成服务 | Claude、Cursor 等外部 MCP 客户端 | stdio（通过 `pptagent-mcp` 命令） |

两套生态的服务端均基于 **`fastmcp`** 框架（v2.x）实现，客户端则使用官方 **`mcp`** SDK（v1.14+）进行连接。

---

## 1. DeepPresenter 内部 MCP 工具服务器

位于 `deeppresenter/tools/` 目录下，由 `deeppresenter/agents/env.py` 通过 `MCPClient`（`deeppresenter/utils/mcp_client.py`）统一编排调度。

配置文件：`deeppresenter/mcp.json`（或其示例文件 `.example`）。

### 1.1 any2markdown — 文件转换服务器

**文件：** `deeppresenter/tools/any2markdown.py`

**作用：** 将用户上传的附件（PDF、DOCX 等）转换为 Markdown 手稿，供下游 Agent 消费。

**提供的工具：**

| 工具名 | 说明 |
|--------|------|
| `convert_to_markdown` | 将文件转为 Markdown。PDF 优先使用 **MinerU API**（在线/离线）进行高质量解析；其他格式回退到 `markitdown`。提取内嵌图片并保存到 `images/` 子目录。 |

**关键行为：**
- 自动检测 `MINERU_API_KEY` / `MINERU_API_URL` 以启用增强版 PDF 解析。
- 过滤掉不支持的 base64 图片格式。
- 将返回 Markdown 中的所有图片路径解析为绝对路径。

---

### 1.2 task — 任务生命周期与待办管理

**文件：** `deeppresenter/tools/task.py`

**作用：** 维护 Agent 的任务状态、显式推理日志，以及最终产物的校验。

**提供的工具：**

| 工具名 | 说明 |
|--------|------|
| `todo_create` | 创建新的待办项，存储在 `todo.csv` 中。 |
| `todo_update` | 更新待办内容或状态（`pending` / `in_progress` / `completed` / `skipped`）。 |
| `todo_list` | 列出未完成的待办；若全部完成则返回 `"All todos completed"`。 |
| `thinking` | 显式推理通道，用于记录 Agent 的思考过程。 |
| `finalize` | **产物校验闸门**。按 Agent 类型检查最终产物路径是否符合规范（如 Planner 须为 `.json`、Research 须为 `.md`、PPTAgent 须为 `.pptx`、Design 须为 HTML 文件）。对 Research 还会将图片链接重写为绝对路径。 |

**关键行为：**
- `finalize` 是每个 Agent 循环的 **出口检查点**。
- 使用 `filelock` 保证 CSV 并发读写安全。

---

### 1.3 deeppresenter（reflect）— 产物检查

**文件：** `deeppresenter/tools/reflect.py`

**作用：** 校验中间产物和最终产物（HTML 幻灯片、Markdown 手稿）。

**提供的工具：**

| 工具名 | 说明 |
|--------|------|
| `inspect_slide` | 验证 HTML 幻灯片：尝试将其转换为 PPTX。在 **Reflective Design 模式**（`heavy_reflect` + 多模态）下，会将幻灯片渲染为图片并以 `ImageContent` 形式返回，供视觉 critique。 |
| `inspect_manuscript` | 校验 Markdown 手稿：统计页数、检测语言（通过 fasttext `LID_MODEL`）、检查图片是否存在、警告外部 URL、缺失 alt 文本、图片重复使用等问题。 |

**关键行为：**
- `inspect_slide` 是 **Design Agent** 的核心反馈回路。
- `inspect_manuscript` 供 **Research Agent** 在交接前确保手稿质量。

---

### 1.4 tool_agents — LLM 增强能力

**文件：** `deeppresenter/tools/tool_agents.py`

**作用：** 将 LLM 能力（图像生成、视觉理解、长文档摘要）以工具形式按需暴露。

**提供的工具：**

| 工具名 | 启用条件 | 说明 |
|--------|----------|------|
| `image_generation` | 配置了 `t2i_model` | 根据文本提示生成图片（支持 base64 或 URL 返回）。 |
| `image_caption` | 配置了 `vision_model` | 描述并分类图片（`Chart`、`Diagram`、`Landscape` 等）。 |
| `document_summary` | 启用了 `multiagent_mode` | 针对特定任务对长文本文档进行摘要。 |

**关键行为：**
- 工具根据运行时配置 **条件注册**，未配置则不暴露。
- 使用与主 Agent 相同的 LLM 后端。

---

### 1.5 research — 学术论文搜索

**文件：** `deeppresenter/tools/research.py`

**作用：** 从 arXiv 和 Semantic Scholar 搜索并获取学术元数据。

**提供的工具：**

| 工具名 | 说明 |
|--------|------|
| `search_papers` | 使用字段前缀（`ti:`、`au:`、`abs:`、`cat:`）查询 arXiv。 |
| `get_paper_authors` | 通过 `ARXIV:XXXX.XXXXX` ID 从 Semantic Scholar 获取作者详情。 |
| `get_scholar_details` | 从 Semantic Scholar 获取学者档案、h-index 及论文列表。 |

---

### 1.6 search — 网页搜索与抓取

**文件：** `deeppresenter/tools/search.py`

**作用：** 通用网页搜索、图片搜索、页面抓取和文件下载。

**提供的工具：**

| 工具名 | 后端 | 说明 |
|--------|------|------|
| `search_web` | SerpAPI **或** Tavily | 文本搜索，支持按时间范围过滤。 |
| `search_images` | SerpAPI **或** Tavily | 图片搜索。 |
| `fetch_url` | Playwright + trafilatura | 渲染网页并提取干净的 Markdown。可检测 WAF/反爬虫页面。 |
| `download_file` | httpx | 下载文件（图片自动将 WEBP 转为 PNG）。 |

**关键行为：**
- **后端互斥**：若配置了 `SERPAPI_KEY` 则使用 Google；否则回退到 `TAVILY_API_KEY`。
- Tavily 针对中国大陆优化（`country: "china"`）。
- `fetch_url` 对 JavaScript 渲染型页面使用 Playwright。

---

### 1.7 sandbox — Docker 执行环境

**文件：** 在 `deeppresenter/mcp.json` 中配置，基于 DesktopCommanderMCP 衍生实现。

**作用：** 为不安全操作提供隔离的容器化 shell 环境。

**提供的工具：**
- 文件系统操作、shell 命令、代码执行（继承自 DesktopCommanderMCP）。

**关键行为：**
- 以 **Docker 容器** 形式运行（镜像 `deeppresenter-sandbox`）。
- 通过 `--volumes-from deeppresenter-host` 挂载工作区。
- 不是 Python `fastmcp` 服务器，而是基于 Docker 的 stdio MCP 服务器。

---

## 2. PPTAgent 对外 MCP 服务

**文件：** `pptagent/mcp_server.py`

**入口命令：** `pptagent-mcp`（定义于 `pyproject.toml`）

**作用：** 将 **legacy 模板化 PPT 生成流水线** 以独立 MCP 服务器形式暴露给外部 AI 编辑器（Claude、Cursor 等）。

**提供的工具：**

| 工具名 | 说明 |
|--------|------|
| `markdown_table_to_image` | 将 Markdown 表格转换为样式化图片。 |
| `list_templates` | 列出内置模板（`default`、`beamer`、`cip`、`hit`、`thu`、`ucas`）。 |
| `set_template` | 加载模板及其幻灯片归纳（slide induction）元数据。 |
| `create_slide` | 从已加载模板中选择布局；返回该布局的内容 Schema。 |
| `write_slide` | 提交与布局 Schema 匹配的结构化幻灯片元素。校验元素名、图片存在性、文本长度。 |
| `generate_slide` | 执行生成流水线（`_generate_commands` → `_edit_slide`），将幻灯片追加到内部列表。 |
| `save_generated_slides` | 将所有生成的幻灯片编译为 `.pptx` 文件并重置状态。 |

**关键行为：**
- 需要环境变量：`PPTAGENT_MODEL`、`PPTAGENT_API_BASE`、`PPTAGENT_API_KEY`。
- 使用 `pptagent.pptgen.PPTAgent` 作为生成引擎。
- 在多次工具调用间维护会话状态（`self.slides`、`self.layout`、`self.editor_output`）。

---

## 3. MCP 客户端基础设施

### 3.1 MCPClient（`deeppresenter/utils/mcp_client.py`）

- 支持 **stdio**（本地子进程）和 **SSE**（远程 HTTP）两种传输方式。
- 管理每个服务器的 `asyncio.Task` 生命周期。
- 连接超时：`MCP_CONNECT_TIMEOUT`（默认 120 秒）。
- 工具调用超时：`MCP_CALL_TIMEOUT`（默认 1800 秒）。

### 3.2 AgentEnv（`deeppresenter/agents/env.py`）

- 读取 `mcp.json`，将每个条目转换为 `MCPServer` 配置对象。
- 向所有启动的服务器传递工作区相关环境变量（`WORKSPACE`、`HOST_WORKSPACE`、`CONFIG_FILE` 等）。
- 支持 **离线模式**：跳过标记 `network: true` 的服务器。
- 提供工具注册、执行、结果截断、错误处理和耗时统计。
- 支持 **本地工具**（直接在 Agent 进程中注册的 Python 可调用对象）与远程 MCP 工具并存。

---

## 4. 配置与接线

### 4.1 deeppresenter/mcp.json

```json
[
  { "name": "any2markdown", "command": "python3", "args": ["$PACKAGE_DIR/tools/any2markdown.py"], "env": { "MINERU_API_KEY": "..." } },
  { "name": "task",         "command": "python3", "args": ["$PACKAGE_DIR/tools/task.py"] },
  { "name": "deeppresenter","command": "python3", "args": ["$PACKAGE_DIR/tools/reflect.py"] },
  { "name": "tool_agents",  "command": "python3", "args": ["$PACKAGE_DIR/tools/tool_agents.py"] },
  { "name": "research",     "command": "python3", "args": ["$PACKAGE_DIR/tools/research.py"], "network": true },
  { "name": "search",       "command": "python3", "args": ["$PACKAGE_DIR/tools/search.py"],   "network": true, "env": { "TAVILY_API_KEY": "$TAVILY_API_KEY" } },
  { "name": "sandbox",      "command": "docker",  "args": ["run", "--init", "--name", "$WORKSPACE_ID", "-i", "--rm", "--volumes-from", "deeppresenter-host", "-w", "$WORKSPACE", "deeppresenter-sandbox"] }
]
```

**环境变量替换：** `$PACKAGE_DIR`、`$WORKSPACE`、`$WORKSPACE_ID`、`$TAVILY_API_KEY` 等由 `MCPServer._process_escape()` 在运行时解析。

### 4.2 pyproject.toml 入口点

```toml
[project.scripts]
pptagent = "deeppresenter.cli:main"       # 主 CLI（deeppresenter）
pptagent-mcp = "pptagent.mcp_server:main" # 独立 MCP 服务（pptagent）
```

---

## 5. 关系图谱

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         外部 MCP 客户端                                   │
│                    (Claude、Cursor、IDE 插件等)                           │
└─────────────────────────────┬───────────────────────────────────────────┘
                              │ stdio
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    pptagent-mcp  (pptagent/mcp_server.py)                │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐ │
│  │ set_template│→ │ create_slide│→ │ write_slide │→ │ generate_slide  │ │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────────┘ │
│         ↑                                              │                  │
│         └──────────── list_templates ──────────────────┘                  │
│  底层使用：pptagent.pptgen.PPTAgent  +  pptagent.templates.*               │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│                         DeepPresenter Agent 循环                         │
│                    (deeppresenter/main.py、agents/*.py)                   │
└─────────────────────────────┬───────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      AgentEnv  (deeppresenter/agents/env.py)             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐ │
│  │ MCPClient   │  │ tool_execute│  │ connect_    │  │ register_tool   │ │
│  │ (stdio/SSE) │  │             │  │ server      │  │ (local)         │ │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────────┘ │
└────────┬────────────────┬────────────────┬────────────────┬─────────────┘
         │                │                │                │
    ┌────┘          ┌────┘           ┌────┘          ┌────┘
    ▼               ▼                ▼               ▼
┌──────────┐  ┌──────────┐   ┌──────────┐    ┌──────────┐
│any2markdown│  │  task    │   │  search  │    │ sandbox  │  … 其他
└──────────┘  └──────────┘   └──────────┘    └──────────┘
```

---

## 6. 汇总对比

| 服务器 | 类型 | 使用方 | 是否需要网络 |
|--------|------|--------|--------------|
| `any2markdown` | 内部工具 | Research、Planner | 否（MinerU 可选） |
| `task` | 内部工具 | 所有 Agent | 否 |
| `deeppresenter`（reflect） | 内部工具 | Research、Design | 否 |
| `tool_agents` | 内部工具 | 所有 Agent（条件注册） | 是（文生图需联网） |
| `research` | 内部工具 | Research Agent | 是 |
| `search` | 内部工具 | Research、Design | 是 |
| `sandbox` | 内部工具 | Design、PPTAgent | 否（Docker） |
| `pptagent-mcp` | **对外服务** | Claude、Cursor 等 | 是（调用 LLM API） |

两套 MCP 生态 **相互独立**：
- **deeppresenter tools** 是内部 Agent 工具链，通过 `mcp.json` 配置，以编程方式消费。
- **pptagent-mcp** 是面向用户的 MCP 服务器，将 legacy 生成引擎暴露给外部 AI 助手。
