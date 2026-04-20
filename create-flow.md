# DeepPresenter 运作流程

本文档以本地 Docker 部署 + 本地 LLM（llama.cpp / Qwen3.6-35B-A3B）为例，完整阐述 DeepPresenter 从用户输入到 PPTX 产出的运作流程。

## 1. 系统架构总览

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Windows 宿主机                                │
│                                                                     │
│  ┌──────────────────┐    ┌──────────────────────────────────────┐  │
│  │  llama-server    │    │  Docker Desktop                      │  │
│  │  :8989           │    │                                      │  │
│  │  (本地 LLM)      │    │  ┌────────────────────────────────┐  │  │
│  │                  │    │  │  deeppresenter-host 容器        │  │  │
│  │  OpenAI 兼容 API  │    │  │                                │  │  │
│  │  /v1/chat/...    │◄───┼──┤  Gradio WebUI (:7861)         │  │  │
│  │                  │    │  │  AgentLoop                      │  │  │
│  └──────────────────┘    │  │  MCPClient → 7个MCP工具服务    │  │  │
│                          │  │      │                          │  │  │
│                          │  │      ├─ python3 task.py        │  │  │
│                          │  │      ├─ python3 reflect.py     │  │  │
│                          │  │      ├─ python3 search.py ─────┼──┼──┼─► Internet
│                          │  │      ├─ python3 research.py ───┼──┼──┼─► arXiv/S2
│                          │  │      ├─ python3 any2markdown.py│  │  │
│                          │  │      ├─ python3 tool_agents.py │  │  │
│                          │  │      └─ docker run sandbox ────┼──┼──┼─► Docker API
│                          │  │                                │  │  │
│                          │  │  ┌──────────────────────────┐  │  │  │
│                          │  │  │ deeppresenter-sandbox 容器│  │  │  │
│                          │  │  │ (按需启动，执行命令/代码) │  │  │  │
│                          │  │  └──────────────────────────┘  │  │  │
│                          │  └────────────────────────────────┘  │  │
│                          └──────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

## 2. Docker 容器与代码库交互

### 2.1 容器结构

系统由两个 Docker 镜像组成：

| 镜像 | 用途 | 启动方式 |
|------|------|---------|
| `deeppresenter-host` | 主服务：Gradio WebUI + Agent 编排 + MCP 工具服务 | `docker compose up` 常驻运行 |
| `deeppresenter-sandbox` | 沙箱：执行 LLM 发出的 shell 命令和代码 | 由 MCP 工具按需 `docker run` 创建 |

### 2.2 卷挂载

```yaml
# docker-compose.yml 中的关键挂载
volumes:
  - ./workspace:/opt/workspace                       # 工作区：产出文件
  - ./deeppresenter:/usr/src/pptagent/deeppresenter   # 代码库：热更新
  - /usr/src/pptagent/deeppresenter/html2pptx/node_modules  # 匿名卷：保护容器内依赖
  - /var/run/docker.sock:/var/run/docker.sock         # Docker Socket：DinD
```

- **`./workspace:/opt/workspace`**：Host 通过 `./workspace` 访问，容器内通过 `/opt/workspace` 访问。所有 PPT 产出文件都在此目录下。
- **`./deeppresenter:/usr/src/pptagent/deeppresenter`**：将本地源码挂载进容器，修改代码后重启容器即可生效（无需重新 build 镜像）。
- **`/var/run/docker.sock`**：host 容器通过此 Socket 控制 Docker daemon，实现 Docker-in-Docker — 动态创建 sandbox 容器。

### 2.3 Sandbox 容器的动态创建

sandbox 不是预运行的，而是当 Design Agent 需要 `execute_command` 等工具时，通过 MCP 协议触发：

```
mcp.json 中的 sandbox 定义:
  command: docker
  args: [run, --init, --name, $WORKSPACE_ID, -i, --rm,
         --volumes-from, deeppresenter-host, -w, $WORKSPACE,
         deeppresenter-sandbox]
```

- `--volumes-from deeppresenter-host`：继承 host 容器的所有卷，sandbox 可以直接读写 workspace
- `--name $WORKSPACE_ID`：以 workspace ID 命名，确保同时只有一个 sandbox 实例
- `-i --rm`：交互模式，退出后自动删除容器
- sandbox 容器预装了 Python 3.13、Node.js、mermaid-cli、python-pptx 等工具

