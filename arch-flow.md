# PPTAgent/DeepPresenter 架构与运行流程分析

本文档阐述 PPTAgent/DeepPresenter 项目的总体架构、运行流程、组件协作、网络拓扑、配置机制和外部依赖。

---

## 1. 总体架构

### 1.1 核心组件

```
┌─────────────────────────────────────────────────────────────┐
│                         CLI / WebUI                          │
│  (deeppresenter/cli/ or webui.py)                           │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                      AgentLoop                              │
│  (deeppresenter/main.py) - 主协调器                         │
│  - 管理工作空间                                              │
│  - 协调 Agent 执行顺序                                      │
│  - 处理中间输出和最终结果                                    │
└────────────────────────┬────────────────────────────────────┘
                         │
         ┌───────────────┼───────────────┐
         │               │               │
         ▼               ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│  Planner     │ │  Research    │ │  Design/     │
│  (可选)      │ │  Agent       │ │  PPTAgent    │
└──────────────┘ └──────┬───────┘ └──────┬───────┘
                       │                │
                       └────────┬───────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────┐
│                      AgentEnv                               │
│  (deeppresenter/agents/env.py)                             │
│  - 管理 MCP 工具服务器连接                                  │
│  - 工具执行和结果缓存                                       │
│  - Docker 沙箱管理                                          │
└────────────────────────┬────────────────────────────────────┘
                         │
         ┌───────────────┼───────────────┐
         │               │               │
         ▼               ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ MCP Servers  │ │ MCP Servers  │ │ MCP Servers  │
│ (stdio)      │ │ (stdio)      │ │ (docker)     │
└──────────────┘ └──────────────┘ └──────────────┘
```

### 1.2 Agent 继承关系

```
Agent (基类)
├── Research - 研究代理，生成手稿
├── Design - 设计代理，生成 HTML 幻灯片
├── PPTAgent - 模板模式代理，生成 PPTX
├── Planner - 规划代理，生成大纲（可选）
└── SubAgent - 子代理（多代理模式）
```

### 1.3 配置系统

```
DeepPresenterConfig
├── 全局配置
│   ├── offline_mode - 离线模式
│   ├── context_folding - 上下文折叠
│   ├── multiagent_mode - 多代理模式
│   └── heavy_reflect - 深度反思
├── LLM 配置
│   ├── research_agent - 研究代理模型
│   ├── design_agent - 设计代理模型
│   ├── long_context_model - 长上下文模型
│   ├── vision_model - 视觉模型（可选）
│   └── t2i_model - 文本生成图像模型（可选）
└── MCP 配置文件路径
```

---

## 2. 运行流程

### 2.1 完整流程图

```
用户请求 (InputRequest)
│
├─► 复制附件到工作空间
│
├─► 创建 AgentEnv
│   ├─► 连接 MCP 服务器
│   │   ├─► any2markdown (文档转换)
│   │   ├─► task (任务管理)
│   │   ├─► deeppresenter (反思工具)
│   │   ├─► tool_agents (工具代理)
│   │   ├─► research (学术搜索)
│   │   ├─► search (网页搜索)
│   │   └─► sandbox (Docker 沙箱)
│   └─► 注册本地工具（如 SubAgent）
│
├─► [可选] Planner 阶段
│   ├─► 生成大纲
│   ├─► 用户交互式编辑
│   └─► 保存 outline.json
│
├─► Research 阶段
│   ├─► 使用工具：搜索、文档转换、学术搜索
│   ├─► 上下文管理（context folding）
│   ├─► 生成手稿（markdown）
│   └─► 调用 finalize 完成阶段
│
├─► 生成阶段（二选一）
│   │
│   ├─► PPTAgent 模式（模板驱动）
│   │   ├─► 使用模板生成 PPTX
│   │   └─► 输出 .pptx
│   │
│   └─► Design 模式（自由设计）
│       ├─► 生成 HTML 幻灯片
│       ├─► 图片检索/生成
│       ├─► HTML 转 PPTX
│       │   ├─► Node.js html2pptx (首选)
│       │   └─► Playwright PDF 转换（降级）
│       └─► 输出 .pptx 或 .pdf
│
└─► 保存中间输出和日志
```

