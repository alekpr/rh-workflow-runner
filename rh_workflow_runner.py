#!/usr/bin/env python3
"""
RunningHub ComfyUI workflow runner (config-driven).

Workflow topology (node IDs, media slots, aspect enums) comes from
`workflow_api.json` — provide your own workflowId + node map and this runner
adapts without code changes. Defaults target the MiniMax H3 reference-to-video
workflow (workflowId 2124499880344559618).

Endpoint contract (workflow path, verified against runninghubClient.js):

  upload  : POST {BASE}/task/openapi/upload    (multipart: apiKey, fileType=input, file)
  submit  : POST {BASE}/task/openapi/create    {apiKey, workflowId, nodeInfoList}
  status  : POST {BASE}/task/openapi/status    {apiKey, taskId}
  outputs : POST {BASE}/task/openapi/outputs   {apiKey, taskId}

  ⚠️  Do NOT use /openapi/v2/media/upload/binary for workflows: it returns an
     `openapi/...` path that ComfyUI loaders cannot resolve. Workflows need
     /task/openapi/upload (returns `data.fileName` like `api/xxx.png`).

PARTIAL MEDIA — the core problem:
  /task/openapi/create only *overrides field values* of nodes listed in
  `nodeInfoList`. It cannot enable/disable nodes. So:

    * Pass --image/--video/--audio ONLY for the slots a given shot uses.
    * Any LoadImage/LoadAudio/VHS_LoadVideo node left empty in the published
      workflow MUST be disabled (muted) in the RunningHub workflow editor,
      otherwise ComfyUI fails validation ("Required input is missing").
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import random
import re
import sys
import time
import uuid
from pathlib import Path
from urllib import error, request

BASE = (
    os.environ.get("RUNNINGHUB_BASE")
    or os.environ.get("RUNNINGHUB_BASE_URL")
    or "https://www.runninghub.ai"
).rstrip("/")
API_KEY = os.environ.get("RUNNINGHUB_API_KEY", "").strip()

DEFAULT_WORKFLOW_ID = "2124499880344559618"

# ---------------------------------------------------------------------------
# Built-in workflow config. Users override this with their own workflow_api.json
# (workflowId + node map). See load_workflow_config() below.
# ---------------------------------------------------------------------------
DEFAULT_WORKFLOW_CONFIG = {
    "name": "MiniMax H3 reference-to-video",
    "workflowId": DEFAULT_WORKFLOW_ID,
    "maxDuration": 15.0,
    "nodes": {
        "prompt":   {"nodeId": "263", "fieldName": "text"},
        "duration": {"nodeId": "259", "fieldName": "value", "valueType": "number"},
        "aspect":   {"nodeId": "252", "fieldName": "aspect_ratio"},
        "seed":     {"nodeId": "397", "fieldName": "seed", "valueType": "number"},
    },
    "media": {
        "image": {"fieldName": "image", "nodeIds": ["51", "49", "50", "43", "19", "23", "199", "200", "201"]},
        "video": {"fieldName": "video", "nodeIds": ["27", "25", "26"]},
        "audio": {"fieldName": "audio", "nodeIds": ["48", "14", "15"]},
    },
    "aspectAliases": {
        "16:9": "16:9 (Widescreen)",
        "9:16": "9:16 (Portrait Widescreen)",
        "1:1": "1:1 (Square)",
    },
    # <Picture N> / @Image N registry id -> image nodeId (slot).
    "pictureToImage": {
        "1": "51", "2": "49", "3": "50", "4": "43", "8": "200", "9": "201",
    },
}

# Resolved runtime globals — mutated by _apply_workflow_config().
WORKFLOW_ID = DEFAULT_WORKFLOW_ID
MAX_DURATION = 15.0
NODE: dict = {}
IMAGE_SLOT_TO_NODE: dict = {}
VIDEO_SLOT_TO_NODE: dict = {}
AUDIO_SLOT_TO_NODE: dict = {}
PICTURE_TO_IMAGE_NODE: dict = {}
ASPECT_ALIASES: dict = {}


def _slot_map(node_ids: list) -> dict:
    """nodeIds list (1-based order) -> {slot: nodeId}."""
    return {str(i + 1): str(n) for i, n in enumerate(node_ids)}


def _media_ids(media: dict, kind: str) -> list:
    """Normalize media.<kind> to a list of nodeId strings."""
    m = media.get(kind)
    if isinstance(m, dict):
        return [str(i) for i in (m.get("nodeIds") or [])]
    if isinstance(m, list):
        return [str(i) for i in m]
    return []


def _media_field(media: dict, kind: str) -> str:
    m = media.get(kind)
    if isinstance(m, dict) and m.get("fieldName"):
        return str(m["fieldName"])
    return kind


def _apply_workflow_config(config: dict) -> None:
    global WORKFLOW_ID, MAX_DURATION, NODE
    global IMAGE_SLOT_TO_NODE, VIDEO_SLOT_TO_NODE, AUDIO_SLOT_TO_NODE
    global PICTURE_TO_IMAGE_NODE, ASPECT_ALIASES

    nodes = config["nodes"]
    media = config.get("media", {})

    NODE = {
        "prompt":   {"nodeId": str(nodes["prompt"]["nodeId"]), "fieldName": nodes["prompt"]["fieldName"]},
        "duration": {"nodeId": str(nodes["duration"]["nodeId"]), "fieldName": nodes["duration"]["fieldName"]},
        "aspect":   {"nodeId": str(nodes["aspect"]["nodeId"]), "fieldName": nodes["aspect"]["fieldName"]},
        "seed":     {"nodeId": str(nodes["seed"]["nodeId"]), "fieldName": nodes["seed"]["fieldName"]},
    }
    for kind in ("image", "video", "audio"):
        NODE[kind] = {"fieldName": _media_field(media, kind), "nodeIds": _media_ids(media, kind)}

    IMAGE_SLOT_TO_NODE = _slot_map(NODE["image"]["nodeIds"])
    VIDEO_SLOT_TO_NODE = _slot_map(NODE["video"]["nodeIds"])
    AUDIO_SLOT_TO_NODE = _slot_map(NODE["audio"]["nodeIds"])
    PICTURE_TO_IMAGE_NODE = {str(k): str(v) for k, v in (config.get("pictureToImage") or {}).items()}
    ASPECT_ALIASES = {str(k): str(v) for k, v in (config.get("aspectAliases") or {}).items()}
    WORKFLOW_ID = str(config["workflowId"])
    MAX_DURATION = float(config.get("maxDuration", 15.0))


def validate_workflow_config(config: dict) -> list:
    """Return a list of human-readable validation errors (empty = valid)."""
    errors = []
    if not str(config.get("workflowId") or "").strip():
        errors.append('"workflowId" is required')
    nodes = config.get("nodes")
    if not isinstance(nodes, dict):
        errors.append('"nodes" must be an object with prompt/duration/aspect/seed')
    else:
        for key in ("prompt", "duration", "aspect", "seed"):
            n = nodes.get(key)
            if not isinstance(n, dict) or not str(n.get("nodeId") or "").strip():
                errors.append(f'nodes.{key}.nodeId is required')
            elif not str(n.get("fieldName") or "").strip():
                errors.append(f'nodes.{key}.fieldName is required')
    media = config.get("media")
    if not isinstance(media, dict):
        errors.append('"media" must be an object with image/video/audio nodeIds')
    else:
        for kind in ("image", "video", "audio"):
            if not _media_ids(media, kind):
                errors.append(f'media.{kind}.nodeIds must be a non-empty list')
    return errors


def load_workflow_config(source=None) -> dict:
    """Resolve, validate and apply a workflow config.

    source: path to workflow_api.json, a dict, or None →
            env RUNNINGHUB_WORKFLOW_JSON → built-in default.
    Returns the fully-merged (defaults + user) config dict.
    """
    if source is None:
        source = os.environ.get("RUNNINGHUB_WORKFLOW_JSON", "").strip()
        if not source:
            source = DEFAULT_WORKFLOW_CONFIG

    if isinstance(source, dict):
        raw, origin = source, "<dict>"
    else:
        p = Path(source)
        if not p.is_file():
            raise RhError(f"workflow config not found: {source}")
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise RhError(f"workflow config is not valid JSON ({p}): {e}")
        origin = str(p)

    if not isinstance(raw, dict):
        raise RhError(f"workflow config must be a JSON object ({origin})")

    # Deep-merge over defaults so partial configs still get the rest.
    config = {**DEFAULT_WORKFLOW_CONFIG, **raw}
    config["nodes"] = {**DEFAULT_WORKFLOW_CONFIG["nodes"], **(raw.get("nodes") or {})}
    config["media"] = {**DEFAULT_WORKFLOW_CONFIG["media"], **(raw.get("media") or {})}

    errors = validate_workflow_config(config)
    if errors:
        raise RhError("Invalid workflow config (" + origin + "):\n  - " + "\n  - ".join(errors))

    _apply_workflow_config(config)
    return config


def describe_workflow() -> str:
    """Human-readable summary of the resolved workflow config."""
    lines = [
        f"workflowId : {WORKFLOW_ID}",
        f"maxDuration: {MAX_DURATION}s",
        "nodes:",
    ]
    for k, n in NODE.items():
        if k in ("image", "video", "audio"):
            lines.append(f"  {k:8s} fieldName={n['fieldName']} nodeIds={','.join(n['nodeIds'])}")
        else:
            lines.append(f"  {k:8s} nodeId={n['nodeId']} fieldName={n['fieldName']}")
    lines.append(f"image slots: {IMAGE_SLOT_TO_NODE}")
    lines.append(f"video slots: {VIDEO_SLOT_TO_NODE}")
    lines.append(f"audio slots: {AUDIO_SLOT_TO_NODE}")
    lines.append(f"picture map: {PICTURE_TO_IMAGE_NODE or '(none)'}")
    lines.append(f"aspects    : {ASPECT_ALIASES}")
    return "\n".join(lines)


# Initialize runtime globals from the built-in default config.
_apply_workflow_config(DEFAULT_WORKFLOW_CONFIG)


def detect_aspect(prompt: str):
    """Return the ResolutionSelector aspect_ratio enum mentioned in the prompt,
    or None if the prompt does not specify one."""
    p = prompt.lower()
    for ratio, enum in ASPECT_ALIASES.items():
        if ratio in prompt:
            return enum
    if re.search(r"vertical|portrait|แนวตั้ง|竖屏|竖版", p):
        return ASPECT_ALIASES["9:16"]
    if re.search(r"horizontal|landscape|แนวนอน|横屏|横版", p):
        return ASPECT_ALIASES["16:9"]
    if re.search(r"square|จัตุรัส|方形|正方", p):
        return ASPECT_ALIASES["1:1"]
    return None

TERMINAL_SUCCESS = {"SUCCESS", "SUCCEEDED", "COMPLETED", "FINISHED", "DONE"}
TERMINAL_FAIL = {"FAILED", "FAIL", "ERROR", "CANCELLED", "CANCELED", "ABORTED"}


class RhError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def api_post(endpoint: str, body: dict) -> dict:
    if not API_KEY:
        raise RhError("RUNNINGHUB_API_KEY is not set")
    url = f"{BASE}{endpoint}"
    data = json.dumps({"apiKey": API_KEY, **body}).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}",
        },
    )
    try:
        with request.urlopen(req, timeout=60) as resp:
            text = resp.read().decode("utf-8")
    except error.HTTPError as e:
        text = e.read().decode("utf-8", "replace")
        raise RhError(f"RunningHub API error ({e.code}) on {endpoint}: {text}")
    except error.URLError as e:
        raise RhError(f"RunningHub network error on {endpoint}: {e.reason}")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise RhError(f"RunningHub non-JSON response on {endpoint}: {text[:200]}")


def upload_file(path: str) -> str:
    """Upload a local media file for a RunningHub workflow (/task/openapi/create).

    Uses the workflow/app upload endpoint and returns data.fileName, which the
    workflow's LoadImage/LoadAudio/VHS_LoadVideo nodes can resolve. (The standard
    model endpoint /media/upload/binary returns an `openapi/...` path that ComfyUI
    loaders cannot find.)
    """
    p = Path(path)
    if not p.is_file():
        raise RhError(f"File does not exist: {path}")
    mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    boundary = f"----rh-{uuid.uuid4().hex}"

    def form_field(name: str, value: str) -> bytes:
        return (
            f"--{boundary}\r\n".encode()
            + f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
            + value.encode("utf-8")
            + b"\r\n"
        )

    body = bytearray()
    body += form_field("apiKey", API_KEY)
    body += form_field("fileType", "input")
    body += f"--{boundary}\r\n".encode()
    body += f'Content-Disposition: form-data; name="file"; filename="{p.name}"\r\n'.encode()
    body += f"Content-Type: {mime}\r\n\r\n".encode()
    body += p.read_bytes()
    body += f"\r\n--{boundary}--\r\n".encode()

    req = request.Request(
        f"{BASE}/task/openapi/upload",
        data=bytes(body),
        method="POST",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    try:
        with request.urlopen(req, timeout=120) as resp:
            res = json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as e:
        raise RhError(f"Upload failed ({e.code}): {e.read().decode('utf-8', 'replace')}")

    if res.get("code") != 0 or res.get("msg") != "success":
        raise RhError(f"Upload failed: {res.get('msg') or res}")
    d = res.get("data") or {}
    name = d.get("fileName") or d.get("filename") or d.get("file_name") or d.get("name")
    if not name:
        raise RhError(f"Upload response missing fileName: {res}")
    return str(name)


# ---------------------------------------------------------------------------
# Task lifecycle
# ---------------------------------------------------------------------------
def submit(workflow_id: str, node_info_list: list) -> str:
    res = api_post("/task/openapi/create", {"workflowId": workflow_id, "nodeInfoList": node_info_list})
    if res.get("code") != 0:
        raise RhError(f"Submit failed: {res.get('msg') or res}")
    task_id = (res.get("data") or {}).get("taskId") or res.get("taskId")
    if not task_id:
        raise RhError(f"Submit response missing taskId: {res}")
    return str(task_id)


def get_status(task_id: str) -> dict:
    res = api_post("/task/openapi/status", {"taskId": task_id})
    if res.get("code") != 0:
        raise RhError(f"Status failed: {res.get('msg') or res}")
    d = res.get("data") or {}
    raw = d if isinstance(d, str) else (d.get("taskStatus") or d.get("status") or "")
    return {
        "status": str(raw).upper(),
        "progress": d.get("progress") if isinstance(d, dict) else None,
        "errorMessage": d.get("errorMessage") if isinstance(d, dict) else None,
    }


def get_outputs(task_id: str) -> list:
    res = api_post("/task/openapi/outputs", {"taskId": task_id})
    if res.get("code") != 0:
        raise RhError(f"Outputs failed: {res.get('msg') or res}")
    d = res.get("data")
    if isinstance(d, list):
        return d
    if isinstance(d, dict) and isinstance(d.get("files"), list):
        return d["files"]
    if isinstance(d, dict) and (d.get("fileUrl") or d.get("url") or d.get("src")):
        return [d]
    return []


def poll(task_id: str, interval: float = 8.0, timeout: float = 15 * 60) -> dict:
    started = time.time()
    while True:
        s = get_status(task_id)
        status = s["status"]
        if any(k in status for k in TERMINAL_SUCCESS):
            return {"ok": True, "status": status}
        if any(k in status for k in TERMINAL_FAIL):
            return {"ok": False, "status": status, "errorMessage": s.get("errorMessage")}
        if time.time() - started > timeout:
            raise RhError(f"Task {task_id} timed out — inspect it in RunningHub call records")
        time.sleep(interval)


def download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = request.Request(url)
    try:
        with request.urlopen(req, timeout=120) as resp:
            dest.write_bytes(resp.read())
    except error.HTTPError as e:
        raise RhError(f"Download failed ({e.code}): {url}")
    return dest


def pick_video_output(outputs: list):
    url_of = lambda o: o.get("fileUrl") or o.get("url") or o.get("src") or o.get("downloadUrl") or o.get("videoUrl")  # noqa: E731
    for o in outputs:
        t = str(o.get("fileType") or o.get("type") or o.get("mediaType") or "").lower()
        u = str(url_of(o) or "").lower()
        if "video" in t or "mp4" in t or "webm" in t or "mov" in t or u.endswith((".mp4", ".webm", ".mov")):
            return o
    return next((o for o in outputs if url_of(o)), None)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def parse_media_arg(value: str) -> tuple[str, str]:
    if ":" in value:
        node_id, path = value.split(":", 1)
    else:
        node_id, path = "", value
    return node_id.strip(), path.strip()


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Run a RunningHub ComfyUI workflow (MiniMax H3 reference-to-video).",
        epilog=(
            "Node IDs come from workflow_api.json (--config) or the built-in default.\n"
            "Run with --show-config to see the resolved node/slot map.\n"
            "Pass --image/--video/--audio as <nodeId>:<path>; omit slots you don't use."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--workflow-id", default=None, help="override workflowId from config")
    ap.add_argument("--config", default=None, help="path to workflow_api.json (or set RUNNINGHUB_WORKFLOW_JSON)")
    ap.add_argument("--show-config", action="store_true", help="print resolved workflow config and exit")
    ap.add_argument("--prompt", default=None, help="Prompt text (prompt node)")
    ap.add_argument("--prompt-file", default=None, help="Read prompt from a file instead of --prompt")
    ap.add_argument("--duration", type=float, default=None, help="Seconds (default: config maxDuration)")
    ap.add_argument("--aspect", default=None, help="aspect_ratio enum. If omitted, auto-detect from prompt; error if not found.")
    ap.add_argument("--seed", type=int, default=None, help="seed value")
    ap.add_argument("--image", action="append", default=[], metavar="NODEID:PATH", help="reference image (repeatable)")
    ap.add_argument("--picture", action="append", default=[], metavar="N:PATH", help="reference image by picture/slot id N (see config pictureToImage) (repeatable)")
    ap.add_argument("--video", action="append", default=[], metavar="NODEID:PATH", help="reference video (repeatable)")
    ap.add_argument("--audio", action="append", default=[], metavar="NODEID:PATH", help="reference audio (repeatable)")
    ap.add_argument("--audio-slot", action="append", default=[], metavar="N:PATH", help="reference audio by slot N (see config audio slots) (repeatable)")
    ap.add_argument("--output-dir", default="./rh-output")
    ap.add_argument("--dry-run", action="store_true", help="Upload files and print the submit payload without submitting")
    ap.add_argument("--json", action="store_true", help="Machine-readable output")
    args = ap.parse_args()

    try:
        cfg = load_workflow_config(args.config)
    except RhError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    workflow_id = args.workflow_id or WORKFLOW_ID

    if args.show_config:
        print(json.dumps(cfg, ensure_ascii=False, indent=2))
        print("--- resolved ---")
        print(describe_workflow())
        return 0

    if not API_KEY:
        print("ERROR: RUNNINGHUB_API_KEY is not set", file=sys.stderr)
        return 2

    duration = float(args.duration) if args.duration is not None else MAX_DURATION
    if duration > MAX_DURATION:
        print(f"  duration {duration}s exceeds workflow maxDuration; capped to {MAX_DURATION}s")
        duration = MAX_DURATION

    prompt = args.prompt
    if args.prompt_file:
        prompt = Path(args.prompt_file).read_text(encoding="utf-8")
    if not prompt:
        print("ERROR: provide --prompt or --prompt-file", file=sys.stderr)
        return 2

    aspect = args.aspect
    if not aspect:
        aspect = detect_aspect(prompt)
        if aspect:
            print(f"  aspect auto-detected from prompt: {aspect}")
    if not aspect:
        print("ERROR: aspect ratio not found in prompt and --aspect not given.", file=sys.stderr)
        print("  valid: " + ", ".join(sorted(ASPECT_ALIASES.values())), file=sys.stderr)
        print("  re-run with --aspect '<value>' (e.g. --aspect '16:9 (Widescreen)')", file=sys.stderr)
        return 3

    node_info: list[dict] = [
        {"nodeId": NODE["prompt"]["nodeId"], "fieldName": NODE["prompt"]["fieldName"], "fieldValue": prompt},
        {"nodeId": NODE["duration"]["nodeId"], "fieldName": NODE["duration"]["fieldName"], "fieldValue": duration},
        {"nodeId": NODE["aspect"]["nodeId"], "fieldName": NODE["aspect"]["fieldName"], "fieldValue": aspect},
        {"nodeId": NODE["seed"]["nodeId"], "fieldName": NODE["seed"]["fieldName"], "fieldValue": int(args.seed if args.seed is not None else random.randint(1, 2**63 - 1))},
    ]

    # Upload + set ONLY the media slots that were provided.
    for kind, entries in (("image", args.image), ("video", args.video), ("audio", args.audio)):
        field_name = NODE[kind]["fieldName"]
        for spec in entries:
            node_id, path = parse_media_arg(spec)
            if not node_id or not path:
                print(f"ERROR: --{kind} must be <nodeId>:<path>, got: {spec!r}", file=sys.stderr)
                return 2
            filename = upload_file(path)
            node_info.append({"nodeId": node_id, "fieldName": field_name, "fieldValue": filename})
            print(f"  uploaded {kind} {node_id} <- {path}  ({filename})")

    # Reference images by registry <Picture N> (maps to @Image N slot).
    for spec in args.picture:
        pic_id, path = parse_media_arg(spec)
        node_id = PICTURE_TO_IMAGE_NODE.get(pic_id)
        if not node_id:
            print(f"ERROR: unknown Picture id: {pic_id!r} (valid: {sorted(PICTURE_TO_IMAGE_NODE)})", file=sys.stderr)
            return 2
        filename = upload_file(path)
        node_info.append({"nodeId": node_id, "fieldName": NODE["image"]["fieldName"], "fieldValue": filename})
        print(f"  uploaded Picture {pic_id} -> @Image {pic_id} (nodeId {node_id}) <- {path}  ({filename})")

    # Reference audio by slot N ({clip}-audio-N.mp3).
    for spec in args.audio_slot:
        slot, path = parse_media_arg(spec)
        node_id = AUDIO_SLOT_TO_NODE.get(slot)
        if not node_id:
            print(f"ERROR: unknown audio slot: {slot!r} (valid: {sorted(AUDIO_SLOT_TO_NODE)})", file=sys.stderr)
            return 2
        filename = upload_file(path)
        node_info.append({"nodeId": node_id, "fieldName": NODE["audio"]["fieldName"], "fieldValue": filename})
        print(f"  uploaded audio slot {slot} (nodeId {node_id}) <- {path}  ({filename})")

    if args.dry_run:
        print("=== DRY-RUN: submit payload (NOT submitted) ===")
        print(json.dumps({"workflowId": args.workflow_id, "nodeInfoList": node_info}, ensure_ascii=False, indent=2))
        return 0

    try:
        task_id = submit(workflow_id, node_info)
        print(f"submitted → taskId {task_id}")
        result = poll(task_id)
        if not result["ok"]:
            raise RhError(f"Task failed: {result.get('errorMessage') or result['status']}")

        outputs = get_outputs(task_id)
        if not outputs:
            raise RhError(f"Task succeeded but no outputs: {task_id}")

        out_dir = Path(args.output_dir)
        saved = []
        for i, o in enumerate(outputs):
            url = o.get("fileUrl") or o.get("url") or o.get("src") or o.get("downloadUrl") or o.get("videoUrl")
            if not url:
                continue
            ext = (Path(url.split("?")[0]).suffix) or ".mp4"
            dest = out_dir / f"rh_{task_id}_{i}{ext}"
            download(url, dest)
            saved.append(str(dest.resolve()))
            print(f"  saved {dest}")

        if args.json:
            print(json.dumps({"taskId": task_id, "files": saved}, ensure_ascii=False, indent=2))
        return 0
    except RhError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        if args.json:
            print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