### 2.4 路径映射

```
宿主机视角                  Host 容器视角                    Sandbox 容器视角
./workspace/20260420/xx/  → /opt/workspace/20260420/xx/  → /opt/workspace/20260420/xx/
                           (DEEPPRESENTER_WORKSPACE_BASE)   (继承自 host 的卷)
```

`DEEPPRESENTER_HOST_WORKSPACE_BASE` 环境变量告诉容器内代码如何将容器路径映射回宿主机路径，用于 sandbox 的卷挂载参数构造。

## 3. 网络模型

### 3.1 网络拓扑

```
浏览器 ──HTTP──► deeppresenter-host:7861 (Gradio)
                    │
                    ├── OpenAI API ──► host.docker.internal:8989 (llama-server)
                    │                   (config.yaml: research_agent.base_url)
                    │
                    ├── HTTP ──► Internet (search, fetch_url)
                    │
                    └── Docker API ──► /var/run/docker.sock
                                        └── 创建 sandbox 容器
```

### 3.2 LLM 通信

容器内通过 `host.docker.internal` 访问宿主机上的 llama-server：

```yaml
# config.yaml
research_agent:
  base_url: "http://host.docker.internal:8989/v1"  # Docker 网关 → 宿主机
  model: "Qwen3.6-35B-A3B-UD-Q3_K_XL.gguf"
  api_key: "not-needed"
```

`host.docker.internal` 是 Docker Desktop 提供的特殊 DNS 名称，解析到宿主机的内部 IP，使容器可以访问宿主机上监听的服务。

### 3.3 外网访问

`search.py` 中的 `fetch_url` 和 `search_web` 需要外网访问（SerpAPI/Tavily、目标网站）。流量路径：

```
host 容器 → Docker NAT → 宿主机网络 → Internet
```

如果设置了 `http_proxy`，`GLOBAL_ENV_LIST` 会将代理环境变量传递给所有 MCP 子进程。

## 4. MCP 工具服务

MCP (Model Context Protocol) 是 LLM 调用工具的通信协议。每个 MCP 服务是一个独立的子进程，通过 stdin/stdout 与主进程通信。

### 4.1 连接流程

```
AgentEnv.__aenter__()
  │
  ├── 读取 mcp.json，过滤 offline_mode 下的 network 服务
  │
  ├── 并行连接所有 MCP 服务 (asyncio.gather)
  │     │
  │     └── MCPClient.connect_server()
  │           │
  │           ├── 创建 asyncio.Task (mcp_session_runner)
  │           │     └── 启动子进程 (StdioServerParameters)
  │           │         python3 $PACKAGE_DIR/tools/xxx.py
  │           │     └── 建立 ClientSession
  │           │     └── list_tools() → 注册工具到 _tools_dict
  │           │     └── 阻塞等待 stop_event
  │           │
  │           └── await ready_event.wait()  # 等待子进程就绪
  │
  └── 注册本地工具 (finalize, thinking, delegate_subagent)
```

### 4.2 七个 MCP 服务

| 服务 | 进程命令 | 工具 | 外网 | 说明 |
|------|---------|------|------|------|
| **task** | `python3 task.py` | `finalize`, `thinking`, `todo_*` | 否 | 任务管理、终止判定 |
| **deeppresenter** | `python3 reflect.py` | `inspect_slide`, `inspect_manuscript` | 否 | 产出物检查 |
| **tool_agents** | `python3 tool_agents.py` | `image_generation`, `image_caption`, `document_summary` | 否 | LLM 驱动的辅助工具 |
| **any2markdown** | `python3 any2markdown.py` | 文件转换 | 否 | PDF/DOCX → Markdown |
| **research** | `python3 research.py` | `search_papers`, `get_paper_authors`, `get_scholar_details` | 是 | 学术论文检索 |
| **search** | `python3 search.py` | `search_web`, `search_images`, `fetch_url`, `download_file` | 是 | 网页搜索与抓取 |
| **sandbox** | `docker run deeppresenter-sandbox` | `execute_command`, `read_file`, `write_file` 等 | 否 | 代码/命令执行沙箱 |

