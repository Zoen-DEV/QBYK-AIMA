"""
Higgsfield Cloud client - text-to-image generation via the Soul model.

Official API (https://platform.higgsfield.ai), no SDK — pure urllib.

Auth:
  Header  ->  Authorization: Key {API_KEY}:{API_SECRET}

Flow (asynchronous):
  1. POST /{model}                         submit a generation
       body  -> {"prompt", "aspect_ratio", "resolution"}
       resp  -> {"status": "queued", "request_id": "...", "status_url": "..."}
  2. GET  {status_url}  (poll)             until status is terminal
       status in {queued, in_progress}     keep polling
       status == completed                 -> {"images": [{"url": ...}]}
       status in {failed, nsfw, cancelled} -> raise

Soul accepts aspect_ratio in {16:9, 3:2, 4:3, 1:1, 3:4, 2:3, 9:16} and
resolution in {480p, 720p, 1080p}. We always request "1:1"/"1080p" for the
shared square base; LinkedIn's 4:5 crop is handled later by image_overlay.

Unlike Pollinations, Higgsfield returns a hosted image URL only once the job
completes, so callers must submit() then poll() (or use generate_image() which
does both and blocks).
"""

import json
import time
import urllib.error
import urllib.request

BASE = "https://platform.higgsfield.ai"
DEFAULT_MODEL = "higgsfield-ai/soul/standard"
DEFAULT_RESOLUTION = "1080p"

# Text-to-video. The platform routes by model path (POST /{model}), so the slug
# is configurable via env (HIGGSFIELD_VIDEO_MODEL) to track Higgsfield's catalog.
DEFAULT_VIDEO_MODEL = "higgsfield-ai/text2video/turbo"
DEFAULT_VIDEO_ASPECT = "9:16"

_SUBMIT_TIMEOUT = 30      # seconds for the submit request
_STATUS_TIMEOUT = 30      # seconds for a single status request
_POLL_INTERVAL = 2.0      # seconds between status polls
_POLL_DEADLINE = 180      # max seconds to wait for one image to complete
_VIDEO_POLL_INTERVAL = 5.0   # video renders are slower; poll less aggressively
_VIDEO_POLL_DEADLINE = 600   # max seconds to wait for one video to complete
_REQUEST_RETRIES = 3      # retries on transient network/429/5xx errors
_RETRY_BACKOFF = [0, 5, 10]

_PENDING_STATUSES = {"queued", "in_progress", "in_queue", "processing", ""}
_TERMINAL_FAIL = {"failed", "nsfw", "cancelled", "canceled"}

# Cloudflare in front of platform.higgsfield.ai bans urllib's default UA
# (Python-urllib/x.y) with error 1010 (browser_signature_banned). A browser
# User-Agent is required to reach the origin.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _auth_header(api_key: str, api_secret: str) -> str:
    return f"Key {api_key}:{api_secret}"


def _request(method: str, url: str, *, api_key: str, api_secret: str,
             body: dict | None = None, timeout: int = _STATUS_TIMEOUT) -> dict:
    """Perform an authenticated JSON request, retrying transient failures.

    4xx other than 429 are treated as permanent (raise immediately); 429 and
    5xx and network errors are retried with backoff.
    """
    headers = {
        "Authorization": _auth_header(api_key, api_secret),
        "Accept": "application/json",
        "User-Agent": _USER_AGENT,
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    last_exc: Exception | None = None
    for attempt in range(_REQUEST_RETRIES):
        wait = _RETRY_BACKOFF[min(attempt, len(_RETRY_BACKOFF) - 1)]
        if wait > 0:
            time.sleep(wait)
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:500]
            if e.code != 429 and e.code < 500:
                raise RuntimeError(f"Higgsfield HTTP {e.code}: {detail}") from e
            last_exc = RuntimeError(f"Higgsfield HTTP {e.code}: {detail}")
        except Exception as e:  # URLError, timeout, JSON decode
            last_exc = e
    raise RuntimeError(f"Higgsfield request failed after {_REQUEST_RETRIES} attempts: {last_exc}")


def submit_image(
    prompt: str,
    *,
    api_key: str,
    api_secret: str,
    aspect_ratio: str = "1:1",
    resolution: str = DEFAULT_RESOLUTION,
    model: str = DEFAULT_MODEL,
) -> dict:
    """Submit a text-to-image generation. Returns a handle to poll later.

    The returned handle carries the prompt so the caller can regenerate via a
    fallback provider if polling ends in failure/nsfw.
    """
    body = {
        "prompt": prompt[:2000],
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
    }
    resp = _request("POST", f"{BASE}/{model}", api_key=api_key, api_secret=api_secret,
                    body=body, timeout=_SUBMIT_TIMEOUT)
    request_id = resp.get("request_id") or resp.get("id")
    status_url = resp.get("status_url")
    if not status_url:
        if not request_id:
            raise RuntimeError(f"Higgsfield submit returned no request_id/status_url: {resp}")
        status_url = f"{BASE}/requests/{request_id}/status"
    return {"request_id": request_id, "status_url": status_url, "prompt": prompt}


