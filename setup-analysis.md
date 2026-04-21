# PPTAgent 部署方式分析

本文档梳理 PPTAgent/DeepPresenter 项目的各种部署启动方式，分析每种方式的依赖、假设和 repo 依赖关系。

---

## 1. CLI 方式 (uvx)

### 命令示例
```bash
# 安装 uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# 首次交互式配置
uvx pptagent onboard

# 生成演示文稿
uvx pptagent generate "Single Page with Title: Hello World" -o hello.pptx
```

### 主要流程
1. `uvx` 从 PyPI 下载并运行 `pptagent` 包
2. `pptagent` 命令指向 `deeppresenter.cli:main` (pyproject.toml 第 107 行)
3. `onboard` 子命令交互式配置环境：
   - 检查 Docker 镜像、Playwright 浏览器、npm 依赖、poppler
   - 询问是否使用本地模型（Forceless/DeepPresenter-9B-GGUF）
   - 配置 LLM（Research Agent、Design Agent、Long Context Model、Vision Model）
   - 配置 MCP 工具（Tavily、SerpAPI、MinerU）
   - 保存配置到 `~/.config/deeppresenter/config.yaml` 和 `mcp.json`
4. `generate` 子命令执行生成任务

### 依赖
- **外部依赖**: uv 工具
- **系统依赖**: Docker、Node.js、poppler、Playwright（macOS 上自动安装，Linux 需手动准备）
- **模型依赖**: 
  - 本地模型：llama.cpp（可选）
  - 或云端 LLM API（OpenAI 等）

### 是否依赖 Repo 源码
**部分依赖**：
- `uvx pptagent` 从 PyPI 安装包，不直接需要 repo 源码
- 但 `onboard` 命令会尝试复用当前目录的 `deeppresenter/config.yaml` 和 `deeppresenter/mcp.json`（如果存在）
- 如果当前目录没有这些文件，会从包内的 `config.yaml.example` 和 `mcp.json.example` 加载模板

**结论**: 理论上可以不 clone repo 直接使用，但如果有本地配置文件会优先使用。

---

## 2. Build from Source 方式

### 命令示例
```bash
uv pip install -e .
playwright install-deps
playwright install chromium
npm install --prefix deeppresenter/html2pptx
modelscope download forceless/fasttext-language-id

docker pull forceless/deeppresenter-sandbox
docker tag forceless/deeppresenter-sandbox deeppresenter-sandbox

# 启动应用
python webui.py
```

### 主要流程
1. Clone repo 到本地
2. 使用 `uv pip install -e .` 以开发模式安装包
3. 手动安装 Playwright 依赖和浏览器
4. 安装 Node.js 依赖（HTML 转 PPTX）
5. 下载语言检测模型
6. 拉取 Docker 镜像（用于沙箱环境）
7. 运行 `webui.py` 启动 Gradio 界面

### 依赖
- **必须依赖**: repo 源码
- **Python 依赖**: uv、Python 3.11+
- **系统依赖**: Docker、Node.js、Playwright、poppler
- **模型依赖**: fasttext-language-id 模型
- **Docker 镜像**: forceless/deeppresenter-sandbox

### 是否依赖 Repo 源码
**完全依赖**：
- 必须先 clone repo
- `uv pip install -e .` 需要本地 pyproject.toml
- `npm install --prefix deeppresenter/html2pptx` 需要本地 package.json
- `python webui.py` 需要本地 webui.py

**结论**: 必须先 clone repo。

---

## 3. Docker Compose 方式

### 命令示例
```bash
# 拉取公共镜像（避免从源码构建）
docker pull forceless/deeppresenter-sandbox
docker tag forceless/deeppresenter-sandbox deeppresenter-sandbox

# 或从源码构建
docker build -t deeppresenter-sandbox -f deeppresenter/docker/SandBox.Dockerfile .

# 启动服务
docker compose up -d
```

### 主要流程
1. Clone repo 到本地
2. 准备 Docker 镜像（拉取或构建）
3. 运行 `docker compose up -d`
4. 服务暴露在 `http://localhost:7861`

### docker-compose.yml 分析
```yaml
services:
  deeppresenter-host:
    build:
      context: .  # 构建上下文是 repo 根目录
      dockerfile: deeppresenter/docker/Host.Dockerfile
    volumes:
      - ./workspace:/opt/workspace
      - ./deeppresenter:/usr/src/pptagent/deeppresenter  # 挂载本地源码
      - /var/run/docker.sock:/var/run/docker.sock  # Docker-in-Docker
```

### Host.Dockerfile 分析
```dockerfile
WORKDIR /usr/src/pptagent
COPY . .  # 复制整个 repo 到容器内
RUN uv pip install -e .  # 以开发模式安装
CMD ["bash", "-c", "umask 000 && python webui.py 0.0.0.0"]
```