### 4.3 环境变量传递

每个 MCP 子进程都会收到一组环境变量，用于路径替换和配置传递：

```python
envs = {
    "WORKSPACE": "/opt/workspace/20260420/xxxx",  # 当前工作区绝对路径
    "HOST_WORKSPACE": "./workspace/20260420/xxxx", # 宿主机视角路径
    "WORKSPACE_ID": "xxxx",                        # 工作区唯一ID
    "CONFIG_FILE": "/usr/src/pptagent/deeppresenter/config.yaml",
    "PACKAGE_DIR": "/usr/src/pptagent/deeppresenter",
    # + GLOBAL_ENV_LIST 中的变量 (proxy, DOCKER_API_VERSION 等)
}
```

`mcp.json` 中的 `$WORKSPACE`, `$PACKAGE_DIR` 等占位符由 `MCPServer._process_escape()` 在运行时替换。

## 5. 与 LLM 的交互

### 5.1 LLM 调用链

```
Agent.action()
  └── LLM.run()
        └── Endpoint.call()
              └── AsyncOpenAI.chat.completions.create(
                    model=..., messages=..., tools=..., tool_choice="auto"
                  )
                    └── HTTP POST → http://host.docker.internal:8989/v1/chat/completions
```

### 5.2 消息格式

LLM 接收的消息是 OpenAI Chat Completion 格式：

```json
[
  {"role": "system",  "content": "你是一位专业的幻灯片内容专家..."},
  {"role": "user",    "content": "针对儿童创作自然灾害科普的ppt"},
  {"role": "assistant","content": null, "tool_calls": [{"function": {"name": "search_web", "arguments": "{...}"}}]},
  {"role": "tool",    "content": [{"type": "text", "text": "{...}"}], "tool_call_id": "..."},
  ...
]
```

### 5.3 Tool Call 循环

每个 Agent 的核心循环：

```
while True:
    1. action() → 发送 chat_history 给 LLM，获取 tool_calls
    2. execute(tool_calls) → 并行执行所有 tool call
    3. 将 tool 结果追加到 chat_history
    4. 如果 finalize 被调用 → 返回结果，退出循环
    5. 否则 → 回到步骤 1
```

### 5.4 模型分配

每个 Agent 通过 `roles/*.yaml` 中的 `use_model` 字段指定使用哪个 LLM 配置：

| Agent | use_model | 配置来源 |
|-------|-----------|---------|
| Research | `research_agent` | `config.yaml: research_agent` |
| Design | `design_agent` | `config.yaml: design_agent` |
| PPTAgent | `research_agent` | `config.yaml: research_agent` |
| Planner | 由 yaml 决定 | `config.yaml` |
| SubAgent | 继承父 Agent | 同父 Agent |

在本地部署中，三个模型槽位指向同一个 llama-server 实例。

### 5.5 Context Folding

当对话历史接近 `context_window` 限制时：

```
context_length > context_window (默认 40000 tokens)
  │
  └── compact_history()
        ├── 保留前 10 条消息 (system + user + 初始交互)
        ├── 保留最近 4 条消息
        ├── 让 LLM 生成摘要 → 保存到本地文件
        └── 替换中间历史为摘要 + 工具执行结果
```

这允许 Agent 在有限上下文内持续工作，最多折叠 `max_context_folds`（默认 5）次。

## 6. 第三方服务交互

### 6.1 搜索服务（可选）

| 服务 | 用途 | 需要 API Key |
|------|------|-------------|
| SerpAPI | Google 搜索/图片搜索 | `SERPAPI_KEY` |
| Tavily | AI 增强搜索 | `TAVILY_API_KEY` |

二选一，优先 SerpAPI。无 key 时搜索工具不注册，Agent 仍可运行但无法联网搜索。

### 6.2 网页抓取

```
fetch_url(url)
  ├── httpx.HEAD → 检测 Content-Type
  ├── Playwright goto → 渲染 JS 动态页面
  ├── markdownify → HTML → Markdown
  └── trafilatura → 提取正文
```

### 6.3 学术检索

- **arXiv**：通过 `arxiv` Python 包搜索论文
- **Semantic Scholar**：通过 `semanticscholar` 包获取作者/引用信息

### 6.4 文件转换（MinerU）

