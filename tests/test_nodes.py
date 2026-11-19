"""The nodes against a scripted Treza, with the media conversion stubbed (no torch needed)."""

import asyncio
import json

import pytest

from treza_nodes import client as c
from treza_nodes import nodes as n


class FakeResponse:
    def __init__(self, status, body, content=b""):
        self.status_code = status
        self._body = body
        self.text = str(body)
        self.content = content

    def json(self):
        return self._body


class FakeTreza:
    def __init__(self, **answers):
        self.answers = {tool: list(replies) for tool, replies in answers.items()}
        self.calls = []
        self.gets = []

    def post(self, url, json=None, headers=None, timeout=None):
        tool = url.rsplit("/", 1)[-1]
        self.calls.append((tool, json, headers))
        status, body = self.answers[tool].pop(0)
        return FakeResponse(status, body)

    def get(self, url, headers=None, timeout=None):
        self.gets.append((url, headers))
        return FakeResponse(200, {}, content=b"file-bytes")

    def asked(self, tool):
        return [args for name, args, _ in self.calls if name == tool]


QUOTE = {"pipelineId": "pipeline_1", "quoteId": "q1", "estimatedChargeUsd": 0.28, "sufficient": True, "model": {"id": "google/veo-3.1-lite"}}
DONE = {"status": "success", "outputs": {"video": "https://media.trezalabs.com/v.mp4"}}


@pytest.fixture
def treza(monkeypatch):
    fake = FakeTreza()
    monkeypatch.setattr(n, "client", lambda: c.TrezaClient("treza_live_test", session=fake))
    monkeypatch.setattr(n, "bytes_to_video", lambda data: ("VIDEO", data))
    monkeypatch.setattr(n, "png_to_image", lambda data: ("IMAGE", data))
    monkeypatch.setattr(n, "progress_hook", lambda: None)
    monkeypatch.setattr(c.time, "sleep", lambda s: None)
    return fake


def run(node, **inputs):
    return asyncio.run(node().run(**inputs))


def test_video_is_quoted_started_and_returned(treza):
    treza.answers = {"make_video": [(200, QUOTE)], "run_pipeline": [(200, {"runId": "r1"})], "get_run": [(200, DONE)]}
    video, url, price, model = run(n.TrezaGenerateVideo, prompt="a fox runs", shape="9:16", seconds=4, with_audio=True, max_cost_usd=0.0, seed=1)
    assert video == ("VIDEO", b"file-bytes")
    assert (url, price, model) == ("https://media.trezalabs.com/v.mp4", 0.28, "google/veo-3.1-lite")
    # No cap set, so none is sent; the seed stays in ComfyUI.
    assert treza.asked("make_video") == [{"prompt": "a fox runs", "aspectRatio": "9:16", "durationSec": 4.0, "withAudio": True}]
    assert treza.asked("run_pipeline") == [{"pipelineId": "pipeline_1", "quoteId": "q1"}]
    # The finished file is fetched without the API key.
    assert treza.gets == [("https://media.trezalabs.com/v.mp4", {"User-Agent": c.USER_AGENT})]


def test_over_max_cost_starts_nothing(treza):
    treza.answers = {"make_video": [(200, QUOTE)]}
    with pytest.raises(c.TrezaError) as err:
        run(n.TrezaGenerateVideo, prompt="a fox runs", shape="16:9", seconds=4, with_audio=True, max_cost_usd=0.10, seed=1)
    assert "over the Max cost of $0.10" in str(err.value)
    assert treza.asked("run_pipeline") == []
    assert treza.asked("make_video")[0]["maxPriceUsd"] == 0.10


def test_empty_prompt_calls_nothing(treza):
    with pytest.raises(c.TrezaError):
        run(n.TrezaGenerateImage, prompt="  ", shape="1:1", max_cost_usd=0.0, seed=0)
    assert treza.calls == []


