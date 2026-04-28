# Vision / Visual Feedback Analysis

## 1. 现状：为什么"视觉反馈"似乎从未生效

### 1.1 两个完全不同的视觉相关机制

代码中存在**两个**独立的视觉相关概念，容易混淆：

| 机制 | 配置项 | 实际用途 | 当前状态 |
|------|--------|----------|----------|
| **Vision Model** | `vision_model` | 给 manuscript 中的图片做 `image_caption`（生成描述+分类） | 配置了但未在核心流程中使用 |
| **Reflective Design** | `heavy_reflect` + `design_agent.is_multimodal` | 将渲染后的幻灯片截图返回给 LLM，让其基于视觉反馈修正 | **默认关闭**，且 prompt 未引导 LLM 利用截图 |

### 1.2 Vision Model (`vision_model`) 的真实角色

- **所在位置**：`deeppresenter/local_tools.py` 第 472-518 行，`deeppresenter/tools/tool_agents.py` 第 72-109 行
- **功能**：提供 `image_caption` 工具，输入一张图片路径，输出 `{size, caption}`，caption 格式为 `<type>:<description>`（如 `Chart: Bar graph showing...`）
- **何时被调用**：只有当 Agent 的 toolset 中显式包含 `image_caption` 时才会被调用
- **检查 Design Agent 的 toolset** (`Design.yaml` 第 99-107 行)：
  ```yaml
  include_tools:
    - delegate_subagent
    - inspect_slide
    - thinking
    - finalize
  ```
  **没有 `image_caption`**。因此 `vision_model` 在 Design Agent 的幻灯片生成流程中**完全不会被触发**。

### 1.3 Reflective Design (`heavy_reflect`) 的真实状态

- **开关条件** (`reflect.py` 第 23 行，`local_tools.py` 第 78 行)：
  ```python
  reflective_design = config.design_agent.is_multimodal and config.heavy_reflect
  ```
- **`heavy_reflect` 默认值**：`False` (`config.py` 第 334-337 行)
- **当前 `config.yaml`**：没有设置 `heavy_reflect`，因此为 `False`
- **`is_multimodal` 检测**：当前模型 `Qwen3.6-35B-A3B` 名称包含 `qwen`，`config.py` 第 208 行会自动检测为 `True`
- **结论**：即使模型被识别为多模态，因为 `heavy_reflect=false`，反射式设计仍被关闭

### 1.4 即使开启 `heavy_reflect`，Design Agent 也"不会看图"

`inspect_slide` 在反射模式下会返回 `ImageContent`（幻灯片截图的 base64 JPEG）。但查看 `Design.yaml` 的 system prompt：

- 全篇 prompt 没有任何地方告诉模型"你会收到一张渲染后的幻灯片图片"
- 没有任何地方 instruct 模型"检查图片中是否有元素重叠、文字截断、颜色对比度不足等视觉问题"
- `inspect_slide` 的返回描述仅说明：
  ```
  ImageContent: The slide as an image content (reflective mode only)
  str: Validation result message
  ```
- Design Agent 收到图片后，**没有被教导要分析图片内容**，因此大概率会忽略图片，仅根据文本验证结果继续

### 1.5 为什么布局 bug（重叠、截断等）没被发现

1. **`inspect_slide` 的验证逻辑非常有限**：
   - 只检查 HTML 是否能通过 `html2pptx` 转换为 PPTX（结构性验证）
   - 检查 manuscript 中的图片是否都在 HTML 中被引用（图片覆盖检查）
   - **不涉及任何视觉/像素级验证**（如元素重叠、文字截断、溢出等）

2. **没有视觉反馈回路**：
   - `heavy_reflect` 关闭时，`inspect_slide` 只返回文本 "Validation PASSED..."
   - LLM 根本看不到渲染后的幻灯片长什么样

3. **LLM 基于文本推理的局限**：
   - Design Agent 仅凭自己生成的 HTML 代码进行推理
   - 代码层面的 "看起来正确" 不等于渲染后的视觉效果正确
   - CSS 的复杂交互（flex 布局、绝对定位、字体渲染差异）很难仅凭代码预判

---

## 2. 如何启用视觉反馈

### 2.1 启用 Reflective Design（反射式设计）

在 `config.yaml` 中增加一行：

```yaml
heavy_reflect: true
```

或者在 `config.yaml.example` 中已有注释：
```yaml
# heavy_reflect: true  # Enable reflective design: render each slide to image and send back to the LLM for visual review
```

### 2.2 确保 design_agent 是多模态模型

