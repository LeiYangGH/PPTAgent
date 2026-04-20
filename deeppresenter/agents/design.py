from deeppresenter.agents.agent import Agent
from deeppresenter.utils.constants import MAX_AGENT_TURNS
from deeppresenter.utils.typings import InputRequest


class Design(Agent):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("max_turns", MAX_AGENT_TURNS)
        super().__init__(*args, **kwargs)

    async def loop(self, req: InputRequest, markdown_file: str):
        (self.workspace / "slides").mkdir(exist_ok=True)
        while True:
            agent_message = await self.action(
                markdown_file=markdown_file, prompt=req.designagent_prompt
            )
            yield agent_message
            outcome = await self.execute(self.chat_history[-1].tool_calls)
            if isinstance(outcome, list):
                for item in outcome:
                    yield item
            else:
                break

        yield outcome
