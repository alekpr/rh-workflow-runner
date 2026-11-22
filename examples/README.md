# Examples

Generic, self-contained examples (no real content). Use them to verify your
setup before running your own prompts.

## Files

| File | Purpose |
|---|---|
| `prompt.txt` | A minimal reference-to-video prompt with `@Image 1/2` refs, 5s, 9:16 |

## Try it

```bash
# 1. validate the bundled workflow config
python3 rh_pipeline.py check --config workflow_api.json

# 2. dry-run a single clip (uploads files + prints payload, does NOT submit)
python3 rh_workflow_runner.py \
  --config workflow_api.json \
  --prompt-file examples/prompt.txt \
  --duration 5 \
  --picture 1:/path/to/your/character.png \
  --picture 2:/path/to/your/background.png \
  --dry-run
```

Replace `/path/to/your/*.png` with your own reference images. Omit the
`--picture` flags if you only want to test text-to-video without references
(remember unused image nodes in the workflow must be muted).

## Pipeline flow

```bash
# a) analyze a folder of prompts → clips.json
python3 rh_pipeline.py analyze --dir ./examples --config workflow_api.json --out clips.json

# b) batch run
python3 rh_pipeline.py run clips.json --config workflow_api.json --yes
```
