"""Picture and video conversion. Needs torch; the video test also needs a ComfyUI checkout (COMFYUI_PATH)."""

import io
import os
import sys

import pytest

torch = pytest.importorskip("torch")
from PIL import Image  # noqa: E402

from treza_nodes import nodes as n  # noqa: E402


def test_picture_round_trip_keeps_shape_and_colour():
    image = torch.zeros((1, 48, 64, 3))
    image[0, :, :, 0] = 1.0  # pure red, 64 wide and 48 tall
    png = n.image_to_png(image[0])
    assert Image.open(io.BytesIO(png)).size == (64, 48)
    back = n.png_to_image(png)
    assert back.shape == (1, 48, 64, 3) and back.dtype == torch.float32
    assert torch.allclose(back, image)


def test_a_transparent_picture_comes_back_as_rgb():
    out = io.BytesIO()
    Image.new("RGBA", (10, 20), (0, 255, 0, 128)).save(out, format="PNG")
    assert n.png_to_image(out.getvalue()).shape == (1, 20, 10, 3)


def test_uploads_each_distinct_picture_once():
    class Client:
        api_key = "k"
        sent = []

        def upload(self, data, content_type, name):
            self.sent.append((content_type, name))
            return f"https://media.trezalabs.com/{len(self.sent)}.png"

    n._uploaded.clear()
    batch = torch.rand((6, 8, 8, 3))
    first = n.upload_images(Client(), batch, 4, "ref")
    again = n.upload_images(Client(), batch, 4, "ref")
    assert len(first) == 4 and first == again and len(Client.sent) == 4
    assert n.upload_images(Client(), None, 4, "ref") == []


def test_a_downloaded_clip_becomes_a_comfyui_video():
    comfy = os.environ.get("COMFYUI_PATH")
    sample = os.environ.get("TREZA_SAMPLE_MP4")
    if not comfy or not sample:
        pytest.skip("set COMFYUI_PATH and TREZA_SAMPLE_MP4")
    sys.path.insert(0, comfy)
    pytest.importorskip("comfy_api.input_impl", reason="needs ComfyUI's own dependencies installed")
    video = n.bytes_to_video(open(sample, "rb").read())
    assert video.get_dimensions() == (640, 360)
    frames = video.get_components().images
    assert frames.shape[1:] == (360, 640, 3) and frames.shape[0] > 1
