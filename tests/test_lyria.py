import asyncio

from dj.lyria import LyriaDJ


class FakeSession:
    def __init__(self) -> None:
        self.sent: list[dict[str, float]] = []  # positive prompts only
        self.raw: list[dict[str, float]] = []  # including negative "avoid" prompts

    async def set_weighted_prompts(self, prompts) -> None:
        self.raw.append({p.text: p.weight for p in prompts})
        self.sent.append({p.text: p.weight for p in prompts if p.weight > 0})


def _dj() -> LyriaDJ:
    dj = LyriaDJ({"deep house": 1.0}, api_key="unused")
    dj._session = FakeSession()
    return dj


async def test_rejected_tag_dropped_once_and_not_resent():
    dj = _dj()
    calls = []
    dj.on_filtered = lambda text, reason: calls.append(text)
    await dj.set_prompts({"hard rock": 1.0, "steady drums": 0.8}, crossfade_s=0)

    dj._handle_filtered("steady drums", "nope")
    dj._handle_filtered("steady drums", "nope")  # Lyria repeats per crossfade step

    assert calls == ["steady drums"]
    assert dj.prompts == {"hard rock": 1.0}
    await dj._send_prompts({"hard rock": 1.0, "steady drums": 0.8})
    assert dj._session.sent[-1] == {"hard rock": 1.0}


async def test_new_prompts_clear_rejections():
    dj = _dj()
    dj._handle_filtered("upbeat", "nope")
    await dj.set_prompts({"upbeat": 1.0}, crossfade_s=0)
    assert dj._session.sent[-1] == {"upbeat": 1.0}


async def test_last_tag_is_never_removed():
    dj = _dj()
    dj._handle_filtered("deep house", "nope")
    assert dj.prompts == {"deep house": 1.0}


def test_guidance_default_sent():
    assert _dj()._gen_config().guidance == 5.0


def test_piano_avoided_unless_requested():
    from dj.lyria import with_avoid

    out = with_avoid({"hard rock": 1.0, "distorted guitar": 1.0})
    assert out["piano"] == -0.6
    assert "piano" not in with_avoid({"jazz": 1.0, "Rhodes Piano": 0.8})
    assert "piano" not in with_avoid({"lo-fi": 1.0, "dusty keys": 0.8})


async def test_negative_prompt_sent_but_hidden_from_llm():
    dj = _dj()
    await dj.set_prompts({"hard rock": 1.0}, crossfade_s=0)
    assert dj._session.raw[-1]["piano"] < 0
    assert "piano" not in dj.describe()


async def test_every_send_is_logged(caplog):
    import json
    import logging

    caplog.set_level(logging.INFO, logger="dj.lyria")
    dj = _dj()
    await dj.set_prompts({"hard rock": 1.0}, crossfade_s=0)
    sent = [
        json.loads(r.getMessage().split(" ", 1)[1])
        for r in caplog.records
        if r.getMessage().startswith("lyria_send")
    ]
    assert sent[-1]["op"] == "set_weighted_prompts"
    assert sent[-1]["prompts"]["hard rock"] == 1.0 and sent[-1]["prompts"]["piano"] < 0


async def test_unavailable_fires_once_after_repeated_connect_failures(monkeypatch):
    import dj.lyria as lyria

    monkeypatch.setattr(lyria, "MAX_CONNECT_FAILURES", 2)
    real_sleep = asyncio.sleep
    monkeypatch.setattr(lyria.asyncio, "sleep", lambda _s: real_sleep(0))

    class FailingConnect:
        async def __aenter__(self):
            raise ConnectionError("quota exhausted")

        async def __aexit__(self, *a):
            return False

    class FakeClient:
        class aio:  # noqa: N801
            class live:  # noqa: N801
                class music:  # noqa: N801
                    @staticmethod
                    def connect(model):
                        return FailingConnect()

    dj = LyriaDJ(api_key="unused", client=FakeClient())
    calls = []
    dj.on_unavailable = lambda: calls.append(1)
    await asyncio.wait_for(dj._run(), timeout=2)
    assert calls == [1]