def poll_image(handle: dict, *, api_key: str, api_secret: str, deadline: int = _POLL_DEADLINE) -> str:
    """Poll a submitted generation until it completes; return the image URL.

    Raises RuntimeError on failed/nsfw/cancelled status or on timeout.
    """
    status_url = handle["status_url"]
    started = time.time()
    last_status = ""
    while True:
        resp = _request("GET", status_url, api_key=api_key, api_secret=api_secret, timeout=_STATUS_TIMEOUT)
        status = (resp.get("status") or "").lower()
        last_status = status or last_status
        if status == "completed":
            images = resp.get("images") or []
            if images and images[0].get("url"):
                return images[0]["url"]
            raise RuntimeError("Higgsfield completed but no image URL in response")
        if status in _TERMINAL_FAIL:
            raise RuntimeError(f"Higgsfield generation status={status}")
        if status not in _PENDING_STATUSES:
            # Unknown status — keep polling but surface it if we time out.
            pass
        if time.time() - started > deadline:
            raise RuntimeError(f"Higgsfield poll timed out after {deadline}s (last status={last_status or 'unknown'})")
        time.sleep(_POLL_INTERVAL)


def generate_image(
    prompt: str,
    *,
    api_key: str,
    api_secret: str,
    aspect_ratio: str = "1:1",
    resolution: str = DEFAULT_RESOLUTION,
    model: str = DEFAULT_MODEL,
) -> list[str]:
    """Submit and block until the image is ready. Returns [url].

    Mirrors pollinations_client.generate_image's return shape so callers can
    treat both providers uniformly.
    """
    print(f"   [...] Generando imagen con Higgsfield Soul ({aspect_ratio}, {resolution})...")
    handle = submit_image(prompt, api_key=api_key, api_secret=api_secret,
                          aspect_ratio=aspect_ratio, resolution=resolution, model=model)
    url = poll_image(handle, api_key=api_key, api_secret=api_secret)
    print(f"   [ok] Imagen lista.")
    return [url]


# ── Video (text-to-video) ────────────────────────────────────────────────────

def _extract_media_url(resp: dict) -> str:
    """Pull a media URL out of a completed response, tolerating shape variance.

    Higgsfield's video models have returned the asset under a few different keys
    across versions ({"video": {"url"}}, {"videos": [{"url"}]}, {"url"}, ...),
    so we probe the known shapes instead of assuming one.
    """
    video = resp.get("video")
    if isinstance(video, dict) and video.get("url"):
        return video["url"]
    if isinstance(video, str) and video:
        return video
    for key in ("videos", "images", "results", "outputs"):
        items = resp.get(key)
        if isinstance(items, list) and items:
            first = items[0]
            if isinstance(first, dict) and first.get("url"):
                return first["url"]
            if isinstance(first, str) and first:
                return first
    if resp.get("url"):
        return resp["url"]
    return ""


def submit_video(
    prompt: str,
    *,
    api_key: str,
    api_secret: str,
    aspect_ratio: str = DEFAULT_VIDEO_ASPECT,
    duration: int | None = None,
    model: str = DEFAULT_VIDEO_MODEL,
) -> dict:
    """Submit a text-to-video generation. Returns a handle to poll later.

    Body stays minimal (prompt + aspect_ratio, plus duration only when given) so
    we don't trip a 422 on a parameter a given video model doesn't accept.
    """
    body: dict = {
        "prompt": prompt[:2000],
        "aspect_ratio": aspect_ratio,
    }
    if duration:
        body["duration"] = duration
    resp = _request("POST", f"{BASE}/{model}", api_key=api_key, api_secret=api_secret,
                    body=body, timeout=_SUBMIT_TIMEOUT)
    request_id = resp.get("request_id") or resp.get("id")
    status_url = resp.get("status_url")
    if not status_url:
        if not request_id:
            raise RuntimeError(f"Higgsfield video submit returned no request_id/status_url: {resp}")
        status_url = f"{BASE}/requests/{request_id}/status"
    return {"request_id": request_id, "status_url": status_url, "prompt": prompt}


def poll_video(handle: dict, *, api_key: str, api_secret: str, deadline: int = _VIDEO_POLL_DEADLINE) -> str:
    """Poll a submitted video generation until it completes; return the video URL.

    Raises RuntimeError on failed/nsfw/cancelled status or on timeout.
    """
    status_url = handle["status_url"]
    started = time.time()
    last_status = ""
    while True:
        resp = _request("GET", status_url, api_key=api_key, api_secret=api_secret, timeout=_STATUS_TIMEOUT)
        status = (resp.get("status") or "").lower()
        last_status = status or last_status
        if status == "completed":
            url = _extract_media_url(resp)
            if url:
                return url
            raise RuntimeError("Higgsfield video completed but no video URL in response")
        if status in _TERMINAL_FAIL:
            raise RuntimeError(f"Higgsfield video generation status={status}")
        if time.time() - started > deadline:
            raise RuntimeError(f"Higgsfield video poll timed out after {deadline}s (last status={last_status or 'unknown'})")
        time.sleep(_VIDEO_POLL_INTERVAL)


def generate_video(
    prompt: str,
    *,
    api_key: str,
    api_secret: str,
    aspect_ratio: str = DEFAULT_VIDEO_ASPECT,
    duration: int | None = None,
    model: str = DEFAULT_VIDEO_MODEL,
) -> str:
    """Submit and block until the video is ready. Returns the video URL."""
    print(f"   [...] Generando video con Higgsfield ({model}, {aspect_ratio})...")
    handle = submit_video(prompt, api_key=api_key, api_secret=api_secret,
                          aspect_ratio=aspect_ratio, duration=duration, model=model)
    url = poll_video(handle, api_key=api_key, api_secret=api_secret)
    print(f"   [ok] Video listo.")
    return url