### 2.2 详细阶段说明

#### 阶段 1: 初始化
```python
# deeppresenter/main.py:AgentLoop.__init__
- 创建工作空间: ~/.cache/deeppresenter/{session_id}
- 加载配置: DeepPresenterConfig
- 设置日志: workspace/.history/deeppresenter-loop.log
```

#### 阶段 2: AgentEnv 启动
```python
# deeppresenter/agents/env.py:AgentEnv.__aenter__
- 读取 mcp.json 配置
- 过滤离线模式下的网络工具
- 连接所有 MCP 服务器（异步并发）
- 注册本地工具（SubAgent）
- 初始化 Docker 沙箱（清理旧容器）
```

#### 阶段 3: Planner（可选）
```python
# deeppresenter/main.py:AgentLoop.run (第 87-110 行)
if request.enable_planner:
    - 创建 Planner Agent
    - 执行 loop() 生成大纲
    - CLI 环境下支持交互式编辑
    - 保存 outline.json
```

#### 阶段 4: Research
```python
# deeppresenter/agents/research.py
while True:
    - 调用 LLM（research_agent）
    - LLM 决定调用工具
    - 执行工具：
        * search_web - 网页搜索
        * search_papers - 学术搜索
        * convert_to_markdown - 文档转换
        * fetch_url - 网页抓取
        * execute_command - 沙箱命令
    - 上下文折叠（超过窗口时）
    - LLM 调用 finalize 完成阶段
```

#### 阶段 5: Design / PPTAgent

**Design 模式流程**:
```python
# deeppresenter/agents/design.py
while True:
    - 调用 LLM（design_agent）
    - LLM 生成 HTML 幻灯片
    - 图片处理：
        * search_images - 网页图片搜索
        * generate_image - 文本生成图像（t2i_model）
    - 保存 HTML 文件
    - 调用 finalize 完成

# HTML 转 PPTX
- 尝试 Node.js html2pptx（deeppresenter/html2pptx/）
- 失败则降级到 Playwright PDF 转换
```

**PPTAgent 模式流程**:
```python
# deeppresenter/agents/pptagent.py
- 使用模板库（pptagent/templates/）
- 布局诱导（layout induction）
- 直接生成 PPTX 文件
```

---

## 3. 组件协作机制

### 3.1 Agent 基类工作原理

```python
# deeppresenter/agents/agent.py
class Agent:
    def __init__(self):
        - 加载角色配置 (roles/{AgentName}.yaml)
        - 选择 LLM 端点（从 config）
        - 构建工具集（从 AgentEnv）
        - 构建 system prompt
        - 初始化聊天历史
    
    async def action():
        - 调用 LLM.run(messages, tools)
        - LLM 返回 tool_calls
        - 返回 ChatMessage
    
    async def execute(tool_calls):
        - 并发执行所有工具调用
        - AgentEnv.tool_execute()
        - 处理工具结果
        - 上下文管理（警告、折叠）
        - 检查 finalize 调用
```

### 3.2 MCP 工具调用流程

```
Agent.action()
  │
  ├─► LLM 决定调用工具
  │   └─► 返回 tool_calls: [{name: "search_web", arguments: {...}}]
  │
  ▼
Agent.execute(tool_calls)
  │
  ├─► 并发调用 AgentEnv.tool_execute()
  │   │
  │   ├─► 判断工具类型
  │   │   ├─► 本地工具 → _call_local_tool()
  │   │   └─► MCP 工具 → MCPClient.tool_execute()
  │   │       │
  │   │       ├─► 查找工具所属服务器
  │   │       ├─► session.call_tool(name, params)
  │   │       └─► 等待结果（超时控制）
  │   │
  │   ├─► 结果截断（超过 TOOL_CUTOFF_LEN）
  │   ├─► 保存到文件（避免上下文溢出）
  │   └─► 包装为 ChatMessage
  │
  ├─► 更新聊天历史
  ├─► 上下文警告（50%, 80% 阈值）
  └─► 上下文折叠（超过窗口时）
```

