# PPTAgent 国产化适配与稳定性加固 —— 完整操作手册

## 一、当前镜像状态汇总

| 组件 | 状态 | 版本/说明 |
|------|------|----------|
| deeppresenter-host | ✅ 已构建 | 9.53GB，LibreOffice 7.4.7.2 已安装 |
| deeppresenter-sandbox | ✅ 已构建 | 8.26GB，LibreOffice 7.4.7.2 已安装 |
| Tavily 搜索 | ✅ 已修复 | API Key 通过 `.env` → docker-compose 注入 |
| apt 源 | ✅ 阿里云镜像 | mirrors.aliyun.com |
| npm 源 | ✅ 淘宝镜像 | registry.npmmirror.com |
| PyPI 源 | ✅ 清华镜像 | mirrors.tuna.tsinghua.edu.cn |
| Playwright (npm) | ✅ npmmirror 加速 | PLAYWRIGHT_DOWNLOAD_HOST 已配置 |
| Playwright (Python) | ✅ 官方 CDN | 取消镜像避免 404 |
| Agent 轮次 | ✅ 已调大 | MAX_AGENT_TURNS=80, MAX_SUBAGENT_TURNS=15 |

> **注意**：旧日志中 `unoconvert/soffice 未安装` 的报错来自**旧预构建镜像**（forceless/deeppresenter-sandbox）。新构建的镜像已包含 LibreOffice，重新运行后即可生效。

---

## 二、完整启动流程（按顺序执行）

### 步骤 1：启动本地 LLM 服务

```powershell
llama-server -m "D:\models\Qwen3.6-35B-A3B-GGUF\Qwen3.6-35B-A3B-UD-Q3_K_XL.gguf" `
    --mmproj "D:\models\Qwen3.6-35B-A3B-GGUF\mmproj-F16.gguf" `
    --port 8989 -np 1 -c 131072 --cache-ram 0
```

> **参数说明**：`-np 1` 单槽位省 VRAM，`-c 131072` 131K 上下文足够 PPTAgent 使用，`--cache-ram 0` 不占用系统内存做 prompt cache。

### 步骤 2：启动 DeepPresenter 服务

```powershell
cd D:\0ly\PPTAgent
docker compose up -d
```

### 步骤 3：访问 WebUI

浏览器打开 http://localhost:7861

### 步骤 4：查看实时日志

```powershell
docker compose logs -f
```

---

## 三、镜像构建命令（仅在修改 Dockerfile 后需要）

### 首次构建或修改 Host Dockerfile 后

```powershell
cd D:\0ly\PPTAgent
docker compose build
```

### 修改 SandBox Dockerfile 后

```powershell
cd D:\0ly\PPTAgent
docker build -f deeppresenter/docker/SandBox.Dockerfile -t deeppresenter-sandbox .
```

### 强制全量重建（清空缓存）

```powershell
docker compose build --no-cache
docker build --no-cache -f deeppresenter/docker/SandBox.Dockerfile -t deeppresenter-sandbox .
```

> **提示**：日常小改动不需要 `--no-cache`，Docker 会自动复用未变更的层。

---

## 四、配置检查清单

### 4.1 Tavily API Key（必须）

1. 在 `.env` 文件中确认：
   ```
   TAVILY_API_KEY=tvly-你的真实Key
   ```
2. 确认 `docker-compose.yml` 第 27 行没有末尾引号：
   ```yaml
   TAVILY_API_KEY: ${TAVILY_API_KEY:-}   # ✅ 正确
   # TAVILY_API_KEY: ${TAVILY_API_KEY:-}"  # ❌ 错误，有引号
   ```
3. 确认 `deeppresenter/mcp.json` 中使用变量引用：
   ```json
   "TAVILY_API_KEY": "$TAVILY_API_KEY"
   ```

### 4.2 LLM 配置（config.yaml）

确认 `deeppresenter/config.yaml` 中所有 `base_url` 为：
```yaml
base_url: "http://host.docker.internal:8989/v1"
```

---

## 五、常见问题排查

| 现象 | 原因 | 解决 |
|------|------|------|
| Tavily 401 Unauthorized | Key 末尾有引号或 Key 无效 | 检查 docker-compose.yml 引号，或用 curl 测试 Key |
| apt 安装失败 / 连不上源 | 代理干扰 | `.env` 中 `http_proxy=` 和 `https_proxy=` 置空 |
| Playwright 下载 404 | npmmirror 未同步最新版 | Python Playwright 已回退官方 CDN，npm Playwright 走镜像 |
| 构建时内存爆满 | WSL2 默认吃掉大量内存 | 创建 `%USERPROFILE%\.wslconfig` 限制 `memory=12GB` |
| 容器内无法连宿主机 LLM | base_url 配置错误 | 容器内必须用 `host.docker.internal:8989` |
| LibreOffice 提示未安装 | 使用的是旧预构建镜像 | 确认 `docker images` 中 deeppresenter-sandbox 是最新构建的 |

---

## 六、进一步增强建议

### 6.1 模型连接稳定性

日志中出现 `Cannot send a request, as the client has been closed` 说明 LLM 连接偶发中断。可在 `deeppresenter/config.yaml` 中增加重试（如果底层支持），或确保 LLM 服务稳定运行。

### 6.2 Hugging Face 连接受阻

Host Dockerfile 中 `modelscope download --model forceless/fasttext-language-id` 使用 modelscope（国内源），通常不受影响。如仍失败，可手动下载后映射进容器。

### 6.3 中文字体补全

当前已安装 `fonts-wqy-zenhei`、`fonts-noto-cjk` 等中文字体。如 Agent 日志中仍提到 `Microsoft YaHei` / `PingFang SC` 缺失，不影响功能（会自动回退到已安装字体），仅视觉效果略降。

### 6.4 多 Agent 并行模式

当前 `multiagent_mode` 为 `false`。如需加速生成，可尝试开启，但本地单卡 GPU 并发可能反而降低速度，建议保持现状。