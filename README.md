# rh-workflow-runner

Run a RunningHub ComfyUI workflow through the REST API from the terminal —
**config-driven** via `workflow_api.json`, so any workflow (your own
`workflowId` + node map) works without code changes.

Built for MiniMax H3 reference-to-video, but generic: two CLI tools + one
workflow config file.

## Features

- `workflow_api.json` — declarative workflow topology (workflowId, node IDs,
  media slots, aspect enums). Copy it, edit it, pass `--config`.
- `rh_pipeline.py check` — validate a config and print the resolved node map
  **before spending credits**.
- `rh_pipeline.py analyze` — scan a folder of prompt files → `clips.json`
  (auto-detects `@Image N` / `<Picture N>`, `{clip}-audio-N.mp3`, duration,
  aspect ratio).
- `rh_pipeline.py run` — batch/queue clips one-by-one, monitor, download and
  remux (MOV → MP4), collect `results.json`.
- `rh_workflow_runner.py` — single-clip runner with `--dry-run` and `--json`.

## Quick start

```bash
git clone https://github.com/<owner>/rh-workflow-runner.git
cd rh-workflow-runner
export RUNNINGHUB_API_KEY="<your-key>"

# validate the bundled workflow config
python3 rh_pipeline.py check --config workflow_api.json

# single clip
python3 rh_workflow_runner.py --config workflow_api.json \
  --prompt-file my_prompt.txt --duration 9 \
  --picture 1:refs/Image-1.png --audio-slot 1:clip-audio-1.mp3

# full pipeline
python3 rh_pipeline.py analyze --dir ./EP --config workflow_api.json --out clips.json
python3 rh_pipeline.py run clips.json --config workflow_api.json --yes
```

## Requirements

- Python ≥ 3.10 (standard library only)
- `ffmpeg` on PATH
- `RUNNINGHUB_API_KEY` environment variable

## workflow_api.json

The bundled `workflow_api.json` targets the MiniMax H3 reference-to-video
workflow (`workflowId 2123782272871833602`). To use your own workflow, copy it
and set:

| Field | Meaning |
|---|---|
| `workflowId` | your RunningHub workflow id (required) |
| `maxDuration` | seconds cap (default 15) |
| `nodes.prompt` | `{nodeId, fieldName}` of the prompt/Text node |
| `nodes.duration` | `{nodeId, fieldName}` of the duration node |
| `nodes.aspect` | `{nodeId, fieldName}` of the ResolutionSelector |
| `nodes.seed` | `{nodeId, fieldName}` of the sampler seed |
| `media.image.nodeIds` | `LoadImage` node ids in `ref_image_0..8` order |
| `media.video.nodeIds` | `VHS_LoadVideo` node ids in `ref_video_0..2` order |
| `media.audio.nodeIds` | `LoadAudio` node ids in `ref_audio_0..2` order |
| `aspectAliases` | prompt keyword → aspect enum string |
| `pictureToImage` | picture/slot id → image node id |

```bash
python3 rh_pipeline.py check --config my_workflow.json   # validate + print map
```

### Bundled node map (MiniMax H3, workflowId 2123782272871833602)

| Field | nodeId | fieldName |
|---|---|---|
| prompt | 263 | text |
| duration | 259 | value |
| aspect | 252 | aspect_ratio |
| seed | 397 | seed |
| image ×9 | 51, 49, 50, 43, 19, 23, 199, 200, 201 | image |
| video ×3 | 27, 25, 26 | video |
| audio ×3 | 48, 14, 15 | audio |

Reference workflow exports live in `workflows/`:
- `YZ Goldfish-MiniMax H3-FOR-API_api.json` — API export (node → inputs map)
- `YZ Goldfish-MiniMax H3-FOR-API.json` — full ComfyUI graph

## Endpoint contract

| Action | Endpoint | Notes |
|---|---|---|
| upload | `POST {BASE}/task/openapi/upload` | multipart `apiKey`, `fileType=input`, `file` → `data.fileName` |
| submit | `POST {BASE}/task/openapi/create` | `{apiKey, workflowId, nodeInfoList}` → `data.taskId` |
| poll | `POST {BASE}/task/openapi/status` | `{apiKey, taskId}` |
| outputs | `POST {BASE}/task/openapi/outputs` | `{apiKey, taskId}` |

> ⚠️ Do NOT use `/openapi/v2/media/upload/binary` for workflows — ComfyUI
> loaders cannot resolve the `openapi/...` path it returns. Use
> `/task/openapi/upload`.

## One-link install for AI agents

Give your AI agent this link — it reads the guide and sets itself up (scripts +
skill + prerequisites + API-key guidance), and asks you only where needed:

```
https://github.com/alekpr/rh-workflow-runner/blob/master/INSTALL.md
```

Raw version (better for agents):
`https://raw.githubusercontent.com/alekpr/rh-workflow-runner/master/INSTALL.md`

### Copy-paste install prompt

ส่งข้อความนี้ให้ AI agent ตัวไหนก็ได้ (Copilot / Claude Code / อื่นๆ):

```
ช่วยติดตั้ง skill นี้ให้หน่อย โดยอ่านไฟล์จากลิงก์นี้ แล้วทำตามขั้นตอน setup ให้ครบ:

https://raw.githubusercontent.com/alekpr/rh-workflow-runner/master/INSTALL.md

ถ้าติดปัญหาตรงไหน ให้อธิบายวิธีแก้และแนะนำให้ผู้ใช้ทำเอง (ห้ามขอ API key ผ่านแชท)
```

## Agent skill

A portable agent skill lives in `skills/rh-workflow-runner/SKILL.md` — install it
into your agent (Copilot / Claude Code / other) so it can operate these scripts
without retyping the contract.

## License

Apache-2.0 — see `LICENSE`.
