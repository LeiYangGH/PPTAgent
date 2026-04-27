import asyncio
import json
import uuid
from abc import abstractmethod
from collections.abc import AsyncGenerator
from datetime import datetime
from pathlib import Path
from typing import Literal

import jsonlines
import yaml
from jinja2 import Template
from jinja2.runtime import StrictUndefined
from openai.types.chat.chat_completion_message import ChatCompletionMessage
from openai.types.chat.chat_completion_message_tool_call import (
    ChatCompletionMessageFunctionToolCall as ToolCall,
)
from pydantic import BaseModel

from deeppresenter.agents.env import AgentEnv
from deeppresenter.utils.config import (
    LLM,
    DeepPresenterConfig,
    get_json_from_response,
)
from deeppresenter.utils.constants import (
    AGENT_PROMPT,
    CONTEXT_MODE_PROMPT,
    CONTINUE_MSG,
    HALF_BUDGET_NOTICE_MSG,
    HIST_LOST_MSG,
    LAST_ITER_MSG,
    MA_RESEACHER_PROMPT,
    MA_RRESENTER_PROMPT,
    MAX_LOGGING_LENGTH,
    MAX_TOOLCALL_PER_TURN,
    MEMORY_COMPACT_MSG,
    OFFLINE_PROMPT,
    PACKAGE_DIR,
    URGENT_BUDGET_NOTICE_MSG,
)
from deeppresenter.utils.log import (
    debug,
    info,
    timer,
    warning,
)
from deeppresenter.utils.typings import (
    ChatMessage,
    Cost,
    InputRequest,
    Role,
    RoleConfig,
)


