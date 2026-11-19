"""Treza's nodes for ComfyUI.

Each render node asks Treza for a price, stops when the price is over Max cost,
starts the render once, waits for it and hands the finished picture or clip to
the graph. ComfyUI's Cancel button cancels the render in Treza too.

ComfyUI's own modules (torch, comfy, folder_paths) are imported inside the
functions that need them, so the client and the plain helpers can be tested
without ComfyUI installed.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import os
from typing import Any

from .client import (
    KEYS_PAGE,
    TOP_UP_URL,
    Data,
    TrezaClient,
    TrezaError,
    compact,
    finished_or_raise,
    max_cost,
    price_limit,
    primary_url,
    to_number,
)

log = logging.getLogger("treza")

CATEGORY = "Treza"
KEY_FILE = "treza_api_key.txt"
IMAGE_SHAPES = ["1:1", "16:9", "9:16", "4:5", "3:4", "4:3", "2:3", "3:2"]
VIDEO_SHAPES = ["16:9", "9:16", "1:1"]
MAX_REFERENCES = 4

SEED = ("INT", {"default": 0, "min": 0, "max": 0xFFFFFFFFFFFFFFFF, "tooltip": "ComfyUI reuses the last result while nothing changes. Change this number to render again."})
MAX_COST = ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1000.0, "step": 0.01, "tooltip": "The most this render may cost, in US dollars. Treza prices it first and stops, with nothing spent, when the price is higher. 0 means no limit."})
MODEL = ("STRING", {"default": "", "tooltip": "A model ID from Treza's model list. Leave empty for Treza's default."})


# ------------------------------------------------------------------ API key


def key_file_paths() -> list[str]:
    """Where the key file may sit: ComfyUI's user folder, then this pack's folder."""
    paths = []
    try:
        import folder_paths

        paths.append(os.path.join(folder_paths.get_user_directory(), KEY_FILE))
    except Exception:
        pass
    paths.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), KEY_FILE))
    return paths


def api_key() -> str:
    """The Treza API key, from TREZA_API_KEY or a key file. Never from the workflow, which gets shared."""
    key = (os.environ.get("TREZA_API_KEY") or "").strip()
    if key:
        return key
    paths = key_file_paths()
    for path in paths:
        try:
            with open(path, encoding="utf-8") as fh:
                key = fh.read().strip()
        except OSError:
            continue
        if key:
            return key
    raise TrezaError(
        f"Treza needs an API key. Make one at {KEYS_PAGE} (permissions: pipelines read, write and run), "
        f"then either set the TREZA_API_KEY environment variable before starting ComfyUI, or save the key "
        f"as the only line of {paths[0]}"
    )


def client() -> TrezaClient:
    return TrezaClient(api_key())


# -------------------------------------------------------------------- media


def image_to_png(image: Any) -> bytes:
    """One ComfyUI picture ([H,W,C] floats from 0 to 1) as PNG bytes."""
    import numpy as np
    from PIL import Image

    array = np.clip(image.detach().cpu().numpy() * 255.0, 0, 255).round().astype("uint8")
    if array.ndim == 3 and array.shape[2] == 1:
        array = array[:, :, 0]
    out = io.BytesIO()
    Image.fromarray(array).save(out, format="PNG")
    return out.getvalue()


def png_to_image(data: bytes) -> Any:
    """A downloaded picture as a ComfyUI IMAGE: [1,H,W,3] floats from 0 to 1."""
    import numpy as np
    import torch
    from PIL import Image, ImageOps

    picture = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    return torch.from_numpy(np.asarray(picture).astype("float32") / 255.0)[None,]


def bytes_to_video(data: bytes) -> Any:
    """A downloaded clip as a ComfyUI VIDEO."""
    from comfy_api.input_impl import VideoFromFile

    return VideoFromFile(io.BytesIO(data))


_uploaded: dict[str, str] = {}


def upload_images(treza: TrezaClient, images: Any, most: int, name: str) -> list[str]:
    """Put a batch of ComfyUI pictures in the Treza library; the same picture is uploaded once per session."""
    if images is None:
        return []
    urls = []
    for index in range(min(int(images.shape[0]), most)):
        png = image_to_png(images[index])
        digest = hashlib.sha256(png).hexdigest()
        cache_key = f"{hashlib.sha256(treza.api_key.encode()).hexdigest()[:12]}:{digest}"
        if cache_key not in _uploaded:
            _uploaded[cache_key] = treza.upload(png, "image/png", f"{name}-{digest[:8]}.png")
        urls.append(_uploaded[cache_key])
    return urls


# ------------------------------------------------------------------ waiting


def progress_hook():
    """Called on every poll: moves the node's progress bar and honours ComfyUI's Cancel button."""
    try:
        import comfy.model_management as management
        import comfy.utils

        bar = comfy.utils.ProgressBar(100)
    except Exception:
        return None

    def on_poll(run: Data) -> None:
        management.throw_exception_if_processing_interrupted()
        nodes = [n for n in (run.get("nodes") or []) if isinstance(n, dict)]
        if nodes:
            done = sum(1 for n in nodes if n.get("status") in ("success", "skipped"))
            bar.update_absolute(min(95, round(100 * done / len(nodes))), 100)

    return on_poll


def render(treza: TrezaClient, started: Data, wait_minutes: float, noun: str, output_key: str | None = None) -> tuple[Data, str]:
    """Wait for a started render and return (run, the finished file's URL)."""
    price = started.get("priceUsd")
    log.info("Treza: %s started at $%.2f (run %s)", noun, price or 0, started["runId"])
    run = treza.wait_for_run(started["pipelineId"], started["runId"], max(1.0, wait_minutes) * 60, progress_hook())
    run = {"runId": started["runId"], "pipelineId": started["pipelineId"], **run}
    finished_or_raise(run)
    page = f"https://www.trezalabs.com/platform/pipelines/{started['pipelineId']}"
    if run.get("status") == "running":
        raise TrezaError(
            f"The {noun} was still rendering after {wait_minutes:g} minutes, so ComfyUI stopped waiting. "
            f"It keeps rendering in Treza and has been paid for: {page} "
            "When it is done, copy its link into a Treza Load node. Raise Wait (minutes) to wait longer next time."
        )
    url = primary_url(run, output_key)
    if not url:
        raise TrezaError(f"The render finished with status {run.get('status')} and no file. See {page}")
    return run, url


def quoted_model(started: Data) -> str:
    model = (started.get("quoted") or {}).get("model")
    return str(model.get("id") if isinstance(model, dict) else model or "")


def in_thread(fn):
    """Run a node's blocking work off ComfyUI's event loop, so several Treza nodes render at once."""

    async def run(self, **inputs):
        return await asyncio.to_thread(fn, self, **inputs)

    return run


# -------------------------------------------------------------------- nodes


class TrezaGenerateImage:
    DESCRIPTION = "Make a picture from a description with Treza. Uses the credits on your Treza account."
    CATEGORY = CATEGORY
    FUNCTION = "run"
    RETURN_TYPES = ("IMAGE", "STRING", "FLOAT", "STRING")
    RETURN_NAMES = ("image", "url", "price_usd", "model")

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": ("STRING", {"multiline": True, "default": "", "tooltip": "What the picture shows."}),
                "shape": (IMAGE_SHAPES, {"default": "1:1"}),
                "max_cost_usd": MAX_COST,
                "seed": SEED,
            },
            "optional": {
                "reference_images": ("IMAGE", {"tooltip": f"Up to {MAX_REFERENCES} pictures to restyle or keep a character from. They are uploaded to your Treza library."}),
                "model": MODEL,
                "wait_minutes": ("FLOAT", {"default": 5.0, "min": 1.0, "max": 60.0, "step": 1.0, "tooltip": "How long to wait for the picture."}),
            },
        }

    @in_thread
    def run(self, prompt, shape, max_cost_usd, seed, reference_images=None, model="", wait_minutes=5.0):
        if not str(prompt).strip():
            raise TrezaError("Describe the picture in Prompt.")
        treza = client()
        cap = max_cost(max_cost_usd)
        args = compact(
            {
                "prompt": prompt,
                "aspectRatio": shape,
                "model": str(model).strip(),
                "referenceImageUrls": upload_images(treza, reference_images, MAX_REFERENCES, "comfyui-reference"),
                "maxPriceUsd": price_limit(cap),
            }
        )
        started = treza.start_quoted(cap, lambda: treza.request("make_image", args))
        _, url = render(treza, started, wait_minutes, "picture", "image")
        return (png_to_image(treza.download(url)), url, float(started.get("priceUsd") or 0), quoted_model(started))