### 依赖
- **必须依赖**: repo 源码
- **Docker 镜像**: 
  - Host 服务：从 Host.Dockerfile 构建（需要 repo 源码）
  - Sandbox 服务：需要 `deeppresenter-sandbox` 镜像
- **系统依赖**: Docker、Docker Compose

### 是否依赖 Repo 源码
**完全依赖**：
- `docker-compose.yml` 必须在 repo 根目录运行（`context: .`）
- `Host.Dockerfile` 使用 `COPY . .`，需要 repo 源码
- 挂载了 `./deeppresenter` 目录到容器
- 即使使用预构建的 `deeppresenter-sandbox` 镜像，Host 服务仍需从源码构建

**结论**: 必须先 clone repo，且 docker-compose 必须在 repo 根目录运行。

---

## 4. 旧版 Docker 方式 (pptagent/docker/Dockerfile)

### Dockerfile 分析
```dockerfile
ARG CACHE_DATE=UNKNOWN
RUN git clone https://github.com/icip-cas/PPTAgent  # 在容器内 clone repo
RUN uv pip install --system "pptagent[full]"
RUN npm install --prefix /PPTAgent/pptagent_ui

WORKDIR /PPTAgent
CMD ["/bin/bash", "docker/launch.sh"]
```

### 主要流程
1. Dockerfile 内部自动 clone repo
2. 安装 Python 包和 Node.js 依赖
3. 运行启动脚本

### 依赖
- **不依赖本地 repo**: Dockerfile 内部自动 clone
- **Docker 依赖**: NVIDIA CUDA 12.1（基础镜像）
- **外部依赖**: GitHub 访问权限

### 是否依赖 Repo 源码
**不依赖**：
- Dockerfile 内部执行 `git clone`
- 用户只需有 Docker 环境，无需手动 clone repo

**结论**: 这是唯一真正独立的 Docker 部署方式，但似乎不是当前推荐方式（README 未提及）。

---

## 5. 问题总结

### Docker Compose 方式的依赖问题
README 第 154-168 行描述的 Docker Compose 方式存在以下问题：

1. **文档误导**: 文档说"Pull the public images to avoid build from source"，但实际上：
   - Host 服务仍需从 `Host.Dockerfile` 构建（需要 repo 源码）
   - 只有 Sandbox 服务可以使用预构建镜像
   - 用户无法仅通过 `docker compose up` 启动，必须先 clone repo

2. **Volume 挂载**: 
   ```yaml
   - ./deeppresenter:/usr/src/pptagent/deeppresenter
   ```
   这意味着本地源码变更会实时反映到容器内，适合开发但不适合纯部署。

3. **Docker-in-Docker**: 挂载 `/var/run/docker.sock`，容器内可以操作宿主机 Docker，增加了安全风险。

### 建议改进
1. **明确说明**: Docker Compose 方式需要先 clone repo
2. **提供纯 Docker 镜像**: 参考 `pptagent/docker/Dockerfile` 的做法，构建包含所有代码的镜像
3. **分离开发和部署配置**: 开发模式挂载源码，部署模式使用内置代码

---

## 6. 快速参考表

| 部署方式 | 需要 Clone Repo | 需要手动配置 | Docker 依赖 | 适用场景 |
|---------|----------------|-------------|-------------|---------|
| CLI (uvx) | 否（可选） | 是（onboard） | 是（沙箱） | 个人使用、快速体验 |
| Build from Source | 是 | 是 | 是 | 开发、调试 |
| Docker Compose | 是 | 是 | 是 | 服务器部署（但需源码） |
| 旧版 Docker | 否 | 否 | 是 | 纯 Docker 部署（不推荐） |

---

## 7. 配置文件位置

### CLI 方式
- 用户配置: `~/.config/deeppresenter/config.yaml`
- MCP 配置: `~/.config/deeppresenter/mcp.json`
- 缓存目录: `~/.cache/deeppresenter/`

### Build from Source / Docker Compose
- 需要手动准备 `deeppresenter/config.yaml` 和 `deeppresenter/mcp.json`
- 或运行 `python -m deeppresenter.cli onboard` 生成

---

## 8. 关键发现

1. **README 中的 Docker Compose 部署方式不够独立**: 仍需 clone repo 并修改配置文件
2. **CLI 方式最独立**: 可以不 clone repo 直接使用，但需要交互式配置
3. **旧版 Dockerfile 最符合 Docker 部署理念**: 镜像内包含所有代码，无需外部依赖
4. **当前推荐方式偏向开发场景**: Docker Compose 挂载源码，适合开发但不适合纯部署