当前配置使用的是 `Qwen3.6-35B-A3B`，名称包含 `qwen`，会被自动检测为 `True`。但如果换用其他模型，需要显式设置：

```yaml
design_agent:
  base_url: "..."
  model: "your-model"
  api_key: "..."
  is_multimodal: true  # 强制声明为多模态
```

### 2.3 （可选）配置独立的 Vision Model

`vision_model` 目前只用于 `image_caption`，与 `heavy_reflect` 无关。但如果未来想让 `image_caption` 生效（例如 Research Agent 需要描述图片），可以保留当前配置：

```yaml
vision_model:
  base_url: "http://host.docker.internal:8989/v1"
  model: "Qwen3.6-35B-A3B-UD-Q4_K_S.gguf"
  api_key: "not-needed"
```

---

## 3. 启用后的运作流程

### 3.1 单页幻灯片的验证-反馈闭环

```
Design Agent
    │
    ▼
调用 inspect_slide(html_file, aspect_ratio, manuscript_file)
    │
    ▼
inspect_slide 内部：
  1. 检查图片覆盖 (_check_image_coverage)
  2. 调用 convert_html_to_pptx 做结构性验证
  3. 如果 reflective_design=True:
     a. Playwright 打开 HTML → 生成 PDF (1280x720)
     b. pdf2image 将 PDF 转为 JPEG (dpi=100, 文件夹: .slide_images-pdf-{stem}/)
     c. 读取 slide_01.jpg → base64 编码
     d. 返回 ImageContent(type="image", data="data:image/jpeg;base64,...")
  4. 如果 reflective_design=False:
     返回文本 "Validation PASSED for slide_XX.html..."
    │
    ▼
Design Agent 收到观察结果（Observation）
  - 如果是 ImageContent，observation 的 has_image=True
  - agent.py 第 315-329 行会转换图片格式：
    * qwen/gemini 模型：保持 OpenAI image_url 格式，role 设为 USER
    * claude 模型：转换为 Anthropic 的 base64 image 格式
    │
    ▼
Design Agent 的下一次 LLM 调用中，这张图片作为上下文的一部分传入
    │
    ▼
LLM 理论上应该：
  - 分析图片中的视觉问题
  - 生成修正后的 HTML
  - 再次调用 inspect_slide
```

### 3.2 图片是如何生成的

核心代码在 `deeppresenter/utils/webview.py`：

1. **PlaywrightConverter.convert_to_pdf()** (第 90-137 行)
   - 启动 headless Chromium 浏览器
   - 用 `page.goto(file://...html)` 加载本地 HTML 文件
   - 用 `page.pdf()` 生成 PDF，页面尺寸根据 `aspect_ratio`：
     - 16:9 → 1280px x 720px
     - 4:3 → 960px x 720px
   - 多个 HTML 文件会合并为一个 PDF

2. **pdf2image 转换** (第 134-135 行)
   - `convert_from_path(output_pdf, dpi=100)`
   - 每页保存为 `slide_{XX}.jpg`
   - 输出文件夹：`{pdf_parent}/.slide_images-pdf-{pdf_stem}/`

3. **base64 编码** (`reflect.py` / `local_tools.py`)
   - 读取 JPEG 二进制 → base64 → `data:image/jpeg;base64,{data}`

### 3.3 图片在 LLM 上下文中的流转

在 `agent.py` 的 `execute()` 方法中（第 314-329 行）：

```python
for obs in observations:
    if obs.has_image:
        if "gemini" in self.model.lower() or "qwen" in self.model.lower():
            obs.role = Role.USER  # 将图片观察的角色设为 USER
        if "claude" in self.model.lower():
            # 转换为 Claude 格式
            oai_b64 = obs.content[0]["image_url"]["url"]
            obs.content = [{
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": oai_b64.split(",")[1]
                }
            }]
```

---

## 4. 代价分析

### 4.1 时间代价

| 操作 | 估算耗时 | 备注 |
|------|----------|------|
| Playwright 启动/复用 | ~200ms（首次）/ 0ms（复用） | `PlaywrightConverter` 有类级缓存 `_browser` |
| 打开 HTML + 渲染 | ~500ms-1s | 取决于 CSS 复杂度 |
| PDF 生成 | ~200-500ms | 单页 |
| PDF → JPEG (pdf2image) | ~300-500ms | dpi=100，单页 |
| **每页额外总耗时** | **~1-2s** | 20 页幻灯片 ≈ 20-40s 额外时间 |

### 4.2 Token / 成本代价

