import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

# Load .env from the web/ root (parent of api/)
_env_path = Path(__file__).parent.parent / ".env"
load_dotenv(_env_path)


@dataclass
class Config:
    blotato_api_key: str
    anthropic_api_key: str
    perplexity_api_key: str
    linkedin_account_id: str
    instagram_account_id: str
    higgsfield_api_key: str = ""
    higgsfield_api_secret: str = ""
    higgsfield_model: str = "higgsfield-ai/soul/standard"
    higgsfield_resolution: str = "1080p"
    higgsfield_video_model: str = "higgsfield-ai/text2video/turbo"
    higgsfield_video_aspect: str = "9:16"
    higgsfield_video_duration: int = 0  # 0 = don't send duration (let model default)

    @property
    def llm_provider(self) -> str:
        """'anthropic' if key present, else 'perplexity'. Raises if neither is set."""
        if self.anthropic_api_key:
            return "anthropic"
        if self.perplexity_api_key:
            return "perplexity"
        raise RuntimeError(
            "No LLM key found. Set ANTHROPIC_API_KEY or PERPLEXITY_API_KEY in .env"
        )

    @property
    def image_provider(self) -> str:
        """'higgsfield' if both key and secret are set, else 'pollinations' (free fallback)."""
        if self.higgsfield_api_key and self.higgsfield_api_secret:
            return "higgsfield"
        return "pollinations"

    @property
    def video_available(self) -> bool:
        """Video generation needs Higgsfield (key+secret) — there is no free fallback."""
        return bool(self.higgsfield_api_key and self.higgsfield_api_secret)


def load_config() -> Config:
    blotato = os.environ.get("BLOTATO_API_KEY", "")
    if not blotato:
        raise RuntimeError("BLOTATO_API_KEY is not set in .env")
    return Config(
        blotato_api_key=blotato,
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        perplexity_api_key=os.environ.get("PERPLEXITY_API_KEY", ""),
        linkedin_account_id=os.environ.get("BLOTATO_LINKEDIN_ACCOUNT_ID", ""),
        instagram_account_id=os.environ.get("BLOTATO_INSTAGRAM_ACCOUNT_ID", ""),
        higgsfield_api_key=os.environ.get("HIGGSFIELD_API_KEY", ""),
        higgsfield_api_secret=os.environ.get("HIGGSFIELD_API_SECRET", ""),
        higgsfield_model=os.environ.get("HIGGSFIELD_MODEL", "") or "higgsfield-ai/soul/standard",
        higgsfield_resolution=os.environ.get("HIGGSFIELD_RESOLUTION", "") or "1080p",
        higgsfield_video_model=os.environ.get("HIGGSFIELD_VIDEO_MODEL", "") or "higgsfield-ai/text2video/turbo",
        higgsfield_video_aspect=os.environ.get("HIGGSFIELD_VIDEO_ASPECT", "") or "9:16",
        higgsfield_video_duration=int(os.environ.get("HIGGSFIELD_VIDEO_DURATION", "") or "0"),
    )
