# PPTAgent 代码优化建议

基于日志分析和代码审查，以下为可在代码库层面实施的优化建议，按优先级排序。

---

## 🔴 高优先级（立即实施）

### 1. 提示词语言约束强化

**现状**：Research.yaml 和 Design.yaml 的系统提示词未强制要求使用简体中文，导致出现英文标题（ERUPTION, LANDSLIDE）和英文占位符（Lorem ipsum）。

**改进位置**：
- `deeppresenter/roles/Research.yaml` - 第3行和第49行
- `deeppresenter/roles/Design.yaml` - 第3行和第231行

**改进内容**：
在系统提示词开头添加：
```yaml
<语言约束>
- 严格使用简体中文输出所有内容，包括标题、说明文字和图片描述
- 禁止使用 Lorem ipsum 等拉丁文占位符
- 禁止使用英文标题，除非用户明确要求英文内容
</语言约束>
```

**预期收益**：消除英文混杂问题，提升中文内容一致性

---

### 2. 图片搜索关键词自动优化

**现状**：`deeppresenter/tools/search.py` 中的 `search_images` 工具直接使用原始查询词，未添加高质量图片筛选关键词。

**改进位置**：`deeppresenter/tools/search.py` 第179-200行（SerpAPI）和第245-271行（Tavily）

**改进内容**：
```python
async def search_images(query: str) -> dict:
    # 自动添加高质量图片筛选关键词
    enhanced_query = f"{query} high quality creative commons illustration for kids"
    debug(f"search_images via Tavily query={enhanced_query!r}")
    # ... 其余逻辑
```

**预期收益**：减少水印图片，提升图片质量和版权安全性

---

### 3. 图片下载分辨率验证

**现状**：`deeppresenter/tools/search.py` 的 `download_file` 函数（第346-443行）仅检查文件是否为图片，未验证分辨率。

**改进位置**：`deeppresenter/tools/search.py` 第402-423行

**改进内容**：
```python
try:
    with Image.open(BytesIO(data)) as img:
        img.load()
        width, height = img.size
        # 验证最小分辨率
        if width < 800 or height < 600:
            return f"Downloaded image resolution too low ({width}x{height}), minimum 800x600 required"
        # 验证宽高比（16:9 允许 ±30% 偏差）
        aspect_ratio = width / height
        target_ratio = 16 / 9
        if abs(aspect_ratio - target_ratio) / target_ratio > 0.3:
            return f"Image aspect ratio {aspect_ratio:.2f} deviates too much from 16:9 ({target_ratio:.2f})"
        # ... 其余保存逻辑
```

**预期收益**：提前过滤低质量图片，减少后期修复成本

---

## 🟡 中优先级（近期实施）

### 4. HTML 验证规则预检查提示

**现状**：Design.yaml 已有详细的 HTML 规则说明（第39-68行），但 Agent 仍可能生成不符合规则的代码，导致反复迭代。

**改进位置**：`deeppresenter/roles/Design.yaml` 第3行后添加

**改进内容**：
```yaml
<生成前自检清单>
在调用 inspect_slide 之前，请自检：
1. 确认所有文本都被包裹在 <p>、<span>、<h1>-<h6>、<li> 中
2. 确认背景、边框、阴影仅应用于 <div> 元素
3. 确认 emoji 使用 <span> 包裹
4. 确认装饰元素添加了 overflow: hidden
5. 计算最底部元素的 top + 渲染高度是否 ≤ 672px（16:9）
</生成前自检清单>
```

**预期收益**：减少验证失败次数，降低迭代开销

---

### 5. Agent 最大轮次调整

**现状**：`deeppresenter/utils/constants.py` 中 `MAX_AGENT_TURNS = 50`（第21行），对于简单任务可能过高。

**改进位置**：`deeppresenter/utils/constants.py` 第21行

**改进内容**：
```python
# 根据任务复杂度动态调整，或通过环境变量覆盖
MAX_AGENT_TURNS = int(os.getenv("MAX_AGENT_TURNS", 30))  # 从50降至30
```

**预期收益**：避免无效的长尾迭代，节省 Token 消耗

---

### 6. 上下文折叠优化

**现状**：`deeppresenter/agents/agent.py` 的 `compact_history` 方法（第376-441行）在上下文溢出时触发，但可能丢失重要信息。

**改进位置**：`deeppresenter/agents/agent.py` 第376行

**改进内容**：
```python
async def compact_history(self, keep_head: int = 15, keep_tail: int = 6):
    # 增加 keep_head 和 keep_tail，保留更多上下文
    # 同时在摘要中明确记录已生成的文件路径和关键决策
```

**预期收益**：减少信息丢失，提升内容连贯性

---

## 🟢 低优先级（长期考虑）

### 7. 多模态反射设计启用

**现状**：`deeppresenter/tools/reflect.py` 中 `REFLECTIVE_DESIGN` 依赖 `config.heavy_reflect` 和 `is_multimodal`（第23行），默认关闭。

**改进位置**：`deeppresenter/config.yaml.example` 第4行

**改进内容**：
```yaml
heavy_reflect: true  # 启用视觉反射设计，但需确保 design_agent.is_multimodal: true
```

**预期收益**：通过视觉反馈提升设计质量，但会增加 Token 消耗

---

### 8. 图片风格一致性检查

**现状**：search.py 下载图片后无风格一致性检查，可能导致卡通/写实混用。

**改进位置**：新增工具函数 `deeppresenter/tools/search.py`

**改进内容**：
```python
@mcp.tool()
def validate_image_style(image_paths: list[str]) -> dict:
    """
    检查一组图片的风格一致性（基于颜色分布、亮度等特征）
    返回风格差异评分和警告
    """
    # 使用 PIL 分析图片的 HSV 颜色分布
    # 计算组内图片的风格距离
    # 返回是否适合用于同一演示文稿
```

**预期收益**：提升视觉一致性，但需要额外的计算开销

---

## ⚪ 外部配置建议（非代码改动）

以下建议不在代码库范围内，但值得记录：

### 1. LLM 服务配置优化
- llama-server 参数调整：`-c 32768`（从 128k 降至 32k）
- 模型量化：从 Q3_K_XL 升级至 Q4_K_M
- KV cache 量化：启用 `-ctk q4_0 -ctv q4_0`

### 2. Docker 资源限制
- WSL2 内存限制：创建 `%USERPROFILE%\.wslconfig` 设置 `memory=12GB`

---

## 实施建议

**第一阶段（1-2天）**：
1. 实施改进 1-3（提示词约束、图片搜索优化、分辨率验证）
2. 测试验证效果

**第二阶段（3-5天）**：
3. 实施改进 4-6（HTML 自检、轮次调整、上下文优化）
4. 观察 Token 消耗和生成时间变化

**第三阶段（按需）**：
5. 根据实际效果决定是否实施改进 7-8

---

## 代码改动影响评估

| 改进项 | 影响文件数 | 改动行数 | 风险等级 |
|--------|-----------|---------|---------|
| 1. 提示词约束 | 2 | ~6 | 低 |
| 2. 图片搜索优化 | 1 | ~4 | 低 |
| 3. 分辨率验证 | 1 | ~10 | 低 |
| 4. HTML 自检 | 1 | ~8 | 低 |
| 5. 轮次调整 | 1 | ~1 | 低 |
| 6. 上下文优化 | 1 | ~5 | 中 |
| 7. 反射设计 | 1 | ~1 | 中 |
| 8. 风格检查 | 1 | ~50 | 中 |

**总计**：8 个改进点，涉及 5 个文件，预计改动行数 ~85 行，整体风险可控。
