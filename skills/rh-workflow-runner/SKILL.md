---
name: rh-workflow-runner
description: "Run a RunningHub ComfyUI workflow (MiniMax H3 reference-to-video or any custom workflow) via the RunningHub API from the terminal or from Codex chat attachments. Use when the user wants to generate a clip from a prompt + reference images/video/audio, map attached files to Image N, confirm and submit a task, return the rendered video in chat, run the clip pipeline (analyze → confirm → batch run), add their own workflow_api.json + workflowId, or validate/check a workflow config. Keywords: runninghub, workflow, clips.json, analyze, submit, MiniMax H3, reference-to-video, workflow_api.json, nodeInfoList, taskId."
---

# RunningHub Workflow Runner

CLI tools for running a RunningHub ComfyUI workflow through the REST API,
driven by a `workflow_api.json` config (workflowId + node map) so any workflow
works without code changes.

> **If you are an AI agent installing this skill:** read
> `https://raw.githubusercontent.com/alekpr/rh-workflow-runner/master/INSTALL.md`
> and follow it end-to-end. The sections below are the reference once installed.

## Setup (required)

1. **Clone the repo**
   ```bash
   git clone https://github.com/<owner>/rh-workflow-runner.git
   cd rh-workflow-runner
   ```

2. **Requirements**
   - Python ≥ 3.10 (stdlib only — no pip install needed)
   - `ffmpeg` on PATH (used to remux output MOV → MP4)
   - RunningHub API key

3. **Environment variables**
   ```bash
   export RUNNINGHUB_API_KEY="<your-runninghub-api-key>"   # required
   export RUNNINGHUB_BASE_URL="https://www.runninghub.ai" # optional (default)
   export RUNNINGHUB_WORKFLOW_JSON="./workflow_api.json"  # optional
   ```
   The API key is created at RunningHub → API keys. `RUNNINGHUB_BASE_URL`
   overrides the API host if you use a proxy/mirror.

4. **Verify**
   ```bash
   python3 rh_pipeline.py check --config workflow_api.json
   ```
   This prints the resolved workflowId + node/slot map. If it prints without
   `ERROR`, setup is complete.

## Codex chat attachments

When a user attaches prompt/media files with **Add files** and wants Codex to
run the workflow, read [references/codex-chat.md](references/codex-chat.md)
before mapping files or submitting. Use the attachment paths supplied by the
Codex host; do not ask the user to type local paths that are already available.

## workflow_api.json (add your own workflow)

The built-in default targets the MiniMax H3 reference-to-video workflow
(`workflowId 2124499880344559618`). To use YOUR workflow, copy
`workflow_api.json`, change the fields, and pass `--config your.json` (or set
`RUNNINGHUB_WORKFLOW_JSON`).

Schema:

```jsonc
{
  "name": "My workflow",
  "workflowId": "1234567890",          // ← your workflow id (required)
  "maxDuration": 15.0,                 // seconds cap
  "nodes": {                           // fixed scalar nodes
    "prompt":   { "nodeId": "263", "fieldName": "text" },
    "duration": { "nodeId": "259", "fieldName": "value" },
    "aspect":   { "nodeId": "252", "fieldName": "aspect_ratio" },
    "seed":     { "nodeId": "397", "fieldName": "seed" }
  },
  "media": {                           // slot-based loaders (1-based order)
    "image": { "fieldName": "image", "nodeIds": ["51", "49", "50", "43", "19", "23", "199", "200", "201"] },
    "video": { "fieldName": "video", "nodeIds": ["27", "25", "26"] },
    "audio": { "fieldName": "audio", "nodeIds": ["48", "14", "15"] }
  },
  "aspectAliases": { "9:16": "9:16 (Portrait Widescreen)", "16:9": "16:9 (Widescreen)", "1:1": "1:1 (Square)" },
  "pictureToImage": { "1": "51", "2": "49", "3": "50", "4": "43", "5": "19", "6": "23", "7": "199", "8": "200", "9": "201" }
}
```

**How to find your node IDs** — export the workflow from ComfyUI/RunningHub as
the API format (`*_api.json`, like the reference files in `workflows/`). For
each node you want to set via API:

