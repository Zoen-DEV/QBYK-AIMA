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
    facebook_account_id: str = ""
    higgsfield_api_key: str = ""
    higgsfield_api_secret: str = ""
    higgsfield_model: str = "higgsfield-ai/soul/standard"
    higgsfield_resolution: str = "1080p"
    higgsfield_video_model: str = "higgsfield-ai/text2video/turbo"
    higgsfield_video_aspect: str = "9:16"
    higgsfield_video_duration: int = 0  # 0 = don't send duration (let model default)
    # Speech-to-text (for the "audio" source). Two engines:
    #   "api"   — OpenAI-compatible Whisper endpoint (OpenAI default, or Groq).
    #   "local" — faster-whisper running on this machine (free, offline, no key).
    transcription_engine: str = "api"
    transcription_api_key: str = ""
    transcription_base_url: str = "https://api.openai.com/v1"
    transcription_model: str = "whisper-1"
    # Local (faster-whisper) settings — only used when engine == "local".
    transcription_local_model: str = "base"
    transcription_local_device: str = "cpu"
    transcription_local_compute: str = "int8"

    @property
    def transcription_available(self) -> bool:
        """Local engine needs no key; the API engine needs a Whisper key."""
        if self.transcription_engine == "local":
            return True
        return bool(self.transcription_api_key)

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
        """'higgsfield' if both key and secret are set, else 'template' (local fallback)."""
        if self.higgsfield_api_key and self.higgsfield_api_secret:
            return "higgsfield"
        return "template"

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
        facebook_account_id=os.environ.get("BLOTATO_FACEBOOK_ACCOUNT_ID", ""),
        higgsfield_api_key=os.environ.get("HIGGSFIELD_API_KEY", ""),
        higgsfield_api_secret=os.environ.get("HIGGSFIELD_API_SECRET", ""),
        higgsfield_model=os.environ.get("HIGGSFIELD_MODEL", "") or "higgsfield-ai/soul/standard",
        higgsfield_resolution=os.environ.get("HIGGSFIELD_RESOLUTION", "") or "1080p",
        higgsfield_video_model=os.environ.get("HIGGSFIELD_VIDEO_MODEL", "") or "higgsfield-ai/text2video/turbo",
        higgsfield_video_aspect=os.environ.get("HIGGSFIELD_VIDEO_ASPECT", "") or "9:16",
        higgsfield_video_duration=int(os.environ.get("HIGGSFIELD_VIDEO_DURATION", "") or "0"),
        # "local" uses faster-whisper offline; anything else (default) uses the
        # hosted OpenAI-compatible endpoint below.
        transcription_engine=(os.environ.get("TRANSCRIPTION_ENGINE", "") or "api").lower(),
        # Prefer OPENAI_API_KEY; fall back to GROQ_API_KEY (Groq exposes an
        # OpenAI-compatible Whisper endpoint — set TRANSCRIPTION_BASE_URL too).
        transcription_api_key=os.environ.get("OPENAI_API_KEY", "") or os.environ.get("GROQ_API_KEY", ""),
        transcription_base_url=os.environ.get("TRANSCRIPTION_BASE_URL", "") or "https://api.openai.com/v1",
        transcription_model=os.environ.get("TRANSCRIPTION_MODEL", "") or "whisper-1",
        transcription_local_model=os.environ.get("TRANSCRIPTION_LOCAL_MODEL", "") or "base",
        transcription_local_device=os.environ.get("TRANSCRIPTION_LOCAL_DEVICE", "") or "cpu",
        transcription_local_compute=os.environ.get("TRANSCRIPTION_LOCAL_COMPUTE", "") or "int8",
    )