- `any2markdown.py` 调用 MinerU API 将 PDF/DOCX 转为 Markdown
- 需要 `MINERU_API_KEY`

### 6.5 图像/视觉（可选）

| 模型 | 用途 | 配置键 |
|------|------|-------|
| `vision_model` | 图片描述 (caption) | `config.yaml: vision_model` |
| `t2i_model` | 文生图 | `config.yaml: t2i_model` |

不配置时，相关工具不注册。Agent 会跳过图片生成/描述步骤。

## 7. PPT 生成全流程

### 7.1 阶段一：用户输入

```
浏览器 → Gradio WebUI (:7861) → send_message()
  │
  ├── instruction: "针对儿童创作自然灾害科普的ppt"
  ├── convert_type: "deeppresenter" (自由生成) 或 "pptagent" (模板)
  ├── num_pages: auto / 指定页数
  └── attachments: 附件文件路径列表
```

### 7.2 阶段二：Research Agent（文稿撰写）

```
Research Agent 启动
  │
  ├── 加载 Research.yaml (system prompt + toolset)
  ├── system prompt: "你是一位专业的幻灯片内容专家..."
  ├── instruction template: "{{prompt}}"
  │
  └── while True 循环:
        │
        ├── LLM 决策 → 调用工具:
        │     ├── search_web("自然灾害 儿童科普") → 获取搜索结果
        │     ├── fetch_url("https://...") → 抓取网页内容
        │     ├── download_file(url, path) → 下载图片素材
        │     ├── write_file("manuscript.md", content) → 写入文稿
        │     ├── inspect_manuscript("manuscript.md") → 检查文稿质量
        │     └── ...
        │
        └── finalize(outcome="/opt/workspace/.../manuscript.md")
              └── 验证 .md 文件存在 → 重写图片链接为绝对路径 → 返回路径
```

**文稿格式**：Markdown，每页用 `---` 分隔，图片用本地绝对路径引用：

```markdown
# 地震是什么

地震是地球表面产生的振动...

![earthquake Diagram 地震示意图 3:2](/opt/workspace/.../assets/earthquake.png)

---

# 如何保护自己

...
```

### 7.3 阶段三：Design Agent（幻灯片制作）— 自由生成模式

```
Design Agent 启动
  │
  ├── 加载 Design.yaml (system prompt + toolset)
  ├── toolset: sandbox + inspect_slide + thinking + finalize
  ├── system prompt: "你是一位专业的幻灯片视觉设计专家..."
  │
  └── while True 循环:
        │
        ├── LLM 决策 → 调用工具:
        │     ├── read_file("manuscript.md") → 读取文稿
        │     ├── write_file("design_plan.md", ...) → 制定设计方案
        │     ├── write_file("slides/slide_01.html", ...) → 生成第1页HTML
        │     ├── inspect_slide("slides/slide_01.html") → 验证HTML
        │     │     └── convert_html_to_pptx(slide_path, validate_only=True)
        │     │         └── node html2pptx_cli.js --validate
        │     ├── write_file("slides/slide_02.html", ...) → 生成第2页
        │     ├── inspect_slide("slides/slide_02.html") → 验证
        │     ├── ... (重复直到所有页面生成)
        │     └── finalize(outcome="/opt/workspace/.../slides")
        │
        └── finalize 返回 slides 目录路径
```

**每页 HTML 幻灯片**的格式要求：
- 固定尺寸（16:9 = 1280×720px）
- 自包含：CSS 内联，图片通过绝对路径引用
- 语义化 HTML：`<p>`, `<li>`, `<span>` 包裹文本

### 7.4 阶段三（替代）：PPTAgent — 模板模式

当用户选择"模版 (templates)"时走此路径：

```
PPTAgent 启动
  │
  ├── toolset: 所有 MCP 服务（包含 pptagent 模板工具）
  ├── 不生成 HTML，而是调用 pptagent 的模板渲染工具
  └── 直接输出 .pptx 文件
```

此模式使用预定义的 PPT 模板（beamer、default、thu 等），对 LLM 能力要求更低。

### 7.5 阶段四：HTML → PPTX 转换

