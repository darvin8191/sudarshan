"""M18.1: the feed's /health says which release is installed, beside its own version.

On 27 Oct 2026 the feed's ``0.1.0`` was read as the release. ``version`` is the version of this
service's code and never moves with a release; ``release`` is the tag the deploy tool recorded in
``deployed_tag`` once an install passed its health check. Best effort: no file, or one holding
anything but a tag, is None and never an error.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import feed_fakes as ff
import httpx

from sudarshan.feed import __version__
from sudarshan.feed.config import Settings
from sudarshan.feed.service import FeedService
from sudarshan.ops.release import DEPLOYED_TAG_FILE


def _service(feed_env, fake=None, **overrides) -> tuple[FeedService, Path]:
    env = feed_env(fake, **overrides)
    return FeedService(Settings.from_env(env)), Path(env["SUDARSHAN_DEPLOYED_TAG_FILE"])


def test_health_names_the_release_and_keeps_the_services_own_version(feed_env):
    async def scenario():
        svc, tag_file = _service(feed_env)
        tag_file.write_text("v0.3.3\n")
        health = svc.health()
        assert health["release"] == "v0.3.3"
        assert health["version"] == __version__ != health["release"]
        keys = list(health)
        assert keys.index("release") == keys.index("version") - 1  # beside it, in the document

    asyncio.run(scenario())


def test_health_has_no_release_when_the_file_cannot_tell(feed_env, tmp_path):
    async def scenario():
        svc, tag_file = _service(feed_env)
        assert svc.health()["release"] is None  # a server the tool never deployed to
        for text in ("", "none\n", "v0.3\n", "v0.3.3-rc1\n", "v0.3.3\nv0.3.4\n", "\x00\xff"):
            tag_file.write_bytes(text.encode("latin-1"))
            assert svc.health()["release"] is None, text
        tag_file.unlink()
        tag_file.mkdir()  # a folder where the file should be
        assert svc.health()["release"] is None
        # Everything else in the document is still there.
        assert svc.health()["service"] == "sudarshan-feed" and svc.health()["state"]

    asyncio.run(scenario())


def test_health_does_not_follow_a_link_or_wait_on_a_pipe(feed_env, tmp_path):
    async def scenario():
        svc, tag_file = _service(feed_env)
        real = tmp_path / "elsewhere"
        real.write_text("v9.9.9\n")
        tag_file.symlink_to(real)
        assert svc.health()["release"] is None
        tag_file.unlink()
        os.mkfifo(tag_file)
        assert svc.health()["release"] is None  # returns at once, no writer needed

    asyncio.run(scenario())


def test_a_new_release_shows_without_a_restart(feed_env):
    async def scenario():
        svc, tag_file = _service(feed_env)
        tag_file.write_text("v0.3.2\n")
        assert svc.health()["release"] == "v0.3.2"
        tag_file.write_text("v0.3.3\n")  # the deploy tool installed while the service ran
        assert svc.health()["release"] == "v0.3.3"

    asyncio.run(scenario())


def test_the_release_is_on_the_http_page_and_on_the_bus(feed_env):
    async def scenario():
        async with ff.FakeUpstox() as fake:
            env = feed_env(fake)
            Path(env["SUDARSHAN_DEPLOYED_TAG_FILE"]).write_text("v0.3.3\n")
            svc = FeedService(Settings.from_env(env))
            run = asyncio.create_task(svc.run())
            try:
                await ff.wait_until(lambda: svc.http is not None)
                async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{svc.http_port}") as http:
                    page = (await http.get("/health")).json()
                assert page["release"] == "v0.3.3" and page["version"] == __version__
                # The same document is what the bus publishes every 5 s and on a state change.
                sent = []
                svc.pub.send = lambda topic, payload: sent.append((topic, json.loads(payload)))
                svc._publish_health()
                assert sent and sent[0][1]["release"] == "v0.3.3"
            finally:
                svc.stop("test")
                await asyncio.wait_for(run, 10)

    asyncio.run(scenario())


def test_the_setting_defaults_to_the_deploy_tools_file_and_can_move(tmp_path):
    base = {"SUDARSHAN_FEED_STATE_DIR": str(tmp_path)}
    assert Settings.from_env(base).deployed_tag_file == DEPLOYED_TAG_FILE
    assert str(DEPLOYED_TAG_FILE) == "/var/lib/sudarshan/deployed_tag"
    moved = Settings.from_env(base | {"SUDARSHAN_DEPLOYED_TAG_FILE": str(tmp_path / "tag")})
    assert moved.deployed_tag_file == tmp_path / "tag"
    blank = Settings.from_env(base | {"SUDARSHAN_DEPLOYED_TAG_FILE": ""})
    assert blank.deployed_tag_file == DEPLOYED_TAG_FILE
