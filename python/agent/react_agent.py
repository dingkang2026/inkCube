import asyncio
import json
import logging
import re
from typing import AsyncGenerator

from llm.client import LLMClient
from tools.registry import tool_registry
from db.database import Database

logger = logging.getLogger(__name__)


REACT_SYSTEM_PROMPT = """你是一个智能助手"墨方"。你可以使用工具来帮助回答问题。

请按照以下格式思考并回答：

Thought: 分析用户的问题，决定是否需要使用工具
Action: 如果需要工具，选择一个工具并调用
Action Input: 工具的输入参数 (JSON 格式)
Observation: 工具返回的结果（由系统提供）
... (可以重复 Thought/Action/Observation 多次)
Thought: 现在我已经有了足够的信息来回答
Final Answer: 给用户的答案

可用工具:
{tools_description}

如果不需要使用工具，直接给出 Final Answer。

📊 富文本支持：
你可以在回答中使用 Markdown 格式，包括：
- Mermaid 图表：用 ```mermaid 代码块绘制流程图、时序图、甘特图等
- SVG 图形：直接嵌入 <svg> 标签绘制自定义图形
- 代码块、表格、列表等标准 Markdown 语法

示例（Mermaid 流程图）：
```mermaid
graph TD
    A[开始] --> B{条件判断}
    B -->|是| C[执行操作]
    B -->|否| D[结束]
```
"""


