import json
import random
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import plugins.reddit_rule34_hot.reddit_rule34_hot as reddit_mod  # noqa: E402
from plugins.reddit_rule34_hot.reddit_rule34_hot import RedditRule34Hot  # noqa: E402


TEST_TMP_ROOT = Path(__file__).resolve().parents[4] / ".tmp" / "reddit_rule34_hot_tests"


class DummyDeviceConfig:
    def __init__(self, resolution=(800, 480), secrets=None):
        self.resolution = resolution
        self.secrets = {
            "REDDIT_CLIENT_ID": "client-id",
            "REDDIT_CLIENT_SECRET": "client-secret",
            "REDDIT_REFRESH_TOKEN": "refresh-token",
        }
        if secrets is not None:
            self.secrets.update(secrets)

    def get_resolution(self):
        return self.resolution

    def get_config(self, _key, default=None):
        return default

    def load_env_key(self, key):
        return self.secrets.get(key, "")


class RecordingLoader:
    def __init__(self):
        self.paths = []

    def from_file(self, path, dimensions, resize=True, focus_crop=False):
        self.paths.append(Path(path))
        with Image.open(path) as image:
            return image.copy().convert("RGB")


class FakeResponse:
    def __init__(self, json_data=None, chunks=None):
        self._json = json_data
        self._chunks = chunks or []

    def raise_for_status(self):
        return None

    def json(self):
        return self._json

    def iter_content(self, chunk_size=8192):
        yield from self._chunks


class FakeSession:
    def __init__(self, post_responses=None, get_responses=None):
        self.post_responses = list(post_responses or [])
        self.get_responses = list(get_responses or [])
        self.post_calls = []
        self.get_calls = []

    def post(self, url, data=None, auth=None, headers=None, timeout=None, **kwargs):
        self.post_calls.append({
            "url": url,
            "data": data or {},
            "auth": auth,
            "headers": headers or {},
        })
        return self.post_responses.pop(0)

    def get(self, url, params=None, headers=None, timeout=None, stream=False, **kwargs):
        self.get_calls.append({
            "url": url,
            "params": params or {},
            "headers": headers or {},
            "stream": stream,
        })
        return self.get_responses.pop(0)


