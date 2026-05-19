import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

# Load .env from the AIMA workspace (parent of web/)
_env_path = Path(__file__).parent.parent.parent / ".env"
load_dotenv(_env_path)


@dataclass
class Config:
    blotato_api_key: str
    anthropic_api_key: str
    groq_api_key: str
    linkedin_account_id: str
    instagram_account_id: str
    freepik_api_key: str

    @property
    def llm_provider(self) -> str:
        """'anthropic' if key present, else 'groq'. Raises if neither is set."""
        if self.anthropic_api_key:
            return "anthropic"
        if self.groq_api_key:
            return "groq"
        raise RuntimeError(
            "No LLM key found. Set ANTHROPIC_API_KEY or GROQ_API_KEY in .env"
        )


def load_config() -> Config:
    blotato = os.environ.get("BLOTATO_API_KEY", "")
    if not blotato:
        raise RuntimeError("BLOTATO_API_KEY is not set in .env")
    return Config(
        blotato_api_key=blotato,
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        groq_api_key=os.environ.get("GROQ_API_KEY", ""),
        linkedin_account_id=os.environ.get("BLOTATO_LINKEDIN_ACCOUNT_ID", ""),
        instagram_account_id=os.environ.get("BLOTATO_INSTAGRAM_ACCOUNT_ID", ""),
        freepik_api_key=os.environ.get("FREEPIK_API_KEY", ""),
    )
