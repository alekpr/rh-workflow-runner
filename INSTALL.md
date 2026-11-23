# rh-workflow-runner — Agent Install Guide

> You are an AI agent. Read this entire file, then execute the steps in order.
> If any step fails or a tool is missing, STOP that step, explain the problem to
> the user, and recommend the exact fix. Never invent commands or fabricate
> success. Never ask the user to paste their API key into chat.

## 0. What this installs

Two Python CLI scripts plus an agent skill that run a RunningHub ComfyUI
workflow through the REST API, driven by a `workflow_api.json` config.

- `rh_workflow_runner.py` — single clip (upload → submit → poll → download)
- `rh_pipeline.py` — pipeline (`check`, `analyze`, `run`)
- `workflow_api.json` — workflow topology (workflowId + node map)
- `skills/rh-workflow-runner/` — the skill and its supporting references for
  other agents

## 1. Detect your platform (skill install location)

Pick the correct skill directory for the agent you are:

| Agent | Skill directory |
|---|---|
| VS Code Copilot | `<project>/.copilot/skills/rh-workflow-runner/` or `~/.copilot/skills/rh-workflow-runner/` |
| Claude Code | `<project>/.claude/skills/rh-workflow-runner/` or `~/.claude/skills/` |
| Other / generic | `skills/rh-workflow-runner/` (or wherever existing `SKILL.md` files live) |

If unsure, look for existing `**/SKILL.md` files on the machine and install
next to them using the same convention.

## 2. Get the files

```bash
git clone --depth 1 https://github.com/alekpr/rh-workflow-runner.git /tmp/rh-workflow-runner
```

## 3. Install the scripts

```bash
mkdir -p ~/rh-workflow-runner
cp /tmp/rh-workflow-runner/rh_workflow_runner.py ~/rh-workflow-runner/
cp /tmp/rh-workflow-runner/rh_pipeline.py        ~/rh-workflow-runner/
cp /tmp/rh-workflow-runner/workflow_api.json     ~/rh-workflow-runner/
```

## 4. Install the skill

```bash
SKILL_DIR="<skill-dir-from-step-1>"
mkdir -p "$SKILL_DIR/rh-workflow-runner"
cp -R /tmp/rh-workflow-runner/skills/rh-workflow-runner/. "$SKILL_DIR/rh-workflow-runner/"
```

## 5. Verify prerequisites

```bash
python3 --version          # must be ≥ 3.10
ffmpeg -hide_banner -version
ffprobe -hide_banner -version
```

Both `ffmpeg` and `ffprobe` must resolve on `PATH`. The Homebrew, Debian, and
WinGet packages below install both commands.

### Why ffmpeg is needed

`rh_pipeline.py run` uses ffmpeg after downloading each batch result. It remuxes
the downloaded stream into a fast-start MP4 so the file can begin playback
before the entire file is downloaded:

```bash
ffmpeg -y -v error -i input.mov -c copy -movflags +faststart output.mp4
```

`-c copy` copies the existing audio/video streams without re-encoding, so this
step is normally fast and does not reduce image quality. `rh_pipeline.py check`,
`rh_pipeline.py analyze`, and the single-clip `rh_workflow_runner.py` do not
invoke ffmpeg.

### Install ffmpeg

macOS with [Homebrew](https://formulae.brew.sh/formula/ffmpeg):

```bash
brew install ffmpeg
```

Debian/Ubuntu:

```bash
sudo apt update
sudo apt install -y ffmpeg
```

Windows PowerShell with WinGet:

```powershell
winget install --id Gyan.FFmpeg --exact
```

Close and reopen the terminal after a Windows installation so the updated
`PATH` and command aliases are loaded. If a package manager is unavailable,
use a compiled build linked from the official
[FFmpeg download page](https://ffmpeg.org/download.html), then add its `bin`
directory to `PATH`.

Python is also required. On macOS install it with `brew install python`; on
Linux or Windows use the platform's package manager and verify that
`python3 --version` reports 3.10 or newer.

### Verify PATH and test remuxing

macOS/Linux:

```bash
command -v ffmpeg
command -v ffprobe
```

Windows PowerShell:

```powershell
where.exe ffmpeg
where.exe ffprobe
```

To test with an existing workflow output without re-encoding it:

```bash
ffmpeg -y -v error -i input.mov -c copy -movflags +faststart output.mp4
ffprobe -v error -show_entries format=duration,size \
  -of default=noprint_wrappers=1 output.mp4
```

Replace `input.mov` with a real local input file. Do not claim the remux test
passed unless `ffmpeg` exits with code 0 and `ffprobe` prints the output
metadata.

## 6. API key

The user must create an API key at RunningHub (RunningHub → API keys).

**Do NOT ask them to paste the key into chat.** Instruct them to run this
themselves in their terminal and add it to their shell profile:

```bash
export RUNNINGHUB_API_KEY="<their-key>"
# macOS/Linux: append to ~/.zshrc or ~/.bashrc so it persists
```

## 7. Verify the install

```bash
python3 ~/rh-workflow-runner/rh_pipeline.py check \
  --config ~/rh-workflow-runner/workflow_api.json
```

Success = it prints a table (`workflowId`, `nodes`, `image slots`, `audio slots`,
`aspects`) with no `ERROR` line.

## 8. Report back to the user

Summarize in the user's language:
1. Where the scripts were installed (`~/rh-workflow-runner/`)
2. Where the skill was installed
3. Which prerequisites were already present vs installed
4. That the API key must be exported by the user (with the exact command)
5. A first runnable command, e.g.:
   ```bash
   python3 ~/rh-workflow-runner/rh_workflow_runner.py \
     --config ~/rh-workflow-runner/workflow_api.json \
     --prompt-file examples/prompt.txt \
     --duration 5 \
     --dry-run
   ```

## If anything goes wrong

- `Invalid workflow config` → run `check` and fix the exact fields it lists.
- `RUNNINGHUB_API_KEY is not set` → the user has not exported the key yet.
- `ffmpeg: command not found` or `ffprobe: command not found` → install the
  package for the current platform, reopen the terminal, and repeat the PATH
  checks in step 5.
- ffmpeg reports that a stream is unsupported by the MP4 container → the
  downloaded codec cannot be copied into MP4. Preserve the original file and
  report the exact ffmpeg error; do not silently re-encode it.
- `Required input is missing` from ComfyUI → an empty loader node in the
  published workflow is not muted; the user must mute it in the workflow editor.
- Wrong upload endpoint (`openapi/...` path not found) → always use
  `/task/openapi/upload`, never `/openapi/v2/media/upload/binary`.
