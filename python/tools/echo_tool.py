from tools.base import BaseTool


class EchoTool(BaseTool):
    name = "echo"
    description = "回显输入文本，用于测试工具调用链路"
    parameters = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "要回显的文本"},
        },
        "required": ["text"],
    }

    async def execute(self, text: str) -> str:
        return f"Echo: {text}"
