"""
Blotato API client - repurpose-youtube-video skill helper.
Reads credentials from .env in the current working directory.

Discovered API quirks (from live usage):
  - source-resolutions-v3 body: {"source": {"sourceType": "youtube", "url": ...}}
  - videos/from-templates body: {"templateId": ..., "inputs": {"prompt": ..., "title": ...}}
  - videos/creations/:id returns {"item": {"status": ..., "imageUrls": [...], "mediaUrl": ...}}
  - Instagram rejects posts with more than 5 hashtags
  - Instagram carousel = multiple URLs in mediaUrls
  - Scheduled publishing: add "scheduledAt": "ISO-8601" to the post body

Media upload (used by image_overlay flow):
  - POST /v2/media       body: {"url": "<public_url>"}   -> {"url": "<blotato_hosted_url>"}
  - POST /v2/media/uploads  body: {"filename": "..."}   -> {"presignedUrl": "...", "publicUrl": "..."}
    then PUT presignedUrl with Content-Type: <mime> and the raw binary body.
    Rate-limited to 10 req/min per user. Files <= 200MB.
"""

import os, re, time, json, sys, urllib.request, urllib.error
from pathlib import Path

MAX_IG_HASHTAGS = 5
BASE_URL = "https://backend.blotato.com/v2"


# ── Config ─────────────────────────────────────────────────────────────────────

def _read_env() -> dict:
    env: dict = {}
    for candidate in [Path(".env"), Path(__file__).parent.parent / ".env"]:
        if candidate.exists():
            for line in candidate.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            break
    for k, v in os.environ.items():
        env.setdefault(k, v)
    return env


def load_config() -> dict:
    """Return all config values. Raises SystemExit if API key is missing."""
    env = _read_env()
    key = env.get("BLOTATO_API_KEY", "")
    if not key:
        raise SystemExit(
            "[WARNING] BLOTATO_API_KEY not found.\n"
            "    Create a .env file with: BLOTATO_API_KEY=your_key_here\n"
            "    Get your key at: https://my.blotato.com/settings/api"
        )
    return {
        "api_key": key,
        "linkedin_account_id": env.get("BLOTATO_LINKEDIN_ACCOUNT_ID", ""),
        "instagram_account_id": env.get("BLOTATO_INSTAGRAM_ACCOUNT_ID", ""),
    }


# ── HTTP ───────────────────────────────────────────────────────────────────────

def _request(method: str, path: str, body: dict | None = None, *, api_key: str) -> dict:
    url = path if path.startswith("http") else BASE_URL + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"blotato-api-key": api_key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode()
        if e.code == 429:
            print("   [rate-limit] Waiting 10s and retrying...")
            time.sleep(10)
            try:
                with urllib.request.urlopen(req) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as e2:
                body2 = e2.read().decode(errors="replace")
                raise RuntimeError(f"HTTP {e2.code} {method} {path}: {body2}") from e2
        raise RuntimeError(f"HTTP {e.code} {method} {path}: {body_text}") from e


def _unwrap(resp: dict) -> dict:
    """Normalize responses that wrap their payload in an 'item' key."""
    return resp.get("item", resp)


def _poll(
    endpoint: str,
    *,
    api_key: str,
    interval: int = 3,
    max_attempts: int = 60,
    done_statuses: tuple = ("done",),
    fail_statuses: tuple = ("failed", "error"),
) -> dict:
    for _ in range(max_attempts):
        data = _unwrap(_request("GET", endpoint, api_key=api_key))
        status = data.get("status", "unknown")
        if status in done_statuses:
            return data
        if status in fail_statuses:
            raise RuntimeError(f"Job failed: {data.get('message', data.get('error', status))}")
        time.sleep(interval)
    raise TimeoutError(f"Polling {endpoint} timed out after {max_attempts * interval}s")


# ── Media upload ───────────────────────────────────────────────────────────────

def upload_media_from_url(public_url: str, *, api_key: str) -> str:
    """
    Re-host a publicly accessible media URL on Blotato.
    Useful for taking a Pollinations-hosted URL and getting a Blotato-hosted one
    so the post is decoupled from Pollinations' CDN lifetime.
    Returns the new Blotato-hosted URL.
    """
    resp = _request("POST", "/media", {"url": public_url}, api_key=api_key)
    return resp.get("url") or _unwrap(resp).get("url", "")


def save_image(file_bytes: bytes, filename: str, *, output_dir: str = "outputs") -> str:
    """Save image bytes to a local file. Returns the absolute file path."""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    path = Path(output_dir) / filename
    path.write_bytes(file_bytes)
    return str(path.resolve())


def upload_media_local(file_bytes: bytes, filename: str, *, api_key: str, mime: str = "image/png") -> str:
    """
    Upload raw binary (e.g. a Pillow-rendered PNG) to Blotato via presigned URL.
    Two-step:
      1. POST /v2/media/uploads with {"filename": ...}  -> {presignedUrl, publicUrl}
      2. PUT presignedUrl with the binary body and Content-Type: <mime>
    Returns the publicUrl (usable in mediaUrls of a post).
    The presigned URL expires quickly, so the PUT happens immediately after step 1.
    """
    resp = _request("POST", "/media/uploads", {"filename": filename}, api_key=api_key)
    presigned = resp.get("presignedUrl") or _unwrap(resp).get("presignedUrl")
    public = resp.get("publicUrl") or _unwrap(resp).get("publicUrl")
    if not presigned or not public:
        raise RuntimeError(f"Blotato presigned upload: missing URLs in response: {resp}")

    put_req = urllib.request.Request(
        presigned, data=file_bytes, method="PUT",
        headers={"Content-Type": mime},
    )
    try:
        with urllib.request.urlopen(put_req) as r:
            r.read()
    except urllib.error.HTTPError as e:
        body_text = e.read().decode(errors="replace")
        raise RuntimeError(f"Blotato presigned PUT failed ({e.code}): {body_text}") from e
    return public