```
Design Agent 返回 slides/ 目录
  │
  ├── convert_html_to_pptx(slides_dir, output.pptx)
  │     └── 子进程: node html2pptx_cli.js --html_dir /opt/.../slides --output /opt/.../output.pptx
  │           │
  │           ├── 遍历 slide_01.html, slide_02.html, ...
  │           ├── Playwright 渲染每个 HTML → 截图
  │           ├── 解析 HTML 元素 → 映射到 pptxgenjs 对象
  │           └── 输出 .pptx 文件
  │
  └── 如果 html2pptx 失败:
        └── 回退到 Playwright → PDF
              └── PlaywrightConverter.convert_to_pdf()
                    ├── 逐页 goto HTML → page.pdf()
                    ├── PdfWriter 合并所有页面
                    └── pdf2image 生成预览图
```

### 7.6 产出文件结构

```
workspace/20260420/xxxx/
├── .input_request.json          # 用户输入记录
├── .history/                    # 交互历史
│   ├── deeppresenter-loop.log
│   ├── Research-00-history.jsonl
│   ├── Design-00-history.jsonl
│   ├── Design-config.json
│   └── tools_time_cost.json
├── intermediate_output.json     # 中间产物路径
├── manuscript.md                # Research Agent 产出的文稿
├── design_plan.md               # Design Agent 的设计方案
├── slides/                      # HTML 幻灯片
│   ├── slide_01.html
│   ├── slide_02.html
│   └── ...
├── assets/                      # 图片素材
│   ├── earthquake.png
│   └── ...
└── manuscript.pptx              # 最终 PPTX 产出
```

## 8. 完整时序图

```
用户          Gradio UI      AgentLoop      Research Agent    Design Agent    MCP工具服务      LLM          sandbox
 │               │              │               │                │              │            │             │
 │─输入提示──►    │              │               │                │              │            │             │
 │               │──run()──►    │               │                │              │            │             │
 │               │              │──初始化──►     │                │              │            │             │
 │               │              │──连接MCP─────────────────────────────────►   │            │             │
 │               │              │               │                │              │            │             │
 │               │              │  ════════ Research 阶段 ════════              │            │             │
 │               │              │──loop()──────►│                │              │            │             │
 │               │              │               │──action()──────┼──────────────────────────►│             │
 │               │              │               │◄──tool_calls───┼──────────────┼────────────│             │
 │               │              │               │──execute()─────┼──search_web──►│            │             │
 │               │              │               │               │──fetch_url───►│            │             │
 │               │              │               │               │──download────►│            │             │
 │               │              │               │──write_file────┼──sandbox─────┼──────────────────────────►│
 │               │              │               │──inspect_ms───┼──reflect─────►│            │             │
 │               │              │               │──finalize()────┼──task────────►│            │             │
 │               │              │               │  ← manuscript.md ─            │            │             │
 │               │              │               │                │              │            │             │
 │               │              │  ════════ Design 阶段 ════════                │            │             │
 │               │              │──loop()───────────────────────►│              │            │             │
 │               │              │               │                │──action()────┼──────────────────────────►│
 │               │              │               │                │◄─tool_calls──┼──────────────┼────────────│
 │               │              │               │                │──read_file───┼──sandbox─────┼─────────────────────────►│
 │               │              │               │                │──write(s1)───┼──sandbox─────┼─────────────────────────►│
 │               │              │               │                │──inspect(s1)─┼──reflect─────►│            │             │
 │               │              │               │                │──write(s2)───┼──sandbox─────┼─────────────────────────►│
 │               │              │               │                │──inspect(s2)─┼──reflect─────►│            │             │
 │               │              │               │                │──...         │              │            │             │
 │               │              │               │                │──finalize()──┼──task────────►│            │             │
 │               │              │               │                │  ← slides/   │              │            │             │
 │               │              │               │                │              │            │             │
 │               │              │  ════════ 转换阶段 ════════                   │            │             │
 │               │              │──convert_html_to_pptx()──────►│              │            │             │
 │               │              │              │               │  node html2pptx_cli.js       │             │
 │               │              │              │               │  → manuscript.pptx            │             │
 │               │              │              │               │                │              │            │             │
 │◄──下载PPTX──  │              │              │               │                │              │            │             │
```

## 9. 关键配置说明

### 9.1 本地 LLM 部署配置要点