### 3.3 上下文管理机制

```python
# deeppresenter/agents/agent.py:Agent.compact_history()
触发条件:
- context_length > context_window
- 且 context_folding = True

折叠步骤:
1. 保存当前历史到文件
2. 分割历史：保留头部 10 条、尾部 4 条
3. 调用 LLM 总结中间部分
4. LLM 可能调用工具保存摘要
5. 替换中间部分为摘要
6. 增加 research_iter 计数
7. 达到 max_context_folds 时停止折叠
```

---

## 4. 网络拓扑

### 4.1 整体网络架构

```
┌─────────────────────────────────────────────────────────────┐
│                    DeepPresenter 主进程                      │
│                    (Python AsyncIO)                          │
└────────────────────────┬────────────────────────────────────┘
                         │
         ┌───────────────┼───────────────┬───────────────┐
         │               │               │               │
         ▼               ▼               ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ MCP Server   │ │ MCP Server   │ │ MCP Server   │ │ Docker       │
│ (stdio)      │ │ (stdio)      │ │ (stdio)      │ │ Container    │
│              │ │              │ │              │ │ (sandbox)    │
│ any2markdown │ │ research     │ │ search       │ │              │
└──────┬───────┘ └──────┬───────┘ └──────┬───────┘ └──────┬───────┘
       │                │                │                │
       │                │                │                │
       ▼                ▼                ▼                ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ MinerU API   │ │ arXiv API    │ │ Tavily/      │ │ 内置工具集   │
│ (可选)       │ │ Semantic     │ │ SerpAPI      │ │ (matplotlib, │
│              │ │ Scholar      │ │              │ │  pandas, etc)│
└──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘
                         │
                         │
         ┌───────────────┼───────────────┬───────────────┐
         │               │               │               │
         ▼               ▼               ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ LLM API 1    │ │ LLM API 2    │ │ LLM API 3    │ │ T2I API      │
│ (research)   │ │ (design)     │ │ (long)       │ │ (image gen)  │
└──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘
```

### 4.2 MCP 连接方式

**Stdio 连接（本地进程）**:
```python
# deeppresenter/utils/mcp_client.py:connect_to_server()
- 使用 subprocess 启动 Python 进程
- 通过 stdin/stdout 通信
- 适用于：any2markdown, research, search, task, reflect
```

**SSE 连接（远程）**:
```python
# deeppresenter/utils/mcp_client.py:connect_to_server_sse()
- 通过 HTTP SSE 连接
- 适用于远程 MCP 服务器
- 当前配置中未使用
```

**Docker 沙箱**:
```bash
# deeppresenter/mcp.json.example
docker run --init --name $WORKSPACE_ID -i --rm \
  -v $HOST_WORKSPACE:$WORKSPACE \
  -w $WORKSPACE \
  deeppresenter-sandbox
```

### 4.3 LLM 调用拓扑

```python
# deeppresenter/utils/config.py:LLM.run()
多端点轮询机制:
- endpoints: [Endpoint1, Endpoint2, ...]
- 轮询策略: cycle(endpoints)
- 重试次数: RETRY_TIMES (默认 3)
- 并发控制: Semaphore (max_concurrent)
```

---

## 5. 可配置项作用机制

### 5.1 配置文件结构

**config.yaml**:
```yaml
# 全局模式
offline_mode: false        # 禁用所有网络工具
context_folding: true      # 启用上下文折叠
multiagent_mode: false     # 启用子代理
heavy_reflect: false       # 启用深度反思（使用渲染图像）

# LLM 配置
research_agent:
  base_url: "https://openrouter.ai/api/v1"
  model: "anthropic/claude-sonnet-4.5"
  api_key: "xxx"
  endpoints: [...]         # 备用端点
  max_concurrent: 10       # 并发限制
  sampling_parameters:    # 采样参数
    temperature: 0.7
```