class TrezaGenerateVideo:
    DESCRIPTION = "Make a video clip from a description with Treza. Uses the credits on your Treza account."
    CATEGORY = CATEGORY
    FUNCTION = "run"
    RETURN_TYPES = ("VIDEO", "STRING", "FLOAT", "STRING")
    RETURN_NAMES = ("video", "url", "price_usd", "model")

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": ("STRING", {"multiline": True, "default": "", "tooltip": "What happens in the clip."}),
                "shape": (VIDEO_SHAPES, {"default": "16:9"}),
                "seconds": ("INT", {"default": 4, "min": 1, "max": 60, "tooltip": "Clip length. Models that render fixed lengths use the next one up."}),
                "with_audio": ("BOOLEAN", {"default": True, "tooltip": "Generate sound with the video where the model can."}),
                "max_cost_usd": MAX_COST,
                "seed": SEED,
            },
            "optional": {
                "first_frame": ("IMAGE", {"tooltip": "The picture the clip opens on. It is uploaded to your Treza library."}),
                "last_frame": ("IMAGE", {"tooltip": "The picture the clip ends on, on models that support it."}),
                "model": MODEL,
                "wait_minutes": ("FLOAT", {"default": 15.0, "min": 1.0, "max": 120.0, "step": 1.0, "tooltip": "How long to wait for the clip."}),
            },
        }

    @in_thread
    def run(self, prompt, shape, seconds, with_audio, max_cost_usd, seed, first_frame=None, last_frame=None, model="", wait_minutes=15.0):
        if not str(prompt).strip():
            raise TrezaError("Describe the clip in Prompt.")
        treza = client()
        cap = max_cost(max_cost_usd)
        first = upload_images(treza, first_frame, 1, "comfyui-first-frame")
        last = upload_images(treza, last_frame, 1, "comfyui-last-frame")
        args = compact(
            {
                "prompt": prompt,
                "aspectRatio": shape,
                "durationSec": to_number(seconds),
                "withAudio": bool(with_audio),
                "model": str(model).strip(),
                "firstFrameUrl": first[0] if first else None,
                "lastFrameUrl": last[0] if last else None,
                "maxPriceUsd": price_limit(cap),
            }
        )
        started = treza.start_quoted(cap, lambda: treza.request("make_video", args))
        _, url = render(treza, started, wait_minutes, "video", "video")
        return (bytes_to_video(treza.download(url)), url, float(started.get("priceUsd") or 0), quoted_model(started))


