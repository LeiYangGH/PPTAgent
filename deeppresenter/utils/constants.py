"""deeppresenter 全局常量定义"""

import logging
import os
from pathlib import Path

# ============ Path ============
PACKAGE_DIR = Path(__file__).parent.parent

# ============ Logging ===========
LOGGING_LEVEL = int(os.getenv("DEEPPRESENTER_LOG_LEVEL", logging.INFO))
MAX_LOGGING_LENGTH = int(os.getenv("DEEPPRESENTER_MAX_LOGGING_LENGTH", 1024))

# ============ Agent  ============
RETRY_TIMES = int(os.getenv("RETRY_TIMES", 10))
MAX_TOOLCALL_PER_TURN = int(os.getenv("MAX_TOOLCALL_PER_TURN", 7))
MAX_RETRY_INTERVAL = int(os.getenv("MAX_RETRY_INTERVAL", 60))
# count in chars, this is about the first 4 page of a dual-column paper
TOOL_CUTOFF_LEN = int(os.getenv("TOOL_CUTOFF_LEN", 4096))
MAX_SUBAGENT_TURNS = int(os.getenv("MAX_SUBAGENT_TURNS", 10))
MAX_AGENT_TURNS = int(os.getenv("MAX_AGENT_TURNS", 50))
# count in tokens
CONTEXT_LENGTH_LIMIT = int(os.getenv("CONTEXT_LENGTH_LIMIT", 200_000))
CUTOFF_WARNING = "NOTE: Output truncated (showing first {line} lines). Use `read_file` with `offset` parameter to continue reading from {resource_id}."

# ============ Environment ============
PIXEL_MULTIPLE = int(os.getenv("PIXEL_MULTIPLE", 16))
MCP_CONNECT_TIMEOUT = int(os.getenv("MCP_CONNECT_TIMEOUT", 120))
MCP_CALL_TIMEOUT = int(os.getenv("MCP_CALL_TIMEOUT", 1800))
WORKSPACE_BASE = Path(
    os.getenv(
        "DEEPPRESENTER_WORKSPACE_BASE",
        str(Path.home() / ".cache/deeppresenter"),
    )
)
DOWNLOAD_CACHE = WORKSPACE_BASE / "downloads"
TOOL_CACHE = PACKAGE_DIR / ".tools.json"

GLOBAL_ENV_LIST = [
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "all_proxy",
    "PYTHONWARNINGS",
    "DOCKER_API_VERSION",
    "COMPOSE_API_VERSION",
]

# ============ Webview ============
PDF_OPTIONS = {
    "print_background": True,
    "landscape": False,
    "margin": {"top": "0mm", "right": "0mm", "bottom": "0mm", "left": "0mm"},
    "prefer_css_page_size": False,
    "display_header_footer": False,
    "scale": 1,
    "page_ranges": "1",
}

# ============ Additional Agent Prompt ===========

AGENT_PROMPT = """
<环境>
当前时间：{time}
工作目录：{workspace}
平台：Debian Linux 容器

预装工具：
- Python 3.13, Node.js, imagemagic, mermaid-cli (mmdc), curl, wget 等常用工具
- python-pptx, matplotlib, plotly 等常用包
可自由安装所需工具、包或命令行工具
</环境>

<任务指南>
- 探索原则：剩余计算预算 10% 时发出警告，此前充分探索并尽力完成
- 最大长度：工具调用输出超过 {cutoff_len} 字符将在前一个换行符处截断，完整内容保存在本地，可通过 `read_file` 的 `offset` 参数访问
- 工具调用原则：
    1. 每次响应必须包含推理内容和有效工具调用
    2. 所有工具调用并行处理，不要在同一轮中发出有相互依赖的工具调用
- 工具调用限制：每轮最多调用 {max_toolcall_per_turn} 个工具
- Matplotlib 指南：用 matplotlib 生成图表时，不要在标题、标签或注释中使用 emoji 或特殊 Unicode 符号（如 🌍🌋🔥），改用纯文本描述，因为大多数图表字体不支持 emoji
</任务指南>
"""

# 长上下文理解和多视角检索
MA_RESEACHER_PROMPT = """
<子智能体指南>
可用子智能体并行执行多个复杂任务，它们能力相同但上下文为空。
子智能体工具接受最小 `task` 和 `context_file`。
调用子智能体前，自己将完整委托说明写入本地文件。
将完整背景、源路径、约束、预期交付物和 handoff 格式放入该文件。
保持 `task` 简短且面向行动。
通常应在可大规模并行且无信息丢失的场景中使用子智能体，例如：
1. 长文档理解：20,000 行文档可分配每个智能体 1,000 行，并行启动 20 个子智能体
2. 多视角检索：从多个方面分析一个主题，如汽车的外观设计、配置定价、开发历史
</子智能体指南>
"""