def make_test_tmp_dir(name):
    path = TEST_TMP_ROOT / f"{name}-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def make_post(
    post_id,
    *,
    subreddit="RuleArt",
    title="adult image",
    score=10,
    over_18=True,
    url=None,
    width=1200,
    height=1800,
    stickied=False,
    spoiler=False,
    is_video=False,
    post_hint="image",
    removed_by_category=None,
):
    url = url or f"https://i.redd.it/{post_id}.jpg"
    return {
        "id": post_id,
        "subreddit": subreddit,
        "title": title,
        "score": score,
        "num_comments": 3,
        "over_18": over_18,
        "url": url,
        "url_overridden_by_dest": url,
        "permalink": f"/r/{subreddit}/comments/{post_id}/title/",
        "author": "artist",
        "stickied": stickied,
        "spoiler": spoiler,
        "is_video": is_video,
        "post_hint": post_hint,
        "removed_by_category": removed_by_category,
        "preview": {
            "images": [
                {
                    "source": {"url": url, "width": width, "height": height},
                    "resolutions": [{"url": url, "width": width // 2, "height": height // 2}],
                }
            ]
        },
    }


def hot_payload(posts):
    return {"data": {"children": [{"data": post} for post in posts]}}


def test_fetch_hot_posts_uses_refresh_token_oauth(monkeypatch):
    session = FakeSession(
        post_responses=[FakeResponse({"access_token": "access-123", "expires_in": 3600})],
        get_responses=[FakeResponse(hot_payload([make_post("abc")]))],
    )
    monkeypatch.setattr(reddit_mod, "get_http_session", lambda: session)

    plugin = RedditRule34Hot({"id": "reddit_rule34_hot"})
    posts = plugin._fetch_hot_posts("RuleArt", DummyDeviceConfig())

    assert posts[0]["id"] == "abc"
    assert session.post_calls[0]["url"] == "https://www.reddit.com/api/v1/access_token"
    assert session.post_calls[0]["data"] == {
        "grant_type": "refresh_token",
        "refresh_token": "refresh-token",
    }
    assert session.post_calls[0]["auth"] == ("client-id", "client-secret")
    assert session.get_calls[0]["url"] == "https://oauth.reddit.com/r/RuleArt/hot"
    assert session.get_calls[0]["params"]["limit"] == 100
    assert session.get_calls[0]["params"]["raw_json"] == 1
    assert session.get_calls[0]["headers"]["Authorization"] == "bearer access-123"


def test_subreddit_settings_parse_and_dedupe():
    plugin = RedditRule34Hot({"id": "reddit_rule34_hot"})

    assert plugin._subreddits({
        "subreddits": " r/RuleArt, OtherArt\nhttps://www.reddit.com/r/RuleArt/ bad-name!"
    }) == ["RuleArt", "OtherArt"]


def test_post_filter_requires_adult_static_safe_images():
    plugin = RedditRule34Hot({"id": "reddit_rule34_hot"})

    assert plugin._is_usable_post(make_post("ok")) is True
    assert plugin._is_usable_post(make_post("sfw", over_18=False)) is False
    assert plugin._is_usable_post(make_post("sticky", stickied=True)) is False
    assert plugin._is_usable_post(make_post("spoiler", spoiler=True)) is False
    assert plugin._is_usable_post(make_post("video", is_video=True, url="https://v.redd.it/video")) is False
    assert plugin._is_usable_post(make_post("gif", url="https://i.redd.it/anim.gif")) is False
    assert plugin._is_usable_post(make_post("risk", title="loli image")) is False


def test_reddit_preview_image_is_used_when_direct_url_is_not_static():
    plugin = RedditRule34Hot({"id": "reddit_rule34_hot"})
    post = make_post("preview", url="https://www.reddit.com/gallery/preview")
    preview_url = "https://preview.redd.it/preview.jpg?width=960&amp;format=pjpg"
    post["preview"]["images"][0]["source"] = {
        "url": preview_url,
        "width": 960,
        "height": 1280,
    }

    url, width, height = plugin._post_image(post)

    assert url == "https://preview.redd.it/preview.jpg?width=960&format=pjpg"
    assert (width, height) == (960, 1280)


def test_daily_pool_cache_hit_does_not_call_reddit(monkeypatch):
    cache_dir = make_test_tmp_dir("cache-hit")
    image_path = cache_dir / "cached.jpg"
    Image.new("RGB", (300, 200), "blue").save(image_path)

    plugin = RedditRule34Hot({"id": "reddit_rule34_hot"})
    plugin.image_loader = RecordingLoader()
    monkeypatch.setenv("INKYPI_REDDIT_RULE34_CACHE", str(cache_dir))
    monkeypatch.setattr(plugin, "_day_key", lambda: "2026-06-22")
    monkeypatch.setattr(plugin, "_now_utc", lambda: datetime(2026, 6, 22, 16, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(plugin, "_fetch_hot_posts", lambda *_args: (_ for _ in ()).throw(AssertionError("Reddit called")))

    settings = {"subreddits": "RuleArt", "poolSize": "20"}
    plugin._write_state({
        "state_version": "reddit-rule34-hot-v1",
        "day_key": "2026-06-22",
        "subreddits": ["RuleArt"],
        "pool_size": 20,
    })
    plugin._write_daily_pool([
        {
            "illust_id": "abc",
            "post_id": "abc",
            "rank": 1,
            "title": "Cached",
            "artist": "r/RuleArt",
            "width": 1200,
            "height": 800,
            "image_path": str(image_path),
        }
    ])

    image = plugin.generate_image(settings, DummyDeviceConfig())

    assert image.size == (800, 480)
    assert plugin.image_loader.paths == [image_path]


def test_daily_pool_refresh_filters_sorts_and_limits(monkeypatch):
    cache_dir = make_test_tmp_dir("refresh")

    plugin = RedditRule34Hot({"id": "reddit_rule34_hot"})
    plugin.image_loader = RecordingLoader()
    monkeypatch.setenv("INKYPI_REDDIT_RULE34_CACHE", str(cache_dir))
    monkeypatch.setattr(plugin, "_day_key", lambda: "2026-06-22")
    monkeypatch.setattr(plugin, "_now_utc", lambda: datetime(2026, 6, 22, 16, 0, tzinfo=timezone.utc))

    def fake_download(_url):
        source = cache_dir / f"source-{uuid.uuid4().hex}.jpg"
        Image.new("RGB", (300, 500), "purple").save(source)
        return source

    monkeypatch.setattr(plugin, "_download_to_temp", fake_download)

    posts_by_subreddit = {
        "RuleArt": [
            make_post("low", score=10),
            make_post("sfw", score=999, over_18=False),
            make_post("risk", score=998, title="guro image"),
        ],
        "OtherArt": [
            make_post("high", subreddit="OtherArt", score=50),
            make_post("mid", subreddit="OtherArt", score=30),
        ],
    }
    monkeypatch.setattr(plugin, "_fetch_hot_posts", lambda subreddit, _cfg: posts_by_subreddit[subreddit])

    pool = plugin._refresh_daily_pool(
        {"subreddits": "RuleArt, OtherArt", "poolSize": "2"},
        DummyDeviceConfig(),
        (800, 480),
    )

    assert [item["post_id"] for item in pool] == ["high", "mid"]
    assert [item["rank"] for item in pool] == [1, 2]
    assert all(Path(item["image_path"]).is_file() for item in pool)


def test_generate_image_renders_triptych_for_portrait_pool(monkeypatch):
    cache_dir = make_test_tmp_dir("triptych")
    paths = []
    for idx, color in enumerate(["red", "green", "blue", "purple"], start=1):
        path = cache_dir / f"p{idx}.jpg"
        Image.new("RGB", (300, 500), color).save(path)
        paths.append(path)

    plugin = RedditRule34Hot({"id": "reddit_rule34_hot"})
    plugin.image_loader = RecordingLoader()
    monkeypatch.setenv("INKYPI_REDDIT_RULE34_CACHE", str(cache_dir))
    monkeypatch.setattr(plugin, "_day_key", lambda: "2026-06-22")
    monkeypatch.setattr(plugin, "_now_utc", lambda: datetime(2026, 6, 22, 16, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(random, "shuffle", lambda values: None)
    monkeypatch.setattr(plugin, "_fetch_hot_posts", lambda *_args: (_ for _ in ()).throw(AssertionError("no fetch")))

    plugin._write_state({
        "state_version": "reddit-rule34-hot-v1",
        "day_key": "2026-06-22",
        "subreddits": ["RuleArt"],
        "pool_size": 20,
    })
    plugin._write_daily_pool([
        {
            "illust_id": f"p{idx}",
            "post_id": f"p{idx}",
            "rank": idx,
            "title": f"Post {idx}",
            "artist": "r/RuleArt",
            "width": 800,
            "height": 1200,
            "image_path": str(path),
        }
        for idx, path in enumerate(paths, start=1)
    ])

    image = plugin.generate_image(
        {"subreddits": "RuleArt", "poolSize": "20", "fitMode": "auto_layout"},
        DummyDeviceConfig(),
    )

    assert image.size == (800, 480)
    assert len(plugin.image_loader.paths) == 3


def test_empty_subreddits_render_fallback_and_cache_empty_pool(monkeypatch):
    cache_dir = make_test_tmp_dir("empty")
    plugin = RedditRule34Hot({"id": "reddit_rule34_hot"})
    monkeypatch.setenv("INKYPI_REDDIT_RULE34_CACHE", str(cache_dir))
    monkeypatch.setattr(plugin, "_day_key", lambda: "2026-06-22")
    monkeypatch.setattr(plugin, "_now_utc", lambda: datetime(2026, 6, 22, 16, 0, tzinfo=timezone.utc))

    image = plugin.generate_image({"subreddits": "", "poolSize": "20"}, DummyDeviceConfig())
    payload = json.loads(plugin._daily_pool_path().read_text(encoding="utf-8"))

    assert image.size == (800, 480)
    assert payload["items"] == []