| 配置项 | 值 | 说明 |
|--------|-----|------|
| `base_url` | `http://host.docker.internal:8989/v1` | 容器内访问宿主机 |
| `model` | 模型文件名 | 需与 llama-server 注册名一致 |
| `api_key` | `not-needed` | llama-server 不鉴权 |
| `is_multimodal` | `false`（自动检测） | 本地模型通常不支持视觉 |
| `DOCKER_API_VERSION` | `1.44` | 需与 Docker Desktop 版本匹配 |
| `DEEPPRESENTER_HOST_WORKSPACE_BASE` | `./workspace` | 宿主机相对路径，用于 sandbox 卷挂载 |

### 9.2 环境变量传递链

```
.env (宿主机)
  └── docker-compose.yml (environment)
        └── 容器内 os.environ
              └── AgentEnv.envs
                    └── MCPClient.envs
                          └── MCPServer._process_escape()
                                └── 子进程环境变量 (StdioServerParameters.env)
```

`GLOBAL_ENV_LIST` 中列出的变量（proxy、DOCKER_API_VERSION 等）会在每层传递时自动注入。

## 10. 幻灯片中的图片元素来源

最终 PPTX 中的视觉元素并非由 LLM 直接生成像素，而是通过多种途径获取和构建的。理解这一点对于解释输出质量至关重要。

### 10.1 图片素材的四种来源

| 来源 | 工具 | 触发阶段 | 是否需要外部服务 | 本地 LLM 场景是否可用 |
|------|------|---------|----------------|---------------------|
| **网络下载** | `search_images` + `download_file` | Research | SerpAPI/Tavily | 需 API Key |
| **网页抓取** | `fetch_url` | Research | 无 | 可用 |
| **AI 文生图** | `image_generation` | Research/Design | t2i_model API | 需配置 |
| **纯代码绘制** | `write_file` (HTML/CSS/SVG) | Design | 无 | 可用（主要方式） |

### 10.2 网络下载：search_images + download_file

Research Agent 通过搜索 + 下载获取真实图片：

```
LLM 决策: "需要一张地震示意图"
  |
  ├── search_images("earthquake diagram")    <- search.py, 调用 SerpAPI Google Images
  |     └── 返回: [{url: "https://.../eq.png", thumbnail: "...", description: "..."}]
  |
  └── download_file("https://.../eq.png", "/opt/workspace/.../assets/earthquake.png")
        <- search.py, httpx 下载 + PIL 验证/转换格式
        └── 保存到 workspace/assets/ 目录
```

这是获取**真实照片和高质量素材图**的主要途径。但在本地 LLM 场景下，如果没有配置 SerpAPI/Tavily Key，`search_images` 工具不会被注册，LLM 就无法调用它。

### 10.3 AI 文生图：image_generation

如果 `config.yaml` 中配置了 `t2i_model`：

```yaml
t2i_model:
  base_url: "https://ark.cn-beijing.volces.com/api/v3"
  model: "doubao-seedream-4.5-251128"
  api_key: "your_key"
```

则 `tool_agents.py` 会注册 `image_generation` 工具，LLM 可以直接生成图片：

```
LLM 决策: "需要一张卡通地震插图"
  |
  └── image_generation(
        prompt="cute cartoon illustration of earthquake safety for kids",
        width=640, height=480,
        path="/opt/workspace/.../assets/earthquake_illustration.png"
      )
        <- tool_agents.py, 调用 t2i_model API (DALL-E/Stable Diffusion 等)
        └── 保存为 PNG
```

**在本地 LLM 场景下，`t2i_model` 通常未配置，此工具不可用。**

### 10.4 纯代码绘制：HTML/CSS/SVG（本地 LLM 的主要方式）

这是最关键的来源 — **你看到的幻灯片中的图标、装饰线条、渐变背景、圆角卡片等，都不是图片文件，而是 LLM 编写的 HTML/CSS 代码。**

