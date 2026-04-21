根据你提供的日志和 PPT 结果，针对你的问题分析如下：

### 1. 内容生成方式
* **文案内容**：**完全由 LLM 自己构思（基于内置知识库）** 。日志显示 `search_web` 工具因 API Key 无效（401 Unauthorized）多次调用失败 ，尝试访问 National Geographic 等网站也因 404 错误未能获取有效信息 。
* **图片生成**：**并非调用外部生图模型**。模型通过编写 Python 代码（使用 `matplotlib` 库）绘制了相关的科普图表和图标（如地球内部结构、防灾图标等），然后保存为本地图片引用 。

### 2. 日志中值得注意的问题
* **工具调用受阻**：SerpAPI 密钥未配置或无效，导致无法获取即时网络信息 。
* **依赖缺失**：系统中未安装 `unoconvert/soffice`，这将导致 PPT 导出为预览图的功能失效 。
* **任务中断**：由于达到了 50 轮的对话限制，Agent 在只完成了 9 张幻灯片的情况下被迫执行了 `finalize`（原计划 16 张），导致火山、海啸等后续章节缺失。
* **字体兼容性**：生成图片时 `WenQuanYi Zen Hei` 字体缺少部分 Unicode 表情符号（如 🌍、🌋），导致图片中出现了警告 。
* **技术债务**：存在多处 `Authlib` 和 `Gradio` 的弃用警告，建议后续更新相关库版本以避免未来运行出错 。

### 3. 已实施的改进措施

#### 3.1 搜索 API 配置（工具调用受阻）
- 更新 `mcp.json` 和 `mcp.json.example`，默认 SERPAPI_KEY 置空，推荐使用 Tavily（国内访问更友好）
- Tavily 申请地址：https://tavily.com ，免费额度 1000 次/月
- 配置方式：在 `mcp.json` 的 search 工具 env 中填入 `"TAVILY_API_KEY": "tvly-xxxx"`
- 注意：Tavily API Key 必须以 `tvly` 开头才会被加载

#### 3.2 LibreOffice 安装（依赖缺失）
- 在 `Host.Dockerfile` 和 `SandBox.Dockerfile` 中均添加了 `libreoffice-impress` 和 `libreoffice-writer` 安装
- 安装后 Agent 可通过 `soffice --headless` 将 PPT 转为预览图
- **需要重新构建镜像**：`docker compose build`

#### 3.3 Agent 轮次限制调优（任务中断）
- 在 `docker-compose.yml` 中添加了环境变量：
  - `MAX_AGENT_TURNS: 80`（原默认值 50）
  - `MAX_SUBAGENT_TURNS: 15`（原默认值 10）
- 如需生成 16+ 页 PPT，可进一步调大到 100
- 可在 `docker-compose.yml` 中直接修改，无需重建镜像

#### 3.4 字体与 matplotlib 兼容性（字体兼容性）
- SandBox.Dockerfile 的 matplotlibrc 增加 `Noto Color Emoji` 字体回退
- 添加 `axes.unicode_minus: False` 避免负号显示问题
- Agent Prompt 增加明确指导：禁止在 matplotlib 图表中使用 emoji/特殊 Unicode 符号

#### 3.5 Authlib/Gradio 弃用警告（技术债务）
- 在 `webui.py` 和 `cli/__init__.py` 中添加 authlib/gradio 模块的 DeprecationWarning 过滤
- 这是 Gradio 内部依赖的 Authlib 版本问题，直接升级 Gradio 可能引入兼容性问题

#### 3.6 中国大陆镜像加速
- **apt 镜像**：SandBox.Dockerfile 添加阿里云镜像源（与 Host.Dockerfile 一致）
- **npm 镜像**：两个 Dockerfile 均配置淘宝 npm 镜像 `https://registry.npmmirror.com`
- **PyPI 镜像**：uv pip install 使用清华镜像 `https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple`
- **基础镜像**：统一使用 `docker.1ms.run` 拉取代理
- **Docker CE**：GPG 密钥直接从阿里云下载，移除硬编码代理
- **git clone**：SandBox 中 DesktopCommanderMCP 优先尝试 gitee 镜像

### 4. 需手动执行的操作

1. **配置搜索 API Key**：编辑 `deeppresenter/mcp.json`，在 search 工具的 env 中填入有效的 Tavily API Key
2. **重建 Docker 镜像**：
   ```bash
   docker compose build
   docker build -f deeppresenter/docker/SandBox.Dockerfile -t deeppresenter-sandbox .
   ```
3. **启动服务**：
   ```bash
   docker compose up -d
   ```
4. （可选）如需更长 PPT，修改 `docker-compose.yml` 中 `MAX_AGENT_TURNS` 值后重启即可