**mcp.json**:
```json
[
  {
    "name": "search",
    "command": "python3",
    "args": ["$PACKAGE_DIR/tools/search.py"],
    "env": {
      "TAVILY_API_KEY": "xxx",
      "SERPAPI_KEY": "xxx"
    },
    "network": true,         // 标记为网络工具
    "keep_tools": [...],     // 保留的工具
    "exclude_tools": [...]   // 排除的工具
  }
]
```

**roles/*.yaml**:
```yaml
system:
  zh: "系统提示词（中文）"
  en: "System prompt (English)"

instruction: |
  用户指令模板，支持 Jinja2:
  prompt: {{prompt}}
  attachments: {{attachments}}

toolset:
  include_tool_servers: ["search", "research", "any2markdown"]
  exclude_tool_servers: []
  include_tools: []
  exclude_tools: []

use_model: "research_agent"  # 使用的 LLM 配置
```

### 5.2 配置加载流程

```python
# CLI 模式
uvx pptagent onboard
  ├─► 交互式配置
  ├─► 保存到 ~/.config/deeppresenter/config.yaml
  └─► 保存到 ~/.config/deeppresenter/mcp.json

# 运行时加载
DeepPresenterConfig.load_from_file()
  ├─► 读取 config.yaml
  ├─► 初始化 LLM 对象
  │   ├─► 创建 Endpoint 实例
  │   ├─► 初始化 AsyncOpenAI 客户端
  │   └─► 检测多模态能力
  └─► 计算上下文窗口
      ├─► context_folding=True: CONTEXT_LENGTH_LIMIT / max_context_folds
      └─► context_folding=False: CONTEXT_LENGTH_LIMIT
```

### 5.3 环境变量优先级

```
环境变量 > mcp.json env > 默认值

关键环境变量:
- TAVILY_API_KEY - Tavily 搜索 API
- SERPAPI_KEY - Google 搜索 API
- MINERU_API_KEY - MinerU PDF 解析 API
- MINERU_API_URL - MinerU 本地服务 URL
- OFFLINE_MODE - 离线模式标志
- DEEPPRESENTER_WORKSPACE_BASE - 工作空间基础路径
- DEEPPRESENTER_HOST_WORKSPACE_BASE - Docker 挂载路径映射
```

---

## 6. 外部依赖详解

### 6.1 图片检索

**方式 1: 网页图片搜索**
```python
# deeppresenter/tools/search.py
工具: search_images(query)

后端（二选一）:
1. SerpAPI (Google Images)
   - 需要 SERPAPI_KEY
   - 返回: url, thumbnail, description
   
2. Tavily
   - 需要 TAVILY_API_KEY
   - 返回: url, description

流程:
Agent → LLM 决定搜索图片 → 调用 search_images → 
返回图片 URL → download_file 下载 → 保存到工作空间
```

**方式 2: 文本生成图像**
```python
# deeppresenter/utils/config.py:LLM.generate_image()
工具: generate_image(prompt, width, height)

配置:
t2i_model:
  base_url: "https://ark.cn-beijing.volces.com/api/v3"
  model: "doubao-seedream-4.5-251128"
  api_key: "xxx"
  min_image_size: 1024  # 最小图片尺寸

流程:
Agent → LLM 决定生成图片 → 调用 t2i_model.generate_image() →
OpenAI Images API → 返回 base64 图片 → 保存到工作空间

多端点支持:
- 支持多个 t2i 端点轮询
- 失败自动重试
- 尺寸自动调整（满足 min_image_size）
```

### 6.2 PDF 解析

**方式 1: MinerU API（在线）**
```python
# deeppresenter/tools/any2markdown.py
环境变量: MINERU_API_KEY

流程:
convert_to_markdown(pdf_path)
  ├─► 调用 MinerU API
  ├─► 上传 PDF
  ├─► 下载解析结果（markdown + images）
  └─► 保存到输出文件夹
```

**方式 2: MinerU 本地服务（离线）**
```python
# deeppresenter/utils/mineru_api.py:parse_pdf_offline()
环境变量: MINERU_API_URL

流程:
convert_to_markdown(pdf_path)
  ├─► POST 到本地 MinerU 服务
  ├─► 获取解析结果
  └─► 保存到输出文件夹

适合:
- 离线模式
- 隐私敏感场景
```

**方式 3: MarkItDown（降级）**
```python
# deeppresenter/tools/any2markdown.py
依赖: markitdown[all]

流程:
convert_to_markdown(docx, pptx, etc.)
  ├─► MarkItDown.convert_local()
  ├─► 提取 base64 图片
  ├─► 保存图片到本地
  └─► 替换 data URI 为本地路径
```

### 6.3 学术搜索

**arXiv 搜索**
```python
# deeppresenter/tools/research.py:search_papers()
依赖: arxiv Python 库

功能:
- 搜索学术论文
- 支持字段查询（ti:, au:, abs:, cat:）
- 返回: title, authors, abstract, published, pdf_url
```

**Semantic Scholar**
```python
# deeppresenter/tools/research.py
依赖: semanticscholar Python 库

功能:
- get_paper_authors - 获取论文作者信息
- get_scholar_details - 获取学者详情
  - hIndex, homepage, paperCount
  - 论文列表（支持按引用数/年份排序）
```

### 6.4 网页抓取

**方式 1: Playwright（首选）**
```python
# deeppresenter/tools/search.py:fetch_url()
依赖: playwright, chromium

流程:
fetch_url(url)
  ├─► Playwright 转到 URL
  ├─► 等待 DOM 加载
  ├─► 获取 HTML
  ├─► markdownify 转换为 markdown
  └─► trafilatura 提取正文（body_only=True）
```

**方式 2: httpx（降级）**
```python
# 用于下载文件
download_file(url, output_file)
  ├─► httpx 流式下载
  ├─► PIL 处理图片
  ├─► WEBP 自动转 PNG
  └─► 保存到工作空间
```

### 6.5 HTML 转 PPTX

**方式 1: Node.js html2pptx（首选）**
```javascript
// deeppresenter/html2pptx/html2pptx.js
依赖:
- pptxgenjs
- playwright
- sharp

流程:
1. 读取 HTML 文件
2. Playwright 渲染为图片
3. 使用 pptxgenjs 创建 PPTX
4. 插入图片到幻灯片
5. 保存 .pptx 文件
```

**方式 2: Playwright PDF 转换（降级）**
```python
# deeppresenter/utils/webview.py:PlaywrightConverter.convert_to_pdf()
流程:
1. 加载所有 HTML 文件
2. Playwright 打印为 PDF
3. 保存 .pdf 文件
```

---

## 7. 关键数据流

### 7.1 消息格式

```python
# deeppresenter/utils/typings.py:ChatMessage
class ChatMessage(BaseModel):
    id: str
    role: Role  # SYSTEM, USER, ASSISTANT, TOOL
    content: str | list[dict]  # 文本或多模态内容
    reasoning: str | None  # 推理过程（如果启用）
    tool_calls: list[ToolCall] | None  # 工具调用
    from_tool: ToolCall | None  # 来自哪个工具
    tool_call_id: str | None  # 工具调用 ID
    is_error: bool = False  # 是否错误
    cost: Cost | None  # Token 消耗
```

### 7.2 工具调用格式

```python
# OpenAI 格式
{
    "type": "function",
    "function": {
        "name": "search_web",
        "arguments": '{"query": "AI", "max_results": 3}'
    }
}

# MCP 格式
CallToolResult(
    type="text",
    content=[TextContent(text="...", type="text")],
    isError=False
)
```

### 7.3 工作空间结构

```
~/.cache/deeppresenter/{session_id}/
├── .history/
│   ├── deeppresenter-loop.log
│   ├── Research-history.jsonl
│   ├── Research-config.json
│   ├── Design-history.jsonl
│   ├── tool_history.jsonl
│   └── tools_time_cost.json
├── .input_request.json
├── intermediate_output.json
├── outline.json (Planner 阶段)
├── manuscript.md (Research 阶段)
├── slides/ (Design 阶段)
│   ├── slide_001.html
│   ├── slide_002.html
│   └── ...
├── images/ (图片资源)
└── final.pptx (最终输出)
```

---

## 8. 性能优化机制

### 8.1 并发控制

```python
# LLM 调用并发
LLM.max_concurrent: int
- Semaphore 控制并发数
- 避免速率限制

# 工具执行并发
asyncio.gather(*tool_coros)
- 所有工具调用并发执行
- 减少 LLM 等待时间
```

### 8.2 缓存机制

```python
# 工具结果缓存
- 超长内容保存到文件
- 避免上下文溢出
- 文件路径返回给 LLM

# 模型缓存
~/.cache/huggingface/ - Hugging Face 模型
~/.cache/modelscope/ - ModelScope 模型
```

### 8.3 重试机制

```python
# LLM 调用重试
RETRY_TIMES = 3
- 端点轮询
- 自动切换备用端点

# 工具调用重试
- 超时自动重试
- 错误自动重试
```

---

## 9. 错误处理

### 9.1 工具执行错误

```python
# deeppresenter/agents/env.py:tool_execute()
错误类型:
1. Tool not found - 工具不存在
2. Timeout - 执行超时（MCP_CALL_TIMEOUT）
3. Validation error - 参数验证失败
4. Execution error - 工具执行失败

处理:
- 包装为 CallToolResult(isError=True)
- 记录到 error_history
- 返回错误消息给 LLM
```

### 9.2 上下文溢出

```python
# deeppresenter/agents/agent.py
阈值:
- 50% context_window - 警告
- 80% context_window - 紧急警告
- 100% context_window - 折叠或报错

处理:
1. context_folding=True - 调用 compact_history()
2. context_folding=False - 抛出 RuntimeError
```

### 9.3 LLM 调用失败

```python
# deeppresenter/utils/config.py:LLM.run()
处理:
- 收集所有端点错误
- 轮询所有端点
- 全部失败后抛出 ValueError
```

---

## 10. 安全隔离

### 10.1 工作空间隔离

```python
# 每个会话独立工作空间
workspace = WORKSPACE_BASE / session_id

# 文件访问限制
assert output_path.is_relative_to(workspace)
- 禁止访问工作空间外文件
```

### 10.2 Docker 沙箱

```python
# deeppresenter/mcp.json.example
sandbox 工具:
- 独立容器
- 挂载工作空间
- 执行任意命令
- 容器自动清理（--rm）
```

### 10.3 环境变量隔离

```python
# MCP 工具独立环境
envs = {
    "WORKSPACE": str(workspace),
    "HOST_WORKSPACE": host_workspace,
    "CONFIG_FILE": str(config.file_path),
    ...
}
- 避免全局环境污染
```

---

## 11. 总结

### 11.1 架构特点

1. **模块化设计**: Agent + MCP 工具的清晰分离
2. **可扩展性**: MCP 协议支持灵活添加工具
3. **容错性**: 多端点轮询、重试机制、降级策略
4. **上下文管理**: 自动折叠、警告机制
5. **多模态支持**: 文本、图像、文档处理

### 11.2 关键依赖

**必需**:
- Python 3.11+
- Docker（沙箱）
- LLM API（至少 3 个端点）
- Node.js（html2pptx）

**可选**:
- Tavily/SerpAPI（网页搜索）
- MinerU（PDF 解析）
- T2I Model（图片生成）

### 11.3 配置要点

1. **LLM 配置**: 至少配置 research_agent, design_agent, long_context_model
2. **MCP 配置**: 根据需求启用工具服务器
3. **模式选择**: offline_mode, context_folding, multiagent_mode
4. **Agent 配置**: roles/*.yaml 定义行为和工具集

### 11.4 调试建议

1. 查看日志: `workspace/.history/*.log`
2. 工具耗时: `workspace/.history/tools_time_cost.json`
3. 中间输出: `workspace/intermediate_output.json`
4. Token 统计: webui 界面显示