class TrezaRunPipeline:
    DESCRIPTION = "Run one of your saved Treza pipelines and get the link to what it made. Connect the link to a Treza Load node."
    CATEGORY = CATEGORY
    FUNCTION = "run"
    RETURN_TYPES = ("STRING", "STRING", "FLOAT")
    RETURN_NAMES = ("url", "outputs_json", "price_usd")

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "pipeline_id": ("STRING", {"default": "", "tooltip": "It is in the pipeline's address in Treza and starts with pipeline_."}),
                "inputs_json": ("STRING", {"multiline": True, "default": "{}", "tooltip": 'One entry per input step of the pipeline, for example {"prompt_1": "a lighthouse at dawn"}.'}),
                "max_cost_usd": MAX_COST,
                "seed": SEED,
            },
            "optional": {
                "wait_minutes": ("FLOAT", {"default": 30.0, "min": 1.0, "max": 240.0, "step": 1.0, "tooltip": "How long to wait for the pipeline."}),
            },
        }

    @in_thread
    def run(self, pipeline_id, inputs_json, max_cost_usd, seed, wait_minutes=30.0):
        pipeline_id = str(pipeline_id).strip()
        if not pipeline_id:
            raise TrezaError("Add the Pipeline ID. It is in the pipeline's address in Treza and starts with pipeline_.")
        inputs = parse_inputs(inputs_json)
        treza = client()
        cap = max_cost(max_cost_usd)
        started = treza.start_quoted(cap, lambda: treza.request("estimate_run_cost", {"pipelineId": pipeline_id}), inputs or None)
        run, url = render(treza, started, wait_minutes, "pipeline run")
        outputs = run.get("outputs") if isinstance(run.get("outputs"), dict) else {}
        return (url, json.dumps(outputs, indent=2), float(started.get("priceUsd") or 0))


