import os
from dataclasses import dataclass
from pathlib import Path


def home() -> Path:
    path = Path(os.environ.get("MEMBRIDGE_HOME", Path.home() / ".membridge"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def project_key(path: str | Path) -> str:
    return os.path.normcase(str(Path(path).expanduser().resolve()))


@dataclass
class Settings:
    database_url: str
    api_key: str = ""
    llm_url: str = ""
    llm_key: str = ""
    llm_model: str = ""
    embedding_model: str = ""
    ingestion_url: str = ""
    ai_url: str = ""

    @classmethod
    def load(cls):
        return cls(
            database_url=os.getenv("MEMBRIDGE_DATABASE_URL") or f"sqlite:///{home() / 'memory.db'}",
            api_key=os.getenv("MEMBRIDGE_API_KEY", ""),
            llm_url=os.getenv("MEMBRIDGE_LLM_URL", ""),
            llm_key=os.getenv("MEMBRIDGE_LLM_KEY", ""),
            llm_model=os.getenv("MEMBRIDGE_LLM_MODEL", ""),
            embedding_model=os.getenv("MEMBRIDGE_EMBEDDING_MODEL", ""),
            ingestion_url=os.getenv("MEMBRIDGE_INGESTION_URL", ""),
            ai_url=os.getenv("MEMBRIDGE_AI_URL", ""),
        )
