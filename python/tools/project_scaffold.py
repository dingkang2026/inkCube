"""project_scaffold tool — generate project boilerplate from templates."""
import json
import os
from pathlib import Path

from tools.base import BaseTool


# ── Built-in project templates ──

TEMPLATES = {
    "python-cli": {
        "description": "Python 命令行工具项目",
        "files": {
            "main.py": '''"""Main entry point."""
import argparse
import sys


def main():
    parser = argparse.ArgumentParser(description="{{project_name}}")
    parser.add_argument("--name", default="World", help="Name to greet")
    args = parser.parse_args()
    print(f"Hello, {args.name}!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
''',
            "requirements.txt": "",
            "README.md": "# {{project_name}}\n\nA Python CLI project.\n\n## Usage\n\n```bash\npython main.py --name YourName\n```\n",
            "pyproject.toml": '''[project]
name = "{{project_name}}"
version = "0.1.0"
requires-python = ">=3.10"

[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.backends._legacy:_Backend"
''',
        },
    },
    "python-flask": {
        "description": "Python Flask Web 应用项目",
        "files": {
            "app.py": '''"""Flask application."""
from flask import Flask, jsonify


def create_app():
    app = Flask(__name__)

    @app.route("/")
    def index():
        return jsonify({"message": "Hello from {{project_name}}!"})

    @app.route("/health")
    def health():
        return jsonify({"status": "ok"})

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, port=5000)
''',
            "requirements.txt": "flask>=3.0\n",
            "README.md": "# {{project_name}}\n\nA Flask web application.\n\n## Quick Start\n\n```bash\npip install -r requirements.txt\npython app.py\n```\n\nVisit http://localhost:5000\n",
            "pyproject.toml": '''[project]
name = "{{project_name}}"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = ["flask>=3.0"]
''',
        },
    },
    "python-fastapi": {
        "description": "Python FastAPI 项目",
        "files": {
            "main.py": '''"""FastAPI application."""
from fastapi import FastAPI


app = FastAPI(title="{{project_name}}")


@app.get("/")
async def root():
    return {"message": "Hello from {{project_name}}!"}


@app.get("/health")
async def health():
    return {"status": "ok"}
''',
            "requirements.txt": "fastapi>=0.100\nuvicorn[standard]>=0.23\n",
            "README.md": "# {{project_name}}\n\nA FastAPI application.\n\n## Quick Start\n\n```bash\npip install -r requirements.txt\nuvicorn main:app --reload\n```\n\nVisit http://localhost:8000\n",
            "pyproject.toml": '''[project]
name = "{{project_name}}"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = ["fastapi>=0.100", "uvicorn[standard]>=0.23"]
''',
        },
    },
    "react-ts": {
        "description": "React + TypeScript 前端项目",
        "files": {
            "package.json": '''{
  "name": "{{project_name}}",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^18.3.0",
    "react-dom": "^18.3.0"
  },
  "devDependencies": {
    "@types/react": "^18.3.0",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.0",
    "typescript": "^5.5.0",
    "vite": "^5.4.0"
  }
}
''',
            "tsconfig.json": '''{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true
  },
  "include": ["src"]
}
''',
            "src/App.tsx": '''import { useState } from "react";

function App() {
  const [count, setCount] = useState(0);

  return (
    <div>
      <h1>{{project_name}}</h1>
      <button onClick={() => setCount((c) => c + 1)}>count: {count}</button>
    </div>
  );
}

export default App;
''',
            "src/main.tsx": '''import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
''',
            "index.html": '''<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>{{project_name}}</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
''',
            "README.md": "# {{project_name}}\n\nA React + TypeScript project.\n\n## Quick Start\n\n```bash\nnpm install\nnpm run dev\n```\n",
            "vite.config.ts": '''import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
});
''',
        },
    },
}


class ProjectScaffoldTool(BaseTool):
    name = "project_scaffold"
    description = (
        "在工作区中生成项目骨架。"
        "支持多种项目模板（Python CLI、Flask、FastAPI、React+TS），"
        "自动生成目录结构、配置文件和 README。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "template": {
                "type": "string",
                "enum": ["python-cli", "python-flask", "python-fastapi", "react-ts"],
                "description": "项目模板类型",
            },
            "project_name": {
                "type": "string",
                "description": "项目名称（用于替换模板中的占位符），默认为 'my-project'",
            },
            "output_dir": {
                "type": "string",
                "description": "输出目录的相对路径（相对于工作区），默认为 '.'（直接在工作区根目录生成）",
            },
        },
        "required": ["template"],
    }

    @property
    def prompt_description(self) -> str:
        template_list = "\n".join(
            f"- `{k}`: {v['description']}" for k, v in TEMPLATES.items()
        )
        return (
            "## project_scaffold\n"
            "生成项目骨架，快速创建项目结构。\n\n"
            "可用模板:\n"
            f"{template_list}\n\n"
            "用法:\n"
            "- 用户要求创建新项目/项目脚手架时，选择合适的模板\n"
            "- 生成的文件会包含项目名称占位符替换\n"
            "- 生成后用 code_executor 或 package_manager 安装依赖\n\n"
            "示例:\n"
            '```\n{"template": "python-fastapi", "project_name": "my-api"}\n```'
        )

    def __init__(self, workspace_dir: str = ""):
        self.workspace_dir = workspace_dir

    def set_workspace(self, workspace_dir: str):
        self.workspace_dir = workspace_dir

    async def execute(
        self,
        template: str,
        project_name: str = "my-project",
        output_dir: str = ".",
    ) -> str:
        if not self.workspace_dir:
            return json.dumps({"error": "工作目录未设置"}, ensure_ascii=False)

        if template not in TEMPLATES:
            available = ", ".join(TEMPLATES.keys())
            return json.dumps({
                "error": f"未知模板: {template}",
                "available_templates": list(TEMPLATES.keys()),
            }, ensure_ascii=False)

        workspace = Path(self.workspace_dir).resolve()
        target_dir = (workspace / output_dir).resolve()
        if target_dir != workspace and not str(target_dir).startswith(str(workspace) + os.sep):
            return json.dumps({"error": "输出路径超出工作区范围"}, ensure_ascii=False)

        tmpl = TEMPLATES[template]
        created_files = []
        skipped_files = []

        try:
            target_dir.mkdir(parents=True, exist_ok=True)

            for rel_path, content in tmpl["files"].items():
                # Replace project name placeholder
                content = content.replace("{{project_name}}", project_name)

                file_path = target_dir / rel_path
                if file_path.exists():
                    skipped_files.append(rel_path)
                    continue

                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(content, encoding="utf-8")
                created_files.append(rel_path)

            return json.dumps({
                "exit_code": 0,
                "template": template,
                "project_name": project_name,
                "output_dir": str(target_dir.relative_to(workspace)) if target_dir != workspace else ".",
                "created_files": created_files,
                "skipped_files": skipped_files,
                "total_created": len(created_files),
                "next_steps": self._suggest_next_steps(template),
            }, ensure_ascii=False)

        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    @staticmethod
    def _suggest_next_steps(template: str) -> list[str]:
        """Suggest next steps after scaffolding."""
        if template.startswith("python"):
            return [
                "用 package_manager 安装依赖: pip install -r requirements.txt",
                "用 code_executor 运行项目验证是否正常",
                "用 code_lint 检查代码质量",
            ]
        elif template == "react-ts":
            return [
                "运行 npm install 安装依赖",
                "运行 npm run dev 启动开发服务器",
            ]
        return []