- `nodes.prompt.nodeId` → the `class_type` is `Text` (its widget name = `text`)
- `nodes.duration.nodeId` → `PrimitiveFloat` ("视频时长（秒）")
- `nodes.aspect.nodeId` → `ResolutionSelector` (widget `aspect_ratio`)
- `nodes.seed.nodeId` → the sampler (widget `seed`)
- `media.image.nodeIds` → every `LoadImage` node, in the order the main node
  receives `ref_images.ref_image_0..8`
- `media.video.nodeIds` → every `VHS_LoadVideo` node (`ref_videos.ref_video_0..2`)
- `media.audio.nodeIds` → every `LoadAudio` node (`ref_audios.ref_audio_0..2`)

Validate your config before spending credits:

```bash
python3 rh_pipeline.py check --config my_workflow.json
```

The loader reports missing `workflowId`, missing node/field names, or empty
media lists with exact error messages.

## Entry points

Two scripts:

| Script | Purpose |
|---|---|
| `rh_workflow_runner.py` | run a single clip (upload → submit → poll → download) |
| `rh_pipeline.py` | analyze a folder of prompts → `clips.json` → batch run |

### Single clip (rh_workflow_runner.py)

```bash
python3 rh_workflow_runner.py \
  --config workflow_api.json \
  --prompt-file my_prompt.txt \
  --duration 9 \
  --picture 1:refs/Image-1.png \
  --audio-slot 1:myclip-audio-1.mp3 \
  --output-dir ./out
```

Flags: `--config`, `--workflow-id`, `--prompt`/`--prompt-file`, `--duration`,
`--aspect`, `--seed`, `--picture N:PATH`, `--audio-slot N:PATH`,
`--image/--video/--audio NODEID:PATH`, `--output-dir`, `--dry-run`, `--json`,
`--show-config`.

### Pipeline (rh_pipeline.py)

```bash
# 1. scan a folder → clips.json (detects @Image N / <Picture N>, {clip}-audio-N.mp3, duration, aspect)
python3 rh_pipeline.py analyze --dir ./EP --refs-dir ./EP/Refs --config workflow_api.json --out clips.json

# 2. review clips.json, fill any missing "aspect"

# 3. batch run (sequential, continues after per-clip errors, writes results.json)
python3 rh_pipeline.py run clips.json --config workflow_api.json --yes --results results.json

# 4. validate a config without running anything
python3 rh_pipeline.py check --config workflow_api.json
```

## Endpoint contract (verified)

1. **upload** — `POST {BASE}/task/openapi/upload` (multipart: `apiKey`, `fileType=input`, `file`) → `data.fileName`
2. **submit** — `POST {BASE}/task/openapi/create` `{apiKey, workflowId, nodeInfoList}` → `data.taskId`
3. **poll** — `POST {BASE}/task/openapi/status` `{apiKey, taskId}`
4. **outputs** — `POST {BASE}/task/openapi/outputs` `{apiKey, taskId}` → download

> ⚠️ Do NOT use `/openapi/v2/media/upload/binary` for workflows — it returns an
> `openapi/...` path that ComfyUI loaders cannot resolve. Workflows need
> `/task/openapi/upload` (returns `data.fileName` like `api/xxx.png`).

## Conventions

- **Image refs** in prompts: `<Picture N>` or `@Image N` → slot N → nodeId via
  `media.image.nodeIds`. The pipeline auto-detects both syntaxes.
- **Audio naming**: `{clip}-audio-N.mp3` (or `{scene}-audio-N.mp3`) → audio slot N.
- **Aspect**: an explicit `--aspect` must be the workflow's exact enum string,
  such as `9:16 (Portrait Widescreen)`. Otherwise omit it and let prompt
  keywords (`9:16`/vertical/portrait, `16:9`/horizontal/landscape,
  `1:1`/square) resolve through `aspectAliases`; if none is found, the runner
  exits with code 3 and asks the user.
- **Unused media nodes MUST be disabled (muted) in the published workflow** —
  the API can only set field values, not enable/disable nodes.

## Troubleshooting

- `RUNNINGHUB_API_KEY is not set` → export the env var.
- `Invalid workflow config` → run `check`, fix the listed fields.
- `Required input is missing` from ComfyUI → an empty loader node in the
  workflow is not muted; mute it and republish.
- Image/character not matching the reference → wrong upload endpoint was used,
  or the reference is not in the highest-priority image slot (slot 1 is highest).
- `Connection reset by peer` mid-poll → check RunningHub task history before
  retrying. The current batch runner does not persist an active task ID or
  checkpoint partial results, so blindly re-running can submit a duplicate.