# 定义全局 CSS 后并行生成多页
MA_RRESENTER_PROMPT = """
<子智能体指南>
可用子智能体并行执行多个复杂任务，它们能力相同但上下文为空。
因此应先定义全局视觉主题为共享样式表文件（slides/style.css），包括 CSS reset、body 基础样式、颜色变量、通用装饰元素和可复用组件类。
然后将每页幻灯片草稿生成分配给不同子智能体。
子智能体工具接受最小 `task` 和 `context_file`。
调用子智能体前，将共享视觉系统路径（slides/style.css）、文稿摘录、幻灯片范围、约束和 handoff 要求写入本地文件。
每个子智能体应 `read_file("slides/style.css")` 加载共享设计系统，然后在幻灯片 HTML 的 `<style>` 标签中仅生成页面特定布局 CSS，通过 `<link rel="stylesheet" href="style.css">` 引用共享样式表。
保持 `task` 为简短动作，如"使用共享 style.css 生成第 1 页幻灯片"。
</子智能体指南>
"""


OFFLINE_PROMPT = """
<离线模式>
- 处于离线模式，无网络访问，所有依赖网络的工具已移除
- 专注于可用工具并相应调整计划
</离线模式>
"""

CONTEXT_MODE_PROMPT = """
<上下文模式>
- 上下文接近上限时会要求生成摘要并保存到本地，这是正常流程，不是任务结束
- 保存摘要后必须继续完成剩余工作，禁止因"上下文不足"调用 finalize
- 为最小化信息丢失，生成或检索后立即保存文件、图片和中间结果，不要延迟
- 压缩后仅保留前几条消息、最近消息和保存的摘要，其他上下文将被丢弃
</上下文模式>
"""


HALF_BUDGET_NOTICE_MSG = {
    "text": "<NOTICE>已使用约一半工作预算，现在专注于核心任务，跳过不必要的步骤或探索。</NOTICE>",
    "type": "text",
}
URGENT_BUDGET_NOTICE_MSG = {
    "text": "<URGENT>工作预算将近耗尽，必须立即完成核心任务并调用 `finalize`，否则工作将失败。跳过检查和验证等额外步骤。</URGENT>",
    "type": "text",
}
HIST_LOST_MSG = {
    "text": "<NOTICE>此点与下一条消息之间的历史记录已压缩为摘要</NOTICE>",
    "type": "text",
}

CONTINUE_MSG = {
    "text": "<NOTICE>历史记录已压缩，参考保存的摘要并继续工作。任务未完成，禁止调用 finalize。</NOTICE>",
    "type": "text",
}

LAST_ITER_MSG = {
    "text": "<URGENT>工作预算将近耗尽，必须立即完成核心任务并调用 `finalize`，否则工作将失败。跳过检查和验证等额外步骤。</URGENT>",
    "type": "text",
}

MEMORY_COMPACT_MSG = """
已达到本次对话的上下文长度限制，立即从工具交互历史中提取关键信息，生成完整状态摘要并保存到工作目录，确保后续对话无缝继续。

<摘要要求>
所有信息必须记录具体细节，不要使用"如上所述"或"见前文"等引用，仅从当前会话的工具交互中提取信息，不记录用户或系统指令提供的信息。

1. 收集的信息与数据
   - 事实数据、证据、研究结果
   - 关键源材料和参考

2. 不确定性与未解决问题
   - 信息缺口、未验证假设、已识别限制

3. 生成的工件
   - 中间文件、代码、图片/图表：路径 + 用途

4. 下一步
   - 已完成工作和成果
   - 剩余任务和建议执行顺序
   - 已计划/启动但未完成的项目

5. 经验教训（如适用）
   - 工具调用中遇到的问题及其解决方案
   - 应避免的操作
</摘要要求>

<重要>
- 使用 {language} 作为主要语言，摘要必须足够详细，使任何后续者无需查看历史即可完全理解当前进度并继续工作
- 在本轮完成摘要生成，不要规划多轮生成，否则历史将丢失，直接保存到工作目录
- 摘要保存后必须继续完成剩余工作，禁止以"上下文不足"为由调用 finalize
</重要>
"""