def parse_inputs(raw: Any) -> Data:
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        inputs = json.loads(raw)
    except ValueError as err:
        raise TrezaError('Inputs must be JSON, for example {"prompt_1": "a lighthouse at dawn"}.') from err
    if not isinstance(inputs, dict):
        raise TrezaError('Inputs must be a JSON object with one entry per input step, for example {"prompt_1": "a lighthouse at dawn"}.')
    return inputs


def checked_url(url: Any) -> str:
    url = str(url or "").strip()
    if not url.startswith(("https://", "http://")):
        raise TrezaError("Connect a link that starts with https://, such as the url output of a Treza node.")
    return url


class TrezaLoadImage:
    DESCRIPTION = "Load a picture from a link, such as the url a Treza pipeline returned."
    CATEGORY = CATEGORY
    FUNCTION = "run"
    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"url": ("STRING", {"default": "", "tooltip": "The picture's link."})}}

    @in_thread
    def run(self, url):
        return (png_to_image(TrezaClient("").download(checked_url(url))),)


class TrezaLoadVideo:
    DESCRIPTION = "Load a video from a link, such as the url a Treza pipeline returned."
    CATEGORY = CATEGORY
    FUNCTION = "run"
    RETURN_TYPES = ("VIDEO",)
    RETURN_NAMES = ("video",)

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"url": ("STRING", {"default": "", "tooltip": "The video's link."})}}

    @in_thread
    def run(self, url):
        return (bytes_to_video(TrezaClient("").download(checked_url(url))),)


class TrezaBalance:
    DESCRIPTION = "Your Treza credit balance. Connect it to a Preview Any node to read it."
    CATEGORY = CATEGORY
    FUNCTION = "run"
    OUTPUT_NODE = True
    RETURN_TYPES = ("FLOAT", "STRING")
    RETURN_NAMES = ("balance_usd", "text")

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {}}

    @classmethod
    def IS_CHANGED(cls, **_):
        return float("nan")  # a balance is never reused from the last run

    @in_thread
    def run(self):
        balance = client().call("get_credit_balance")
        usd = to_number(balance.get("balanceUsd"))
        if usd is None:
            raise TrezaError("Treza did not report a balance.")
        text = f"Your Treza balance is ${usd:.2f}. Add credits at {balance.get('topUpUrl') or TOP_UP_URL}"
        log.info("Treza: %s", text)
        return {"ui": {"text": [text]}, "result": (usd, text)}


NODE_CLASS_MAPPINGS = {
    "TrezaGenerateImage": TrezaGenerateImage,
    "TrezaGenerateVideo": TrezaGenerateVideo,
    "TrezaRunPipeline": TrezaRunPipeline,
    "TrezaLoadImage": TrezaLoadImage,
    "TrezaLoadVideo": TrezaLoadVideo,
    "TrezaBalance": TrezaBalance,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "TrezaGenerateImage": "Treza Generate Image",
    "TrezaGenerateVideo": "Treza Generate Video",
    "TrezaRunPipeline": "Treza Run Pipeline",
    "TrezaLoadImage": "Treza Load Image (URL)",
    "TrezaLoadVideo": "Treza Load Video (URL)",
    "TrezaBalance": "Treza Balance",
}
