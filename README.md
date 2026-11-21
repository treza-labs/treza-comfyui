# Treza for ComfyUI

Generate AI pictures and videos from a ComfyUI graph with [Treza](https://www.trezalabs.com), and run your own Treza pipelines. The renders happen on Treza's servers, so they need no GPU or model downloads on your machine. Treza prices every render first. Set **max_cost_usd** and, if the price is higher, the node stops and nothing is spent.

## What Treza makes

![Four short clips made with Treza: a red hover car racing through a neon city, an astronaut walking over a Martian dune at sunset, an orchid mantis on a leaf, and espresso pouring into a cup](https://raw.githubusercontent.com/treza-labs/treza-labs/main/assets/treza-demo.gif)

These clips were made with [Treza](https://www.trezalabs.com), and the [ComfyUI integration page](https://www.trezalabs.com/integrations/comfyui) shows how to set up these nodes.

## Nodes

All nodes are under **Treza** in the node menu.

| Node | What it does | Cost |
| --- | --- | --- |
| **Treza Generate Image** | Makes a picture from a prompt, optionally from up to 4 reference pictures. Returns an `IMAGE`. | Uses your Treza credits |
| **Treza Generate Video** | Makes a video clip from a prompt, optionally opening or ending on a picture. Returns a `VIDEO`. | Uses your Treza credits |
| **Treza Run Pipeline** | Runs one of your saved Treza pipelines and returns the link to what it made. | Uses your Treza credits |
| **Treza Load Image (URL)** / **Treza Load Video (URL)** | Turns a link, such as a pipeline's result, into an `IMAGE` or `VIDEO`. | Free |
| **Treza Balance** | Shows the credits left on your Treza account. | Free |

Render nodes also return the file's `url`, the `price_usd` Treza charged and the `model` that made it.

## Install

In ComfyUI-Manager, search for **Treza** and install it. Or by hand:

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/treza-labs/treza-comfyui treza
```

Restart ComfyUI. It needs ComfyUI 0.3.45 or newer.

## Connect your Treza account

1. Create a Treza account at https://www.trezalabs.com and add credits. The nodes are free; renders are paid from your Treza credits.
2. Create an API key at https://www.trezalabs.com/platform/api-keys with the `pipelines:read`, `pipelines:write` and `pipelines:run` permissions.
3. Give ComfyUI the key in one of two ways, then restart it:
   - set the `TREZA_API_KEY` environment variable before starting ComfyUI, or
   - save the key as the only line of a file named `treza_api_key.txt` in ComfyUI's `user` folder.

The key is never a node setting, so it is not saved into workflows you share.

## Example workflows

Three templates ship with the pack and show under **Templates > treza** in ComfyUI: text to image, text to video, and image to video (a picture becomes the clip's first frame).

## Good to know

- **Price limit.** `max_cost_usd` is the most a render may cost. Treza prices the render first and the node stops, with nothing spent, when the price is higher. `0` means no limit: the render runs at Treza's price as long as your balance covers it.
- **Re-running.** ComfyUI reuses a node's last result while its settings are unchanged, so queueing the same graph again does not pay for the same render twice. Change `seed` to render again.
- **Cancel.** ComfyUI's Cancel button also cancels the render in Treza. A render that already finished a step is not refunded for that step.
- **Waiting.** Pictures usually take under a minute and videos one to five minutes. Each node waits up to `wait_minutes`. If the render is not done by then, the node stops with a link to it in Treza; it keeps rendering there, and you can load the finished file with a Treza Load node.
- **Model.** Leave `model` empty for Treza's default, or paste a model ID from Treza's model list, for example `google/veo-3.1-lite`.
- **Pictures you connect** (`reference_images`, `first_frame`, `last_frame`) are uploaded to your Treza library so the model can use them.
- **Run Pipeline inputs.** Leave `inputs_json` as `{}` to run the pipeline as saved, or pass one entry per input step, for example `{"prompt_1": "a lighthouse at dawn"}`. The pipeline ID is in the pipeline's address in Treza and starts with `pipeline_`.

## Errors you may see

- *This render costs $X, over the Max cost of $Y. Nothing was spent.* Raise `max_cost_usd`, or ask for a shorter clip or a cheaper model.
- *This render costs $X and your Treza balance is $Y.* Add credits in Treza under Settings.
- *Treza needs an API key.* Follow "Connect your Treza account" above.

## Network and privacy

The nodes call Treza's API over HTTPS at `www.trezalabs.com` and download finished files from `media.trezalabs.com`. They send your prompts, settings and any pictures you connect, with your API key, and store nothing else. The Load nodes fetch whatever link you give them. Treza's privacy policy: https://docs.trezalabs.com/terms-and-privacy/privacy

## Support

- Issues: https://github.com/treza-labs/treza-comfyui/issues
- Email: hello@trezalabs.com

## License

MIT