class Agent:
    def __init__(
        self,
        config: DeepPresenterConfig,
        agent_env: AgentEnv,
        workspace: Path,
        language: Literal["zh", "en"],
        config_file: str | None = None,
        keep_reasoning: bool = True,
        max_turns: int | None = None,
    ):
        self.name = self.__class__.__name__
        self.cost = Cost()
        self.context_length = 0
        self.context_warning = 0
        self.workspace = workspace
        self.agent_env = agent_env
        self.language = language
        self.keep_reasoning = keep_reasoning
        self.context_window = config.context_window
        self.max_context_turns = config.max_context_folds
        self.max_turns = max_turns
        self.turn_count = 0
        config_file = (
            Path(config_file)
            if config_file
            else PACKAGE_DIR / "roles" / f"{self.name}.yaml"
        )
        if not config_file.exists():
            raise FileNotFoundError(f"未找到角色配置文件: {config_file} ")

        # Setting basic context
        workspace.mkdir(parents=True, exist_ok=True)
        with open(config_file, encoding="utf-8") as f:
            config_data = yaml.safe_load(f)
        self.role_config = RoleConfig(**config_data)
        self.llm: LLM = config[self.role_config.use_model]
        self.model = self.llm.model_name
        self._setup_toolset()
        if language not in self.role_config.system:
            warning(f"未找到语言 '{language}' 的系统提示词，回退为 'zh'")
            language = "zh"
        self.language = language
        self.error_history: list[ToolCall | ChatMessage] = []
        self.research_iter = 0
        if config.context_folding:
            self.context_warning = -1

        # Setting tools and interative context
        self.system = self.role_config.system[language]
        self.prompt: Template = Template(
            self.role_config.instruction, undefined=StrictUndefined
        )
        # ? for those agents equipped with sandbox only
        if any(t["function"]["name"] == "execute_command" for t in self.tools):
            self.system += AGENT_PROMPT.format(
                workspace=self.workspace,
                cutoff_len=self.agent_env.cutoff_len,
                time=datetime.now().strftime("%Y-%m-%d"),
                max_toolcall_per_turn=MAX_TOOLCALL_PER_TURN,
            )

        if any(t["function"]["name"] == "delegate_subagent" for t in self.tools):
            if self.name == "Research":
                self.system += MA_RESEACHER_PROMPT
            elif self.name == "Design":
                self.system += MA_RRESENTER_PROMPT

        if config.offline_mode:
            self.system += OFFLINE_PROMPT

        if config.context_folding:
            self.system += CONTEXT_MODE_PROMPT

        self.chat_history: list[ChatMessage] = [
            ChatMessage(role=Role.SYSTEM, content=self.system)
        ]
        available_tools = [tool["function"]["name"] for tool in self.tools]
        debug(
            f"{self.name} 智能体获得 {len(self.tools)} 个工具: {', '.join(available_tools)}"
        )

    def _setup_toolset(self):
        toolset = self.role_config.toolset
        if toolset.include_tool_servers == "all":
            toolset.include_tool_servers = list(self.agent_env._server_tools)
        for server in toolset.include_tool_servers:
            assert server in self.agent_env._server_tools, (
                f"服务器 {server} 不可用"
            )
        self.tools = []
        for server in toolset.include_tool_servers:
            if server not in toolset.exclude_tool_servers:
                for tool in self.agent_env._server_tools[server]:
                    if tool not in toolset.exclude_tools:
                        self.tools.append(self.agent_env._tools_dict[tool])

        for tool_name, tool in self.agent_env._tools_dict.items():
            if tool_name in toolset.include_tools:
                self.tools.append(tool)

    async def chat(
        self,
        message: ChatMessage,
        response_format: type[BaseModel] | None = None,
        **chat_kwargs,
    ) -> ChatMessage:
        if len(self.chat_history) == 1:
            self.chat_history.append(
                ChatMessage(role=Role.USER, content=self.prompt.render(**chat_kwargs))
            )
            self.log_message(self.chat_history[-1])
        self.chat_history.append(message)
        self.log_message(self.chat_history[-1])
        with timer(f"{self.name} Agent LLM chat"):
            response = await self.llm.run(
                messages=self.chat_history,
                response_format=response_format,
            )
            if response.usage is not None:
                self.cost += response.usage
                self.context_length = response.usage.total_tokens
            self.chat_history.append(
                ChatMessage(
                    role=Role.ASSISTANT,
                    content=response.choices[0].message.content,
                    cost=response.usage,
                    reasoning=getattr(response.choices[0].message, "reasoning", None)
                    if self.keep_reasoning
                    else None,
                )
            )
            self.log_message(self.chat_history[-1])
            return self.chat_history[-1]

    async def action(
        self,
        **chat_kwargs,
    ):
        """Tool calling interface"""
        self.turn_count += 1
        if self.max_turns is not None:
            if self.turn_count > self.max_turns:
                raise RuntimeError(
                    f"{self.name} 超过最大轮次: {self.turn_count - 1}/{self.max_turns}"
                )
            remaining = self.max_turns - self.turn_count
            if remaining <= 5 and remaining > 0:
                self.chat_history[-1].content.append(
                    {
                        "type": "text",
                        "text": f"<URGENT>仅剩 {remaining} 轮（共 {self.max_turns} 轮）。必须立即调用 `finalize` 完成当前任务，不要开始新任务或幻灯片。</URGENT>",
                    }
                )

        if len(self.chat_history) == 1:
            self.chat_history.append(
                ChatMessage(
                    role=Role.USER,
                    content=self.prompt.render(**chat_kwargs),
                )
            )
            self.log_message(self.chat_history[-1])

        with timer(f"{self.name} Agent LLM call"):
            try:
                response = await self.llm.run(
                    messages=self.chat_history,
                    tools=self.tools,
                )
            except Exception as e:
                err_msg = str(e).lower()
                if (
                    "exceed" in err_msg and "context" in err_msg
                    and self.context_warning == -1
                ):
                    info(
                        f"{self.name} 上下文溢出检测到，正在压缩历史"
                    )
                    await self.compact_history()
                    response = await self.llm.run(
                        messages=self.chat_history,
                        tools=self.tools,
                    )
                else:
                    raise
            if response.usage is not None:
                self.cost += response.usage
                self.context_length = response.usage.total_tokens
            agent_message: ChatCompletionMessage = response.choices[0].message
        self.chat_history.append(
            ChatMessage(
                role=Role.ASSISTANT,
                content=agent_message.content,
                cost=response.usage,
                tool_calls=agent_message.tool_calls,
                reasoning=getattr(agent_message, "reasoning", None)
                if self.keep_reasoning
                else None,
            )
        )
        self.log_message(self.chat_history[-1])
        return self.chat_history[-1]

    @abstractmethod
    async def loop(
        self, req: InputRequest, *args, **kwargs
    ) -> AsyncGenerator[str | ChatMessage, None]:
        """
        Loop interface, return the message or the outcome filepath of the agent.
        """

    @abstractmethod
    async def finish(self, result: str):
        """This function defines when and how should an agent finish their tasks, combined with outcome check"""

    async def execute(self, tool_calls: list[ToolCall]) -> str | list[ChatMessage]:
        coros = []
        observations: list[ChatMessage] = []
        used_tools = set()
        finish_id = None
        outcome = None
        for t in tool_calls:
            arguments = t.function.arguments
            if len(arguments) == 0:
                arguments = None
            else:
                try:
                    assert len(tool_calls) <= MAX_TOOLCALL_PER_TURN, (
                        f"工具调用过多 ({len(tool_calls)})，最多允许 {MAX_TOOLCALL_PER_TURN} 个"
                    )
                    arguments = get_json_from_response(t.function.arguments)
                    if t.function.name == "finalize":
                        arguments["agent_name"] = self.name
                        finish_id = t.id
                        assert "outcome" in arguments, (
                            "finalize 工具调用必须包含 outcome"
                        )
                        outcome = arguments["outcome"]
                    assert isinstance(arguments, dict), (
                        f"工具调用参数必须是字典或空，但给出了 {arguments}"
                    )
                    t.function.arguments = json.dumps(arguments, ensure_ascii=False)
                except AssertionError as e:
                    observations.append(
                        ChatMessage(
                            role=Role.TOOL,
                            content=str(e),
                            tool_call_id=t.id,
                            is_error=True,
                        )
                    )
                    info(f"工具调用 `{t.function}` 遇到错误: {e}")
                    continue
            used_tools.add(t.function.name)
            info(f"{self.name} 智能体正在调用工具 `{t.function.name}`")
            coros.append(self.agent_env.tool_execute(t))

        observations.extend(await asyncio.gather(*coros))
        for obs in observations:
            if obs.has_image:
                if "gemini" in self.model.lower() or "qwen" in self.model.lower():
                    obs.role = Role.USER
                if "claude" in self.model.lower():
                    oai_b64 = obs.content[0]["image_url"]["url"]
                    obs.content = [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": oai_b64.split(";")[0].split(":")[1],
                                "data": oai_b64.split(",")[1],
                            },
                        }
                    ]

        self.chat_history.extend(observations)

        tool_call_map = {t.id: t for t in tool_calls}
        for o in observations:
            if o.is_error:
                t = tool_call_map[o.tool_call_id]
                self.error_history.append(t)
                self.error_history.append(o)

        if finish_id is not None:
            for obs in observations:
                if obs.tool_call_id == finish_id and obs.text == outcome:
                    info(f"{self.name} 智能体已完成，结果: {obs.text}")
                    return obs.text

        if (
            self.context_warning == 0
            and self.context_length > self.context_window * 0.5
        ):
            self.context_warning += 1
            observations[0].content.insert(0, HALF_BUDGET_NOTICE_MSG)
        elif (
            self.context_warning == 1
            and self.context_length > self.context_window * 0.8
        ):
            observations[0].content.insert(0, URGENT_BUDGET_NOTICE_MSG)
            self.context_warning = 2

        for obs in observations:
            self.log_message(obs)

        if self.context_length > self.context_window:
            if self.context_warning == -1:
                await self.compact_history()
            else:
                raise RuntimeError(
                    f"{self.name} 智能体超出上下文窗口: {self.context_length}/{self.context_window}"
                )
        return observations

    def log_message(self, msg: ChatMessage):
        if len(msg.text) < MAX_LOGGING_LENGTH:
            debug(f"{self.name}: {msg.text}")
        else:
            debug(f"{self.name}: {msg.text[:MAX_LOGGING_LENGTH]}...")

    async def compact_history(self, keep_head: int = 10, keep_tail: int = 4):
        """Summarize the history."""
        # ? it's 10 = system + user + (thinking, read, design, write)*2
        if keep_head + keep_tail > len(self.chat_history):
            return

        if self.research_iter == self.max_context_turns:
            return

        self.save_history(message_only=True)
        self.research_iter += 1
        head, tail = self._split_history(keep_head, keep_tail)
        summary_ask = ChatMessage(
            role=Role.USER, content=MEMORY_COMPACT_MSG.format(language=self.language)
        )
        response = await self.llm.run(
            self.chat_history + [summary_ask],
            tools=self.tools,
        )
        agent_message = response.choices[0].message
        summary_message = ChatMessage(
            id=f"context_fold_{uuid.uuid4().hex[:8]}",
            role=agent_message.role,
            content=agent_message.content,
            tool_calls=agent_message.tool_calls,
            reasoning=getattr(agent_message, "reasoning", None)
            if self.keep_reasoning
            else None,
        )
        debug(
            f"研究迭代 {self.research_iter:02d} 的摘要: \n"
            + summary_message.text
        )
        tasks = [
            self.agent_env.tool_execute(tc) for tc in summary_message.tool_calls or []
        ]
        observations = await asyncio.gather(*tasks)
        observations[-1].content.append(CONTINUE_MSG)
        if self.research_iter == self.max_context_turns:
            observations[-1].content.append(LAST_ITER_MSG)
        new_tail = [
            summary_ask,
            summary_message,
            *observations,
        ]
        self.chat_history = head + tail + new_tail

    def _split_history(self, keep_head, keep_tail):
        # ensure the left context window contains the paired tool call and tool call result
        head = []
        for msg in self.chat_history:
            if len(head) < keep_head or msg.role == Role.TOOL:
                head.append(msg)
            else:
                break
        head[-1].content.append(HIST_LOST_MSG)

        tail = self.chat_history[-keep_tail:]
        for i, m in enumerate(tail):
            if m.role == Role.ASSISTANT and m not in head:
                tail = tail[i:]
                break
        else:
            tail = []

        return head, tail

    def save_history(self, hist_dir: Path | None = None, message_only: bool = False):
        hist_dir = hist_dir or self.workspace / ".history"
        hist_dir.mkdir(parents=True, exist_ok=True)

        history_file = hist_dir / f"{self.name}-history.jsonl"
        if self.research_iter >= 0:
            history_file = (
                hist_dir / f"{self.name}-{self.research_iter:02d}-history.jsonl"
            )
        with jsonlines.open(history_file, mode="w") as writer:
            for message in self.chat_history:
                writer.write(message.model_dump())

        if message_only:
            return

        config_file = hist_dir / f"{self.name}-config.json"
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "name": self.name,
                    "model": self.model,
                    "context_window": self.context_length,
                    "cost": self.cost.model_dump(),
                    "tools": self.tools,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )

        if self.error_history:
            error_file = hist_dir / f"{self.name}-errors.jsonl"
            with jsonlines.open(error_file, mode="w") as writer:
                for msg in self.error_history:
                    writer.write(msg.model_dump())

        debug(
            f"{self.name} 完成 | 花费:{self.cost} 上下文:{self.context_length} | 历史:{history_file.name} 配置:{config_file.name}"
        )
