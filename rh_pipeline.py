#!/usr/bin/env python3
"""
RunningHub clip pipeline.

  1. analyze   scan a folder of prompt files + reference media
               -> write clips.json (prompt, duration, aspect, pictures, audio, seed)
  2. (confirm) review clips.json / fill missing aspect ratios
  3. run       batch/queue: run clips one-by-one, monitor, download + remux,
               collect results.json

Reuses rh_workflow_runner.py for the RunningHub API contract.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import rh_workflow_runner as R

MAX_DURATION = 15.0


# ---------------------------------------------------------------------------
# analyze
# ---------------------------------------------------------------------------
def parse_duration(text: str) -> float:
    m = re.search(r"(\d+(?:\.\d+)?)\s*[- ](?:second|sec|s)\b", text, re.I)
    if m:
        return min(float(m.group(1)), MAX_DURATION)
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:วินาที|วิ)\b", text)
    if m:
        return min(float(m.group(1)), MAX_DURATION)
    return MAX_DURATION


def scene_prefix(clip_id: str) -> str:
    """EP03-S01-C01 -> EP03-S01 (strip trailing -C01/-P02/-S01[a-z]?)."""
    return re.sub(r"-(?:C|P|S)\d+[a-z]?$", "", clip_id)


def find_image_file(base: Path, refs: Path, n: str) -> str | None:
    for d in (refs, base):
        for name in (f"Image-{n}", f"Picture-{n}"):
            for ext in (".png", ".jpg", ".jpeg"):
                p = d / f"{name}{ext}"
                if p.exists():
                    return str(p)
    return None


def analyze(args) -> int:
    try:
        R.load_workflow_config(args.config)
    except R.RhError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    global MAX_DURATION
    MAX_DURATION = R.MAX_DURATION
    base = Path(args.dir)
    refs = Path(args.refs_dir) if args.refs_dir else (base / "Refs")
    _non_prompt_exts = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp3", ".mp4", ".mov", ".wav", ".json", ".py", ".bak"}
    prompt_files = sorted(
        p for p in base.iterdir()
        if p.is_file()
        and not p.name.startswith(".")
        and p.suffix.lower() not in _non_prompt_exts
        and "MAPPING" not in p.name.upper()
        and "REGISTRY" not in p.name.upper()
    )
    if not prompt_files:
        print(f"ERROR: no prompt files found in {base}", file=sys.stderr)
        return 1

    clips = []
    asks: list[dict] = []
    for pf in prompt_files:
        cid = pf.stem
        prompt = pf.read_text(encoding="utf-8")

        # image refs: both `<Picture N>` (GM) and `@Image N` (NOK) -> slot N
        img_refs = set(re.findall(r"<Picture\s+(\d+)>", prompt)) | set(re.findall(r"@Image\s+(\d+)", prompt))
        images = []
        for n in sorted(img_refs):
            if n not in R.IMAGE_SLOT_TO_NODE:
                continue
            f = find_image_file(base, refs, n)
            if f:
                images.append({"slot": int(n), "file": f})
            else:
                asks.append({"clip": cid, "field": f"Image {n}", "reason": "file not found in Refs/"})

        audio = []
        for slot in sorted(R.AUDIO_SLOT_TO_NODE):
            found = None
            for prefix in (cid, scene_prefix(cid)):
                af = base / f"{prefix}-audio-{slot}.mp3"
                if af.exists():
                    found = str(af)
                    break
            if found:
                audio.append({"slot": int(slot), "file": found})

        aspect = R.detect_aspect(prompt)
        if not aspect:
            asks.append({"clip": cid, "field": "aspect", "reason": "not found in prompt"})

        clips.append({
            "id": cid,
            "prompt_file": str(pf),
            "duration": parse_duration(prompt),
            "aspect": aspect,
            "seed": random.randint(1, 2**63 - 1),
            "images": images,
            "audio": audio,
        })

    out = {
        "workflow_id": args.workflow_id or R.WORKFLOW_ID,
        "output_dir": args.output_dir,
        "clips": clips,
    }
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {args.out}: {len(clips)} clip(s)")
    for c in clips:
        print(f"  - {c['id']}: duration={c['duration']}s aspect={c['aspect'] or 'ASK'} "
              f"images={[i['slot'] for i in c['images']]} audio={[a['slot'] for a in c['audio']]}")
    if asks:
        print("⚠ needs user input:")
        for a in asks:
            print(f"  - [{a['clip']}] {a['field']}: {a['reason']}")
    return 0


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------
def remux_to_mp4(src: Path, dst: Path) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-c", "copy", "-movflags", "+faststart", str(dst)],
        check=True,
    )
    return dst


def run_clip(clip: dict, workflow_id: str, output_dir: Path) -> dict:
    prompt = Path(clip["prompt_file"]).read_text(encoding="utf-8")

    aspect = clip.get("aspect") or R.detect_aspect(prompt)
    if not aspect:
        raise R.RhError(f"aspect missing (add 'aspect' to clips.json or put it in the prompt)")

    node_info: list[dict] = [
        {"nodeId": R.NODE["prompt"]["nodeId"], "fieldName": R.NODE["prompt"]["fieldName"], "fieldValue": prompt},
        {"nodeId": R.NODE["duration"]["nodeId"], "fieldName": R.NODE["duration"]["fieldName"], "fieldValue": float(clip["duration"])},
        {"nodeId": R.NODE["aspect"]["nodeId"], "fieldName": R.NODE["aspect"]["fieldName"], "fieldValue": aspect},
        {"nodeId": R.NODE["seed"]["nodeId"], "fieldName": R.NODE["seed"]["fieldName"], "fieldValue": int(clip.get("seed") or random.randint(1, 2**63 - 1))},
    ]

    for img in clip.get("images", []):
        fn = R.upload_file(img["file"])
        node_info.append({"nodeId": R.IMAGE_SLOT_TO_NODE[str(img["slot"])], "fieldName": "image", "fieldValue": fn})
        print(f"  [image {img['slot']}] uploaded <- {img['file']}")
    for a in clip.get("audio", []):
        fn = R.upload_file(a["file"])
        node_info.append({"nodeId": R.AUDIO_SLOT_TO_NODE[str(a["slot"])], "fieldName": "audio", "fieldValue": fn})
        print(f"  [audio {a['slot']}] uploaded <- {a['file']}")

    task_id = R.submit(workflow_id, node_info)
    print(f"[{clip['id']}] submitted → taskId {task_id}")

    res = R.poll(task_id)
    if not res["ok"]:
        raise R.RhError(f"task FAILED: {res.get('errorMessage') or res['status']}")

    outputs = R.get_outputs(task_id)
    video = R.pick_video_output(outputs)
    if not video:
        raise R.RhError("no video output in results")

    url = (video.get("fileUrl") or video.get("url") or video.get("src")
           or video.get("downloadUrl") or video.get("videoUrl"))
    raw = output_dir / f"rh_{task_id}_0.mp4"
    R.download(url, raw)
    final = remux_to_mp4(raw, output_dir / f"{clip['id']}.mp4")
    print(f"[{clip['id']}] ✓ {final}")
    return {"id": clip["id"], "task_id": task_id, "file": str(final)}


def run(args) -> int:
    try:
        R.load_workflow_config(args.config)
    except R.RhError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    data = json.loads(Path(args.clips).read_text(encoding="utf-8"))
    clips = data.get("clips", [])
    if args.limit:
        clips = clips[: args.limit]
    workflow_id = args.workflow_id or data.get("workflow_id") or R.WORKFLOW_ID
    output_dir = Path(args.output_dir or data.get("output_dir") or "./rh-output")
    output_dir.mkdir(parents=True, exist_ok=True)

    # confirm
    print(f"workflow {workflow_id} · {len(clips)} clip(s):")
    for c in clips:
        print(f"  {c['id']}: {c['duration']}s, aspect={c.get('aspect') or 'ASK'}, "
              f"images={[i['slot'] for i in c.get('images', [])]}, audio={[a['slot'] for a in c.get('audio', [])]}")
    if not args.yes:
        if not sys.stdin.isatty():
            print("ERROR: confirm required — re-run with --yes after reviewing clips.json", file=sys.stderr)
            return 2
        ans = input(f"run {len(clips)} clip(s)? [y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            print("aborted")
            return 2

    results = []
    for clip in clips:
        try:
            results.append(run_clip(clip, workflow_id, output_dir))
        except Exception as e:
            print(f"[{clip['id']}] ✗ {e}", file=sys.stderr)
            results.append({"id": clip["id"], "error": str(e)})
            if args.stop_on_error:
                print("stopping on first error", file=sys.stderr)
                break

    results_file = args.results or "results.json"
    Path(results_file).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = sum(1 for r in results if "file" in r)
    print(f"done: {ok}/{len(clips)} succeeded → {results_file}")
    return 0 if ok == len(clips) else 1


def check(args) -> int:
    """Validate workflow_api.json and print the resolved node/slot map."""
    try:
        cfg = R.load_workflow_config(args.config)
    except R.RhError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(cfg, ensure_ascii=False, indent=2))
    else:
        print(R.describe_workflow())
    return 0


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(
        description="RunningHub clip pipeline (analyze → confirm → batch run)",
        epilog="Workflow topology (node IDs, slots, aspects) comes from workflow_api.json (--config / RUNNINGHUB_WORKFLOW_JSON).",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    pa = sub.add_parser("analyze", help="scan prompt files → clips.json")
    pa.add_argument("--dir", required=True, help="folder with prompt files")
    pa.add_argument("--refs-dir", default=None, help="folder with reference images (default: <dir>/Refs)")
    pa.add_argument("--out", default="clips.json")
    pa.add_argument("--workflow-id", default=None, help="override workflowId from config")
    pa.add_argument("--config", default=None, help="path to workflow_api.json (or RUNNINGHUB_WORKFLOW_JSON)")
    pa.add_argument("--output-dir", default="./rh-output")
    pa.set_defaults(func=analyze)

    pr = sub.add_parser("run", help="batch/queue run clips.json")
    pr.add_argument("clips", help="path to clips.json")
    pr.add_argument("--workflow-id", default=None, help="override workflowId from config")
    pr.add_argument("--config", default=None, help="path to workflow_api.json (or RUNNINGHUB_WORKFLOW_JSON)")
    pr.add_argument("--output-dir", default=None)
    pr.add_argument("--results", default=None, help="results.json output path")
    pr.add_argument("--limit", type=int, default=None, help="run only first N clips")
    pr.add_argument("--yes", action="store_true", help="skip interactive confirmation")
    pr.add_argument("--stop-on-error", action="store_true")
    pr.set_defaults(func=run)

    pc = sub.add_parser("check", help="validate workflow_api.json and print the resolved node map")
    pc.add_argument("--config", default=None, help="path to workflow_api.json (or RUNNINGHUB_WORKFLOW_JSON)")
    pc.add_argument("--json", action="store_true", help="print raw resolved config JSON")
    pc.set_defaults(func=check)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
