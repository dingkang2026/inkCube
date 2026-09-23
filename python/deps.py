from db.database import Database
from agent.task_agent import TaskAgent
from knowledge.model_manager import ModelManager
from knowledge.task_queue import KnowledgeWorker

db: Database = None  # type: ignore
task_agent: TaskAgent = None  # type: ignore
model_manager: ModelManager = None  # type: ignore
worker: KnowledgeWorker = None  # type: ignore