def test_image_sends_shape_and_model(treza):
    quote = {**QUOTE, "estimatedChargeUsd": 0, "model": "google/gemini-2.5-flash-image"}
    done = {"status": "success", "outputs": {"image": "https://media.trezalabs.com/i.png"}}
    treza.answers = {"make_image": [(200, quote)], "run_pipeline": [(200, {"runId": "r1"})], "get_run": [(200, done)]}
    image, url, price, model = run(n.TrezaGenerateImage, prompt="a red kite", shape="4:5", max_cost_usd=0.0, seed=0, model=" google/gemini-2.5-flash-image ")
    assert image == ("IMAGE", b"file-bytes") and url.endswith("i.png") and price == 0.0
    assert model == "google/gemini-2.5-flash-image"
    assert treza.asked("make_image") == [{"prompt": "a red kite", "aspectRatio": "4:5", "model": "google/gemini-2.5-flash-image"}]


def test_still_rendering_after_the_wait_says_where_it_is(treza, monkeypatch):
    treza.answers = {"make_video": [(200, QUOTE)], "run_pipeline": [(200, {"runId": "r1"})], "get_run": [(200, {"status": "running"})]}
    clock = iter(range(0, 10**9, 10_000))  # every look at the clock is hours later
    monkeypatch.setattr(c.time, "monotonic", lambda: next(clock))
    with pytest.raises(c.TrezaError) as err:
        run(n.TrezaGenerateVideo, prompt="a fox runs", shape="16:9", seconds=4, with_audio=True, max_cost_usd=0.0, seed=1)
    assert "still rendering" in str(err.value) and "pipeline_1" in str(err.value)
    assert treza.gets == []


def test_pipeline_passes_inputs_and_returns_outputs(treza):
    quote = {"pipelineId": "pipeline_9", "quoteId": "q9", "estimatedChargeUsd": 1.5, "sufficient": True}
    done = {"status": "success", "outputs": {"final": "https://media.trezalabs.com/f.mp4", "note": "ok"}}
    treza.answers = {"estimate_run_cost": [(200, quote)], "run_pipeline": [(200, {"runId": "r9"})], "get_run": [(200, done)]}
    url, outputs, price = run(n.TrezaRunPipeline, pipeline_id=" pipeline_9 ", inputs_json='{"prompt_1": "a lighthouse"}', max_cost_usd=2.0, seed=0)
    assert url == "https://media.trezalabs.com/f.mp4" and price == 1.5
    assert json.loads(outputs) == done["outputs"]
    assert treza.asked("run_pipeline") == [{"pipelineId": "pipeline_9", "inputs": {"prompt_1": "a lighthouse"}, "quoteId": "q9"}]


def test_bad_inputs_json_calls_nothing(treza):
    with pytest.raises(c.TrezaError):
        run(n.TrezaRunPipeline, pipeline_id="pipeline_9", inputs_json="[1, 2]", max_cost_usd=0.0, seed=0)
    assert treza.calls == []


def test_a_broken_render_raises_the_reason(treza):
    broken = {"status": "error", "statusNote": "The model refused the prompt."}
    treza.answers = {"make_video": [(200, QUOTE)], "run_pipeline": [(200, {"runId": "r1"})], "get_run": [(200, broken)]}
    with pytest.raises(c.TrezaError) as err:
        run(n.TrezaGenerateVideo, prompt="x", shape="16:9", seconds=4, with_audio=False, max_cost_usd=0.0, seed=1)
    assert "The model refused the prompt." in str(err.value)
    assert treza.gets == []


def test_balance(treza):
    treza.answers = {"get_credit_balance": [(200, {"balanceUsd": 2.86})]}
    out = run(n.TrezaBalance)
    assert out["result"][0] == 2.86 and "$2.86" in out["ui"]["text"][0]


def test_load_refuses_a_non_link(treza):
    with pytest.raises(c.TrezaError):
        run(n.TrezaLoadVideo, url="file:///etc/passwd")


def test_key_comes_from_the_environment_or_a_file(monkeypatch, tmp_path):
    monkeypatch.setenv("TREZA_API_KEY", " treza_live_env ")
    assert n.api_key() == "treza_live_env"
    monkeypatch.delenv("TREZA_API_KEY")
    key_file = tmp_path / "treza_api_key.txt"
    key_file.write_text("treza_live_file\n")
    monkeypatch.setattr(n, "key_file_paths", lambda: [str(tmp_path / "missing.txt"), str(key_file)])
    assert n.api_key() == "treza_live_file"
    key_file.write_text("")
    with pytest.raises(c.TrezaError) as err:
        n.api_key()
    assert "TREZA_API_KEY" in str(err.value) and "missing.txt" in str(err.value)
