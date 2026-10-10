from dataclasses import dataclass, field
import os
from pathlib import Path
from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    database_path: str = "./data/justread.sqlite3"
    llm_provider: str = "openai"
    llm_model: str = ""
    llm_api_key: str = field(default="", repr=False)
    llm_base_url: str = ""
    search_provider: str = "auto"
    search_api_key: str = field(default="", repr=False)
    search_base_url: str = "https://api.tavily.com"
    provider_timeout_seconds: float = 60
    task_timeout_seconds: float = 1800
    max_sources: int = 8
    workers: int = 2
    max_active_tasks: int = 100
    terminal_retention_seconds: int = 86400
    session_ttl_seconds: int = 30 * 86400
    session_cookie_secure: bool = False
    public_origin: str = ""
    static_dir: str = "../dist"
    test_mode: bool = False
    fixture_delay_seconds: float = 0.1

    @property
    def effective_search_provider(self) -> str:
        if self.search_provider != "auto":
            return self.search_provider
        if self.llm_provider == "openrouter" and not self.search_api_key.strip():
            return "openrouter"
        return "tavily"

    @property
    def search_ready(self) -> bool:
        if self.effective_search_provider == "openrouter":
            return self.llm_provider == "openrouter" and bool(self.llm_model.strip() and self.llm_api_key.strip())
        return self.effective_search_provider == "tavily" and bool(self.search_api_key.strip())

    @property
    def ready(self) -> bool:
        if self.llm_provider == "fixture":
            return self.test_mode
        return self.llm_provider in {"openai", "anthropic", "openrouter"} and bool(
            self.llm_model.strip() and self.llm_api_key.strip()
        )

    def validate(self) -> None:
        if self.llm_provider not in {"openai", "anthropic", "openrouter", "fixture"}:
            raise ValueError("Unsupported JUSTREAD_LLM_PROVIDER")
        if self.llm_provider == "fixture" and not self.test_mode:
            raise ValueError("Fixture provider requires JUSTREAD_TEST_MODE=true")
        if self.search_provider not in {"auto", "tavily", "openrouter"}:
            raise ValueError("Unsupported JUSTREAD_SEARCH_PROVIDER")
        if self.search_provider == "openrouter" and self.llm_provider != "openrouter":
            raise ValueError("OpenRouter search requires JUSTREAD_LLM_PROVIDER=openrouter")
        if not 1 <= self.max_sources <= 20:
            raise ValueError("max_sources must be between 1 and 20")
        if not 1 <= self.workers <= 16 or self.max_active_tasks < 1:
            raise ValueError("Invalid worker/queue limits")
        if min(self.provider_timeout_seconds, self.task_timeout_seconds,
               self.terminal_retention_seconds, self.session_ttl_seconds) <= 0:
            raise ValueError("Timeouts must be positive")

    @classmethod
    def from_env(cls) -> "Settings":
        # Per-machine secrets are kept separately from the copied example config.
        # Existing process environment always wins; tests may disable dotenv loading.
        project = Path(__file__).resolve().parents[2]
        load_dotenv(project / ".env.local")
        load_dotenv(project / ".env")
        values = {}
        defaults = cls()
        for name in cls.__dataclass_fields__:
            raw = os.getenv("JUSTREAD_" + name.upper())
            if raw is None:
                continue
            default = getattr(defaults, name)
            if isinstance(default, bool):
                if raw.lower() not in {"true", "false", "1", "0"}:
                    raise ValueError(f"Invalid boolean setting: {name}")
                values[name] = raw.lower() in {"true", "1"}
            elif isinstance(default, int):
                values[name] = int(raw)
            elif isinstance(default, float):
                values[name] = float(raw)
            else:
                values[name] = raw
        result = cls(**values)
        result.validate()
        return result