class ReactAgent:
    def __init__(self, db: Database):
        self.db = db
        self.max_iterations = 10
        self._cancel_event = asyncio.Event()
        self.update_settings(db.get_settings())

    def update_settings(self, settings: dict):
        self.settings = settings
        self.llm = LLMClient(settings)
        tool_registry.configure(settings)

    def request_cancel(self):
        self._cancel_event.set()

    def _reset_cancel(self):
        self._cancel_event.clear()

    def _get_tools_description(self) -> str:
        if not tool_registry.tools:
            return "（暂无可用工具）"
        lines = []
        for name, tool in tool_registry.tools.items():
            lines.append(f"- {name}: {tool.description}")
            lines.append(f"  参数: {json.dumps(tool.parameters, ensure_ascii=False)}")
        return "\n".join(lines)

    def _build_system_prompt(self) -> str:
        base = REACT_SYSTEM_PROMPT.format(tools_description=self._get_tools_description())
        if self.settings.get("system_prompt"):
            base = self.settings["system_prompt"] + "\n\n" + base
        return base

    def _parse_action(self, text: str) -> tuple[str, dict] | None:
        # ── Pattern 1: standard ReAct format ──
        action_line = None
        input_line = None
        for line in text.split("\n"):
            stripped = line.strip()
            if stripped.startswith("Action:"):
                action_line = stripped[len("Action:"):].strip()
            elif stripped.startswith("Action Input:"):
                input_line = stripped[len("Action Input:"):].strip()
        if action_line and input_line:
            try:
                params = json.loads(input_line)
                return action_line, params
            except json.JSONDecodeError:
                return action_line, {"input": input_line}

        # ── Pattern 2: fallback – "tool_name {...}" ──
        from tools.registry import tool_registry
        tool_names = "|".join(re.escape(n) for n in tool_registry.tools)
        if tool_names:
            m = re.search(
                rf"(?:^|\n)({tool_names})\s*(\{{.*\}})",
                text, re.DOTALL,
            )
            if m:
                name, raw_json = m.group(1), m.group(2)
                try:
                    return name, json.loads(raw_json)
                except json.JSONDecodeError:
                    pass

        return None

    def _extract_final_answer(self, text: str) -> str | None:
        lines = text.split("\n")
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("Final Answer:"):
                first_line = stripped[len("Final Answer:"):].strip()
                remaining = "\n".join(lines[i + 1:]).strip()
                if remaining:
                    return first_line + "\n" + remaining if first_line else remaining
                return first_line
        return None

    async def run(self, conversation_id: str, user_message: str) -> AsyncGenerator[dict, None]:
        logger.info("Conversation started: conv_id=%s, message_len=%d", conversation_id, len(user_message))
        self._reset_cancel()
        settings = self.db.get_settings()
        self.update_settings(settings)

        if not settings.get("api_key"):
            logger.info("Demo mode: no API key configured")
            async for event in self._demo_mode(conversation_id, user_message):
                yield event
            return

        history = self.db.get_messages(conversation_id)
        system_prompt = self._build_system_prompt()
        logger.debug("Built system prompt: len=%d, history_count=%d", len(system_prompt), len(history))

        messages = [{"role": "system", "content": system_prompt}]
        for msg in history:
            if msg["role"] in ("user", "assistant"):
                messages.append({"role": msg["role"], "content": msg["content"]})

        for iteration in range(self.max_iterations):
            if self._cancel_event.is_set():
                logger.info("Conversation cancelled by user")
                break

            logger.debug("LLM iteration %d/%d, messages_count=%d", iteration + 1, self.max_iterations, len(messages))
            llm_task = asyncio.create_task(self.llm.chat(messages, settings))
            cancel_task = asyncio.create_task(self._cancel_event.wait())
            done, pending = await asyncio.wait(
                [llm_task, cancel_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()

            if cancel_task in done:
                break
            if llm_task in done:
                try:
                    response = llm_task.result()
                    # Legacy path always returns str (reasoning_content only in streaming/FC)
                    response_text = response
                    logger.debug("LLM response received: len=%d",
                                len(response_text) if response_text else 0)
                except Exception as e:
                    logger.error("LLM call failed: %s", str(e), exc_info=True)
                    error_msg = f"调用模型出错: {str(e)}"
                    self.db.save_message(conversation_id, "assistant", error_msg)
                    yield {"type": "answer_chunk", "data": {"content": error_msg}}
                    yield {"type": "done", "data": {}}
                    return

            final_answer = self._extract_final_answer(response_text)
            if final_answer:
                logger.info("Final answer extracted at iteration %d: len=%d", iteration + 1, len(final_answer))
                self.db.save_message(conversation_id, "assistant", final_answer)
                yield {"type": "answer_chunk", "data": {"content": final_answer}}
                yield {"type": "done", "data": {}}
                return

            action = self._parse_action(response_text)
            if action:
                tool_name, tool_params = action
                logger.info("Tool selected: %s, params_keys=%s", tool_name, list(tool_params.keys()))
                thought_text = response_text.split("Action:")[0].strip()
                if thought_text.startswith("Thought:"):
                    thought_text = thought_text[len("Thought:"):].strip()

                yield {"type": "thought", "data": {"content": thought_text}}
                self.db.save_message(conversation_id, "thought", thought_text)

                yield {"type": "action", "data": {"tool": tool_name, "input": tool_params}}

                tool = tool_registry.get(tool_name)
                if tool:
                    try:
                        logger.debug("Executing tool: %s", tool_name)
                        result = await tool.execute(**tool_params)
                        logger.debug("Tool %s completed: result_len=%d", tool_name, len(result) if result else 0)
                    except Exception as e:
                        logger.error("Tool %s execution failed: %s", tool_name, str(e), exc_info=True)
                        result = f"工具执行出错: {str(e)}"
                else:
                    logger.warning("Unknown tool requested: %s", tool_name)
                    result = f"未知工具: {tool_name}"

                yield {"type": "observation", "data": {"content": result}}
                self.db.save_message(
                    conversation_id, "observation", result,
                    metadata={"tool_name": tool_name, "tool_input": tool_params},
                )
                self.db.save_message(
                    conversation_id, "action", tool_name,
                    metadata={"tool_name": tool_name, "tool_input": tool_params, "tool_output": result},
                )

                messages.append({"role": "assistant", "content": response_text})
                messages.append({"role": "user", "content": f"Observation: {result}"})
            else:
                self.db.save_message(conversation_id, "assistant", response_text)
                yield {"type": "answer_chunk", "data": {"content": response_text}}
                yield {"type": "done", "data": {}}
                return

        if self._cancel_event.is_set():
            fallback = "已停止生成。"
            logger.info("Conversation stopped by user")
        else:
            fallback = "抱歉，我尝试了多次但未能得出答案。请尝试换一种方式提问。"
            logger.warning("Conversation exhausted max_iterations=%d without final answer", self.max_iterations)
        self.db.save_message(conversation_id, "assistant", fallback)
        yield {"type": "answer_chunk", "data": {"content": fallback}}
        yield {"type": "done", "data": {}}

    async def _demo_mode(self, conversation_id: str, user_message: str) -> AsyncGenerator[dict, None]:
        answer = f"收到你的消息：「{user_message}」\n\n> 当前为演示模式。请在设置中配置 API Key 以启用 AI 对话。"
        self.db.save_message(conversation_id, "assistant", answer)
        yield {"type": "answer_chunk", "data": {"content": answer}}
        yield {"type": "done", "data": {}}