- 单张 1280x720 JPEG (dpi=100, 质量中等) 的 base64 大小：**约 50-150KB**
- 对应 token 数：**约 15K-40K tokens**（取决于图片复杂度和压缩率）
- 假设 20 页幻灯片，每页都发回截图：
  - 额外输入 token：**300K-800K tokens**
  - 如果上下文压缩 (`_compact_completed_slides`) 生效，已完成的幻灯片历史会被压缩，但图片本身仍在最近的观察中
- **上下文膨胀**：图片观察进入 `chat_history` 后，后续每轮对话都会携带这些图片的 base64 数据（除非被压缩折叠），这会快速耗尽上下文窗口

### 4.3 质量代价（风险）

- **模型可能没有足够视觉理解能力**：当前配置的 `Qwen3.6-35B-A3B` 是文本为主的模型（35B 激活参数，A3B 总参数），虽然名字被检测为多模态，但实际视觉能力可能有限
- **Prompt 未引导视觉分析**：即使收到图片，模型没有被 instruct 去"找布局 bug"，可能会忽略图片内容
- **幻觉风险**：模型可能"看到"不存在的问题，或忽略真正的问题，导致无限循环修正

---

## 5. 如果要真正利用视觉反馈修复布局 bug

### 5.1 需要修改的地方

1. **启用开关**
   - `config.yaml` 中设置 `heavy_reflect: true`

2. **修改 Design.yaml 的 system prompt**
   - 在 `<工作流程>` 中增加第 6 步：
     ```
     6. **视觉审查**：inspect_slide 验证通过后，你会收到该幻灯片的渲染截图。
        - 仔细检查截图中的视觉问题：元素重叠、文字截断/溢出、图片变形、颜色对比度不足、对齐偏差。
        - 如发现问题，主动修复 HTML 并重新调用 inspect_slide。
        - 如截图看起来正确，继续生成下一页。
     ```
   - 在 `<自检要点>` 中增加视觉检查项

3. **（可选）使用专门的多模态模型作为 design_agent**
   - 当前 `Qwen3.6-35B-A3B` 视觉能力未知
   - 建议换用明确支持 vision 的模型，如 Qwen2.5-VL、GPT-4o、Claude 3.5 Sonnet 等

4. **（可选）独立的 vision_model 用于幻灯片审查**
   - 当前 `heavy_reflect` 是把图片塞回 `design_agent`（同一个 LLM）
   - 可以考虑让 `vision_model` 专门做视觉审查，输出 bug 列表，再由 `design_agent` 修复
   - 但这需要修改 `inspect_slide` 的逻辑，让它调用 `vision_model` 而非直接返回图片

### 5.2 最小可行改动

如果只改动一处来启用视觉反馈：

**修改 `Design.yaml`**，在 system prompt 的 `<工作流程>` 第 5 步后追加：

```yaml
    6. **视觉审查（ reflect mode 下生效）**：
       - 当 inspect_slide 返回图片而非纯文本时，代表该页已渲染成功。
       - 你必须分析这张截图，检查是否存在以下问题：
         * 文字被截断或溢出容器
         * 元素重叠或间距不均
         * 图片比例失调或模糊
         * 配色导致文字难以阅读
         * 布局与设计 plan 不符
       - 如发现问题，描述具体问题后修复 HTML，再次调用 inspect_slide。
       - 如截图无误，继续下一页。
```

同时启用 `heavy_reflect: true`。

---

## 6. 关键文件速查

| 文件 | 相关行 | 作用 |
|------|--------|------|
| `deeppresenter/config.yaml` | 第 1-35 行 | 当前配置，`heavy_reflect` 未设置（默认 false） |
| `deeppresenter/utils/config.py` | 第 334-337 行 | `heavy_reflect` 字段定义，默认 `False` |
| `deeppresenter/utils/config.py` | 第 206-214 行 | `is_multimodal` 自动检测逻辑 |
| `deeppresenter/tools/reflect.py` | 第 23 行 | `REFLECTIVE_DESIGN = is_multimodal and heavy_reflect` |
| `deeppresenter/tools/reflect.py` | 第 141-156 行 | `inspect_slide` 中生成图片并返回 `ImageContent` |
| `deeppresenter/local_tools.py` | 第 78, 338-353 行 | 本地工具版本的 reflective design 逻辑 |
| `deeppresenter/utils/webview.py` | 第 90-137 行 | `PlaywrightConverter.convert_to_pdf()` 渲染流程 |
| `deeppresenter/agents/agent.py` | 第 314-329 行 | 处理含图片的 observation，格式转换 |
| `deeppresenter/roles/Design.yaml` | 全文 | Design Agent 的 system prompt，**缺少视觉审查指导** |