# ── API wrappers ───────────────────────────────────────────────────────────────

def extract_youtube(url: str, *, api_key: str) -> dict:
    """Start YouTube extraction and poll until done. Returns resolved content dict."""
    resp = _request(
        "POST", "/source-resolutions-v3",
        {"source": {"sourceType": "youtube", "url": url}},
        api_key=api_key,
    )
    return _poll(f"/source-resolutions-v3/{resp['id']}", api_key=api_key, interval=3)


def extract_youtube_local(url: str) -> dict:
    """
    Extract YouTube video info using yt-dlp (metadata) + youtube-transcript-api (transcript).
    No API key required. Falls back gracefully if transcript is unavailable.
    Returns dict with: title, description, transcript, summary, keyPoints, tags, chapters.
    """
    try:
        import yt_dlp as _ytdlp
    except ImportError:
        raise RuntimeError(
            "[ERROR] yt-dlp no está instalado. Ejecuta: python -m pip install yt-dlp"
        )

    # Metadata via yt-dlp Python API (no PATH dependency)
    ydl_opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    with _ytdlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    video_id = info.get("id", "")
    title = info.get("title", "")
    description = (info.get("description") or "")[:3000]
    tags = info.get("tags") or []
    chapters = [c["title"] for c in (info.get("chapters") or []) if isinstance(c, dict) and "title" in c]
    channel = info.get("channel") or info.get("uploader") or ""

    # Transcript via youtube-transcript-api
    transcript_text = ""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi, NoTranscriptFound, TranscriptsDisabled
        ytt = YouTubeTranscriptApi()
        try:
            fetched = ytt.fetch(video_id, languages=["es", "es-419", "es-ES", "en", "en-US"])
        except (NoTranscriptFound, Exception):
            # Try any available language
            tl = ytt.list(video_id)
            fetched = next(iter(tl)).fetch()
        transcript_text = " ".join(s.text for s in fetched)
    except Exception:
        transcript_text = ""

    return {
        "title": title,
        "description": description,
        "transcript": transcript_text,
        "summary": "",
        "keyPoints": [],
        "tags": tags,
        "chapters": chapters,
        "channel": channel,
    }


def get_accounts(platform: str, *, api_key: str) -> list[dict]:
    resp = _request("GET", f"/users/me/accounts?platform={platform}", api_key=api_key)
    return resp.get("items", [])


def get_templates(*, api_key: str) -> list[dict]:
    resp = _request("GET", "/videos/templates?fields=id,name,description", api_key=api_key)
    return resp.get("items", [])


def create_visual(template_id: str, prompt: str, title: str, *, api_key: str) -> dict:
    """
    Create a visual from a template and poll until done.
    Returns dict with 'imageUrls' (list) and/or 'mediaUrl'.
    """
    resp = _request(
        "POST", "/videos/from-templates",
        {"templateId": template_id, "inputs": {"prompt": prompt, "title": title}},
        api_key=api_key,
    )
    creation_id = _unwrap(resp).get("id", resp.get("id"))
    return _poll(f"/videos/creations/{creation_id}", api_key=api_key, interval=5, max_attempts=72)


def enforce_ig_hashtags(text: str) -> str:
    """Remove hashtags beyond MAX_IG_HASHTAGS (Instagram hard limit = 5)."""
    hashtags = re.findall(r'#\w+', text)
    if len(hashtags) <= MAX_IG_HASHTAGS:
        return text
    for tag in hashtags[MAX_IG_HASHTAGS:]:
        text = text.replace(" " + tag, "", 1).replace(tag, "", 1)
    return text.strip()


def publish_post(
    account_id: str,
    platform: str,
    text: str,
    media_urls: list[str],
    *,
    api_key: str,
    schedule_time: str | None = None,
    share_to_feed: bool = True,
) -> dict:
    """
    Publish or schedule a post.

    media_urls: one URL = single image/video; multiple URLs = carousel (Instagram).
    schedule_time: ISO-8601 string for deferred publishing (e.g. "2026-05-14T18:00:00Z"),
                   or None to publish immediately.
    Returns Blotato response with 'postSubmissionId'.
    """
    if platform == "instagram":
        text = enforce_ig_hashtags(text)

    content: dict = {"text": text, "platform": platform, "mediaUrls": media_urls}

    post_body: dict = {
        "accountId": account_id,
        "content": content,
        "target": {"targetType": platform},
    }
    if platform == "instagram" and share_to_feed:
        post_body["shareToFeed"] = True
    if schedule_time:
        post_body["scheduledAt"] = schedule_time

    return _request("POST", "/posts", {"post": post_body}, api_key=api_key)


def poll_post_status(submission_id: str, *, api_key: str, timeout: int = 120) -> dict:
    return _poll(
        f"/posts/{submission_id}",
        api_key=api_key,
        interval=3,
        max_attempts=timeout // 3,
        done_statuses=("published", "scheduled"),
        fail_statuses=("failed", "error"),
    )


# ── CLI (quick extraction test) ────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python blotato_client.py <youtube_url>")
        sys.exit(1)
    result = extract_youtube_local(sys.argv[1])
    print(json.dumps(result, indent=2))
