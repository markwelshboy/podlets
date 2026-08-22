# sl:name qwen-caption-import
# sl:description Import a local caption-validation dataset once into the persistent qwen-caption cache
# sl:input 1
# sl:setup-version 1

sl_run() {
  local source="$SL_ARG_1"
  local dataset_key="${SL_ARG_2:-}"
  local root="$SL_CACHE_DIR/qwen-caption/datasets"
  local dest tmp

  if [[ -z "$dataset_key" ]]; then
    echo "ERROR: dataset key is required" >&2
    echo "Usage: sl run qwen-caption-import <local-directory> <dataset-key>" >&2
    return 2
  fi
  if [[ ! "$dataset_key" =~ ^[A-Za-z0-9._-]+$ ]]; then
    echo "ERROR: dataset key must contain only letters, numbers, dot, underscore, or dash: $dataset_key" >&2
    return 2
  fi

  dest="$root/$dataset_key"
  tmp="$root/.${dataset_key}.import-${SL_JOB_ID}"

  if [[ ! -d "$source" ]]; then
    echo "ERROR: qwen-caption-import expects a directory input: $source" >&2
    return 2
  fi
  if [[ -e "$dest" ]]; then
    echo "ERROR: dataset already exists in persistent cache: $dest" >&2
    echo "Use a new dataset key (or remove the cached dataset intentionally before re-importing)." >&2
    return 2
  fi

  rm -rf "$tmp"
  mkdir -p "$tmp/images"
  cp -a -- "$source"/. "$tmp/images"/

  python3 - "$dataset_key" "$tmp/images" "$tmp/dataset.json" <<'PY'
import hashlib
import json
import pathlib
import sys
from datetime import datetime, timezone

key = sys.argv[1]
root = pathlib.Path(sys.argv[2])
out = pathlib.Path(sys.argv[3])
image_exts = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
files = sorted(p for p in root.rglob("*") if p.is_file())
images = [p for p in files if p.suffix.lower() in image_exts]

digest = hashlib.sha256()
for path in files:
    rel = path.relative_to(root).as_posix()
    digest.update(rel.encode("utf-8"))
    digest.update(b"\0")
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)

payload = {
    "schema": 1,
    "dataset_key": key,
    "imported_at": datetime.now(timezone.utc).astimezone().isoformat(),
    "file_count": len(files),
    "image_count": len(images),
    "sha256": digest.hexdigest(),
    "images": [p.relative_to(root).as_posix() for p in images],
}
out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
print(f"Imported dataset {key}: {len(images)} image(s), {len(files)} total file(s)")
print(f"Dataset fingerprint: {payload['sha256']}")
PY

  mv "$tmp" "$dest"
  echo "Persistent dataset: $dest/images"
}