Design Agent 的工作不是操作像素，而是**写代码**。它生成的 `slide_01.html` 内容类似：

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<style>
  body { width: 1280px; height: 720px; margin: 0;
         background: linear-gradient(135deg, #1a1a2e, #16213e); }
  .card { border-radius: 16px; padding: 24px;
          background: rgba(255,255,255,0.08); }
  .icon { font-size: 48px; }
  .step-number { width: 36px; height: 36px; border-radius: 50%;
                  background: #ff6b6b; color: white;
                  display: flex; align-items: center; justify-content: center; }
</style>
</head>
<body>
  <div class="card">
    <div class="icon">🌍</div>           <!-- Emoji 作为图标 -->
    <h2>什么是地震？</h2>
    <p>地震是地球表面产生的振动...</p>
  </div>
  <div class="card">
    <div class="icon">🛡️</div>          <!-- Emoji 作为图标 -->
    <h2>地震来了怎么办？</h2>
    <div class="step-number">1</div>    <!-- CSS 圆形 -->
    <p>蹲下！</p>
  </div>
</body>
</html>
```

具体来说，你看到的视觉元素实际构成如下：

| 你看到的 | 实际来源 | 是否为图片文件 |
|---------|---------|-------------|
| 渐变背景 | CSS `linear-gradient` | 否 |
| 圆角卡片 | CSS `border-radius` + `background` | 否 |
| 图标（🌍💥🛡️⭐）| Unicode Emoji 字符 | 否 |
| 数字圆圈 | CSS `border-radius: 50%` + `background` | 否 |
| 分隔线 | CSS `border` 或 `<hr>` | 否 |
| 颜色高亮 | CSS `color` / `background-color` | 否 |
| 照片/插图 | `<img src="/path/to/file.png">` | 是（如果有的话） |

**在纯本地 LLM（无搜索 API、无文生图模型）的场景下，幻灯片几乎完全由 HTML/CSS 构成。** LLM 的设计能力决定了视觉效果的上限。

### 10.5 Mermaid 图表

sandbox 容器预装了 `mermaid-cli`（mmdc），Design Agent 可以通过 `execute_command` 生成流程图、架构图等：

```
LLM 决策: "需要一个地震应急流程图"
  |
  └── execute_command("mmdc -i flowchart.mmd -o flowchart.png")
        <- sandbox 中执行 shell 命令
        └── Mermaid 文本描述 -> PNG 图片
```

Mermaid 输入是纯文本描述：

```
graph TD
    A[感觉震动] --> B{能否撤离?}
    B -->|能| C[有序撤离到空旷地带]
    B -->|不能| D[蹲下-掩护-抓紧]
```

这为本地 LLM 提供了一种无需外部 API 即可生成结构化图表的途径。

### 10.6 图片来源决策树

```
Design Agent 需要视觉元素
  |
  ├── 装饰性元素（背景、圆角、线条、颜色）
  |     └── 直接用 CSS 编写 <- 最常见，无需任何外部依赖
  |
  ├── 图标
  |     ├── Unicode Emoji <- 零依赖，LLM 直接输出
  |     ├── SVG 代码 <- LLM 编写矢量图代码
  |     └── 图标字体 <- 需网络加载（受限）
  |
  ├── 结构化图表（流程图、架构图）
  |     └── Mermaid -> mmdc -> PNG <- sandbox 本地执行
  |
  ├── 照片/真实图片
  |     ├── search_images + download_file <- 需 SerpAPI/Tavily Key
  |     └── image_generation <- 需 t2i_model
  |
  └── 数据可视化
        └── Python matplotlib/plotly -> PNG <- sandbox 本地执行
```

### 10.7 html2pptx 如何处理这些元素

最终转换阶段，`html2pptx_cli.js` 将 HTML 转为 PPTX 时：

```
HTML 元素              PPTX 映射
---------------------------------------------------------
<p> 文字              -> TextRange (文本框)
<img src="...">        -> SlideImage (嵌入图片)
CSS background-image   -> SlideImage (渲染为图片后嵌入)
CSS gradient           -> 渲染为位图 -> SlideImage
CSS border-radius      -> 渲染为位图 -> SlideImage
Emoji 字符             -> 渲染为位图 -> SlideImage
SVG 内联               -> 渲染为位图 -> SlideImage
```

关键点：**HTML/CSS 构建的所有视觉元素在转换为 PPTX 时，都会被 Playwright 渲染为位图后嵌入。** 这意味着即使原 HTML 使用纯代码绘制，最终 PPTX 中它们也是图片形式存在 — 但源头上它们是代码，不是外部图片文件。
