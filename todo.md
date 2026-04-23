我发现tavily默认的调用方式 虽然是返回了结果，但是默认的参数没直接的素材，
也没针对中国搜索优化，结果的网站很多是没法从中国大陆访问的。
我刚刚在tavily官网api测试了下，发现如下的参数可以返回中国的结果，且有可能直接包含素材。
你看看能否帮我修改代码，让它使用我的api 配置：

下面是python和js示例
# To install: pip install tavily-python
from tavily import TavilyClient
client = TavilyClient("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")
response = client.search(
    query="常见自然灾害",
    search_depth="basic",
    max_results=3,
    include_images=True,
    include_image_descriptions=True,
    include_favicon=True,
    include_usage=True,
    country="china"
)
print(response)


// To install: npm i @tavily/core
const { tavily } = require('@tavily/core');
const client = tavily({ apiKey: "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" });
client.search("常见自然灾害", {
    searchDepth: "basic",
    maxResults: 3,
    includeImages: true,
    includeImageDescriptions: true,
    includeFavicon: true,
    includeUsage: true,
    country: "china"
})
.then(console.log);

-----


## 本地 LLM 启动命令（Qwen3.6-27B-Q3_K_XL @ RTX 3090 24GB）

```powershell
C:\local\llama-b8036-bin-win-cuda-13.1-x64\llama-server.exe `
    -m "D:\models\Qwen3.6-27B-GGUF\Qwen3.6-27B-UD-Q3_K_XL.gguf" `
    --mmproj "D:\models\Qwen3.6-27B-GGUF\mmproj-F16.gguf" `
    --port 8989 `
    -np 1 `
    -c 32768 `
    -b 4096 -ub 4096 `
    -ctk q4_0 -ctv q4_0 `
    --flash-attn on `
    --cache-ram 0
```

### 参数设计依据

| 参数 | 值 | 说明 |
|------|-----|------|
| `-m` | Q3_K_XL (14GB) | 比 Q4_K_XL (17GB) 省 3GB VRAM，留给 KV cache |
| `--mmproj` | mmproj-F16.gguf | 视觉编码器，多模态输入必需 |
| `-np 1` | 单并行槽 | PPTAgent 串行调用，多槽无意义且浪费 VRAM |
| `-c 32768` | 32K 上下文 | 从 16K 提到 32K，满足 PPTAgent research 阶段长输出需求 |
| `-b 4096` | 逻辑 batch | prompt 处理批大小，4096 平衡速度与显存 |
| `-ub 4096` | 物理 batch | 与 -b 对齐，避免分批调度开销 |
| `-ctk q4_0 -ctv q4_0` | KV cache Q4 量化 | **关键**：KV cache 从 FP16 压缩到 Q4，每 token 从 ~2MB 降到 ~0.5MB，32K 上下文仅占 ~16MB |
| `--flash-attn on` | Flash Attention | 必须显式 on，加速注意力计算并降低显存 |
| `--cache-ram 0` | 不用 RAM 做 prompt cache | 避免 RAM 抢占，全部走 VRAM |

### VRAM 预算（24GB RTX 3090）

| 组件 | 占用 |
|------|------|
| 模型权重 Q3_K_XL | ~14 GB |
| Vision Encoder (mmproj) | ~1.2 GB |
| CUDA / 框架开销 | ~0.5 GB |
| KV cache (32K × Q4) | ~0.5 GB |
| **合计** | **~16.2 GB** |
| **剩余** | **~7.8 GB 余量** |

> 之前 Q4_K_XL + FP16 KV + 16K context 就已经 OOM，根本原因：FP16 KV cache 吞掉了 ~32MB/token 的显存。
> 现在换 Q3 模型 + Q4 KV，省出 3GB 权重空间 + 大幅压缩 KV，32K context 也绑绑有余。
-----

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