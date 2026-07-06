"""Tests del target de Blotato por plataforma (mediaType de reels/historias).

Facebook e Instagram aceptan target.mediaType "reel"/"story"; LinkedIn no tiene
mediaType. Se monkeypatchea `_request` para capturar el body sin tocar la red.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import blotato_client as bc


def _capture(monkeypatch):
    calls: list[dict] = []

    def fake_request(method, path, body=None, *, api_key=None, **kwargs):
        calls.append({"method": method, "path": path, "body": body})
        return {"postSubmissionId": "sub-1"}

    monkeypatch.setattr(bc, "_request", fake_request)
    return calls


def test_facebook_reel_sets_media_type(monkeypatch):
    calls = _capture(monkeypatch)
    bc.publish_post("acc", "facebook", "hola", ["https://m/x.mp4"],
                    api_key="k", page_id="page-9", media_type="reel")
    target = calls[0]["body"]["post"]["target"]
    assert target == {"targetType": "facebook", "pageId": "page-9", "mediaType": "reel"}
    # shareToFeed es de Instagram; no debe colarse en Facebook.
    assert "shareToFeed" not in calls[0]["body"]["post"]


def test_facebook_story_sets_media_type(monkeypatch):
    calls = _capture(monkeypatch)
    bc.publish_post("acc", "facebook", "hola", ["https://m/x.png"],
                    api_key="k", page_id="page-9", media_type="story")
    assert calls[0]["body"]["post"]["target"]["mediaType"] == "story"


def test_facebook_feed_post_has_no_media_type(monkeypatch):
    calls = _capture(monkeypatch)
    bc.publish_post("acc", "facebook", "hola", ["https://m/x.png"],
                    api_key="k", page_id="page-9")
    assert "mediaType" not in calls[0]["body"]["post"]["target"]


def test_instagram_reel_keeps_cover_and_share_to_feed(monkeypatch):
    calls = _capture(monkeypatch)
    bc.publish_post("acc", "instagram", "hola", ["https://m/x.mp4"],
                    api_key="k", media_type="reel", cover_image_url="https://m/c.png")
    post = calls[0]["body"]["post"]
    assert post["target"]["mediaType"] == "reel"
    assert post["target"]["coverImageUrl"] == "https://m/c.png"
    assert post["shareToFeed"] is True


def test_linkedin_never_gets_media_type(monkeypatch):
    calls = _capture(monkeypatch)
    bc.publish_post("acc", "linkedin", "hola", ["https://m/1.png", "https://m/2.png"],
                    api_key="k", media_type="reel")  # aunque se pase por error
    target = calls[0]["body"]["post"]["target"]
    assert "mediaType" not in target
    # Carrusel de LinkedIn = varias mediaUrls en el content.
    assert calls[0]["body"]["post"]["content"]["mediaUrls"] == ["https://m/1.png", "https://m/2.png"]
