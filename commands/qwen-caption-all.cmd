# sl:name qwen-caption-all
# sl:description Run the governed Fusion 2.3.3 / Projection 1.3.5 caption pipeline on a cached dataset and return a tar
# sl:output 5
# sl:setup-version 1
# sl:memcheck

_qwen_caption_paths() {
  export QCAP_ROOT="$SL_CACHE_DIR/qwen-caption"
  export QCAP_REPO="$QCAP_ROOT/repo"
  export QCAP_DATASETS="$QCAP_ROOT/datasets"
  export QCAP_RUNS="$QCAP_ROOT/runs"
  export QCAP_QWEN_WS="$QCAP_ROOT/envs/qwen3"
  export QCAP_VLLM_WS="$QCAP_ROOT/envs/qwen3-vllm"
  export QCAP_SAM3D_WS="$QCAP_ROOT/envs/sam3d-body"
}

_qwen_caption_validate_key() {
  local label="$1" value="$2"
  if [[ ! "$value" =~ ^[A-Za-z0-9._-]+$ ]]; then
    echo "ERROR: $label must contain only letters, numbers, dot, underscore, or dash: $value" >&2
    return 2
  fi
}

sl_prepare() {
  _qwen_caption_paths

  local dataset_key="$SL_ARG_1"
  local run_name="$SL_ARG_2"
  local ref="$SL_ARG_4"
  local dataset_dir="$QCAP_DATASETS/$dataset_key"
  local workspace_repo_link="$QCAP_QWEN_WS/qwen3-vl-captioning-validation"

  _qwen_caption_validate_key "dataset key" "$dataset_key" || return $?
  _qwen_caption_validate_key "run name" "$run_name" || return $?
  if [[ -z "$SL_ARG_3" ]]; then
    echo "ERROR: subject token cannot be empty" >&2
    return 2
  fi
  if [[ -z "$ref" ]]; then
    echo "ERROR: repository ref cannot be empty" >&2
    return 2
  fi
  if ((${#SL_EXTRA_ARGS[@]})); then
    echo "ERROR: qwen-caption-all does not accept pass-through arguments yet" >&2
    return 2
  fi
  if [[ ! -d "$dataset_dir/images" || ! -f "$dataset_dir/dataset.json" ]]; then
    echo "ERROR: cached dataset not found: $dataset_key" >&2
    echo "Import it first with: sl run qwen-caption-import <local-dir> $dataset_key" >&2
    return 2
  fi

  mkdir -p "$QCAP_ROOT" "$QCAP_RUNS" "$QCAP_QWEN_WS" "$(dirname "$QCAP_REPO")"
  if [[ ! -d "$QCAP_REPO/.git" ]]; then
    rm -rf "$QCAP_REPO"
    git init -q "$QCAP_REPO"
    git -C "$QCAP_REPO" remote add origin https://github.com/markwelshboy/qwen3-vl-captioning-validation.git
  fi

  echo "Fetching qwen caption repo ref: $ref"
  git -C "$QCAP_REPO" fetch --quiet --depth 1 --no-tags origin "$ref"
  git -C "$QCAP_REPO" checkout --quiet --detach FETCH_HEAD
  git -C "$QCAP_REPO" reset --hard --quiet FETCH_HEAD
  printf '%s\n' "$(git -C "$QCAP_REPO" rev-parse HEAD)" > "$SL_COMMAND_CACHE/repo.sha"

  # A few current workspace wrappers intentionally resolve the repository as
  # $QWEN_WORKSPACE_ROOT/qwen3-vl-captioning-validation. Keep one canonical
  # checkout in QCAP_REPO and expose it at that compatibility location.
  if [[ -e "$workspace_repo_link" && ! -L "$workspace_repo_link" ]]; then
    echo "ERROR: workspace repository compatibility path exists and is not a symlink: $workspace_repo_link" >&2
    return 2
  fi
  ln -sfn "$QCAP_REPO" "$workspace_repo_link"
}

sl_setup() {
  _qwen_caption_paths

  echo "Building/reusing Qwen Transformers+DWPose workspace"
  QWEN_WORKSPACE_ROOT="$QCAP_QWEN_WS" \
    bash "$QCAP_REPO/build_workspace.sh"

  echo "Building/reusing Qwen vLLM workspace"
  QWEN_VLLM_WORKSPACE_ROOT="$QCAP_VLLM_WS" \
    bash "$QCAP_REPO/build_vllm_workspace.sh"

  echo "Building/reusing isolated SAM3D workspace"
  SAM3D_WORKSPACE_ROOT="$QCAP_SAM3D_WS" \
    bash "$QCAP_REPO/build_sam3d_workspace.sh" --download
}

sl_run() {
  _qwen_caption_paths

  local dataset_key="$SL_ARG_1"
  local run_name="$SL_ARG_2"
  local subject_token="$SL_ARG_3"
  local requested_ref="$SL_ARG_4"
  local dataset_dir="$QCAP_DATASETS/$dataset_key"
  local image_dir="$dataset_dir/images"
  local run_dir="$QCAP_RUNS/$run_name"
  local caption_export="$run_dir/caption-export"
  local compose_label="8b-bf16-governance135"
  local repair_label="${compose_label}-repair1"
  local actual_sha dataset_sha provenance model_slug fusion_model_dir

  actual_sha="$(git -C "$QCAP_REPO" rev-parse HEAD)"
  dataset_sha="$(python3 - "$dataset_dir/dataset.json" <<'PY'
import json, sys
print(json.load(open(sys.argv[1], encoding='utf-8'))['sha256'])
PY
)"
  provenance="$run_dir/PODLETS_RUN_PROVENANCE.json"

  if [[ -d "$run_dir" && -n "$(find "$run_dir" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]; then
    if [[ ! -f "$provenance" ]]; then
      echo "ERROR: run directory already exists without Podlets provenance: $run_dir" >&2
      echo "Use a new run name rather than mixing results." >&2
      return 2
    fi
    if ! python3 - "$provenance" "$actual_sha" "$dataset_sha" <<'PY'
import json, sys
p=json.load(open(sys.argv[1], encoding='utf-8'))
expected_repo=sys.argv[2]
expected_dataset=sys.argv[3]
if p.get('repo_commit') != expected_repo:
    raise SystemExit(f"repository revision mismatch: run={p.get('repo_commit')} current={expected_repo}")
if p.get('dataset_sha256') != expected_dataset:
    raise SystemExit(f"dataset fingerprint mismatch: run={p.get('dataset_sha256')} current={expected_dataset}")
PY
    then
      echo "ERROR: refusing to mix a different code revision or dataset into existing run '$run_name'." >&2
      echo "Use a new run name for revision comparisons." >&2
      return 2
    fi
  else
    mkdir -p "$run_dir"
  fi

  python3 - "$provenance" "$dataset_key" "$dataset_sha" "$run_name" "$subject_token" "$requested_ref" "$actual_sha" "$compose_label" "$repair_label" <<'PY'
import json, pathlib, sys
from datetime import datetime, timezone
path=pathlib.Path(sys.argv[1])
payload={
    'schema': 1,
    'dataset_key': sys.argv[2],
    'dataset_sha256': sys.argv[3],
    'run_name': sys.argv[4],
    'subject_token': sys.argv[5],
    'requested_ref': sys.argv[6],
    'repo_commit': sys.argv[7],
    'fusion_version': '2.3.3',
    'projection_version': '1.3.5',
    'compose_run_label': sys.argv[8],
    'repair_run_label': sys.argv[9],
    'started_at': datetime.now(timezone.utc).astimezone().isoformat(),
}
path.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
PY
  cp -f "$dataset_dir/dataset.json" "$run_dir/DATASET_PROVENANCE.json"

  echo "Qwen caption run: $run_name"
  echo "Dataset: $dataset_key ($image_dir)"
  echo "Repository: $requested_ref -> $actual_sha"
  echo "Pipeline: Fusion 2.3.3 / Projection 1.3.5"

  echo "Running caption/governance regression tests"
  if ! (
    cd "$QCAP_REPO"
    "$QCAP_QWEN_WS/.venv/bin/python" -m unittest \
      tests.test_caption_evidence \
      tests.test_caption_lint \
      tests.test_caption_projection \
      tests.test_caption_projection_131 \
      tests.test_caption_projection_132 \
      tests.test_laterality_refine \
      tests.test_laterality_bilateral_guard \
      tests.test_compose_governance_133 \
      tests.test_compose_governance_134 \
      tests.test_compose_governance_135 \
      tests.test_compose_lint_repair_135 \
      -v
  ) > "$run_dir/preflight-tests.log" 2>&1; then
    cat "$run_dir/preflight-tests.log" >&2
    return 1
  fi

  echo "Stage: Analyze v2.1 (32B FP8 / vLLM)"
  QWEN_WORKSPACE_ROOT="$QCAP_VLLM_WS" \
    bash "$QCAP_REPO/run_analysis_v2_1_workspace.sh" \
      "$image_dir" \
      --models 32b-fp8 \
      --backend vllm \
      --output "$QCAP_RUNS" \
      --run-name "$run_name" \
      --recursive \
      --subject-token "$subject_token" \
      --detail balanced

  echo "Stage: DWPose"
  QWEN_WORKSPACE_ROOT="$QCAP_QWEN_WS" \
    bash "$QCAP_REPO/run_dwpose_workspace.sh" \
      "$image_dir" \
      --output "$run_dir/dwpose" \
      --recursive \
      --device auto

  echo "Stage: SAM3D"
  SAM3D_WORKSPACE_ROOT="$QCAP_SAM3D_WS" \
    bash "$QCAP_REPO/run_sam3d_probe_workspace.sh" \
      "$image_dir" \
      --dwpose-dir "$run_dir/dwpose" \
      --output "$run_dir/sam3d" \
      --bbox-source dwpose \
      --inference-type body \
      --no-save-mesh

  echo "Stage: Fusion v2.3"
  QWEN_WORKSPACE_ROOT="$QCAP_QWEN_WS" \
    bash "$QCAP_REPO/run_fusion_v2_3_workspace.sh" \
      "$run_dir" \
      --model 32b-fp8 \
      --dwpose-dir "$run_dir/dwpose" \
      --sam3d-dir "$run_dir/sam3d"

  echo "Stage: Fusion v2.3.1 laterality refinement"
  QWEN_WORKSPACE_ROOT="$QCAP_QWEN_WS" \
    bash "$QCAP_REPO/run_laterality_refine_workspace.sh" \
      "$run_dir" \
      --model 32b-fp8

  echo "Stage: Fusion v2.3.2 bilateral collision guard"
  QWEN_WORKSPACE_ROOT="$QCAP_QWEN_WS" \
  QWEN_REPO_ROOT="$QCAP_REPO" \
    bash "$QCAP_REPO/run_laterality_bilateral_guard_workspace.sh" \
      "$run_dir" \
      --model 32b-fp8

  echo "Stage: Fusion v2.3.3 signed depth refinement"
  QWEN_WORKSPACE_ROOT="$QCAP_QWEN_WS" \
    bash "$QCAP_REPO/run_signed_depth_refine_workspace.sh" \
      "$run_dir" \
      --model 32b-fp8

  model_slug="$(
    cd "$QCAP_REPO"
    "$QCAP_QWEN_WS/.venv/bin/python" - <<'PY'
from qwen_caption_validate.runner import model_slug, resolve_model_id
print(model_slug(resolve_model_id('32b-fp8')))
PY
  )"
  fusion_model_dir="$run_dir/fusion-v2.3.3/$model_slug"

  if [[ ! -d "$fusion_model_dir" ]]; then
    echo "ERROR: expected Fusion 2.3.3 output missing: $fusion_model_dir" >&2
    return 1
  fi

  echo "Stage: Projection 1.3.5 + Compose (8B BF16, fusion-safe)"
  QWEN_WORKSPACE_ROOT="$QCAP_QWEN_WS" \
    bash "$QCAP_REPO/run_compose_governance_135_workspace.sh" \
      "$run_dir" \
      --analysis-model 32b-fp8 \
      --fusion-dir "$fusion_model_dir" \
      --compose-model Qwen/Qwen3-VL-8B-Instruct \
      --backend transformers \
      --quantization none \
      --dtype bfloat16 \
      --detail balanced \
      --subject-token "$subject_token" \
      --variants fusion-safe \
      --run-label "$compose_label"

  echo "Stage: one-shot lint repair + final caption export"
  QWEN_WORKSPACE_ROOT="$QCAP_QWEN_WS" \
    bash "$QCAP_REPO/run_compose_lint_repair_135_workspace.sh" \
      "$run_dir" \
      --analysis-model 32b-fp8 \
      --compose-model Qwen/Qwen3-VL-8B-Instruct \
      --source-run-label "$compose_label" \
      --run-label "$repair_label" \
      --backend transformers \
      --quantization none \
      --dtype bfloat16 \
      --export-caption-dir "$caption_export"

  if [[ ! -f "$caption_export/caption_export.index.json" ]]; then
    echo "ERROR: caption export index missing: $caption_export/caption_export.index.json" >&2
    return 1
  fi

  echo "Caption export summary:"
  "$QCAP_QWEN_WS/.venv/bin/python" - "$caption_export/caption_export.index.json" "$image_dir" "$caption_export" <<'PY'
import json
import pathlib
import sys

index_path = pathlib.Path(sys.argv[1])
image_root = pathlib.Path(sys.argv[2])
export_root = pathlib.Path(sys.argv[3])
index = json.loads(index_path.read_text(encoding='utf-8'))
exts = {'.png', '.jpg', '.jpeg', '.webp', '.bmp'}
image_count = sum(1 for p in image_root.rglob('*') if p.is_file() and p.suffix.lower() in exts)
caption_count = sum(1 for p in export_root.rglob('*.txt') if p.is_file())
summary = {key: index.get(key) for key in ('matched', 'written', 'review_required', 'missing')}
print(json.dumps(summary, indent=2))
print(f"Images: {image_count}")
print(f"Exported caption files: {caption_count}")
if index.get('missing'):
    raise SystemExit(f"caption export reports missing records: {index.get('missing')}")
if caption_count != image_count:
    raise SystemExit(f"caption/image count mismatch: captions={caption_count} images={image_count}")
PY

  python3 - "$provenance" <<'PY'
import json, pathlib, sys
from datetime import datetime, timezone
p=pathlib.Path(sys.argv[1])
data=json.loads(p.read_text(encoding='utf-8'))
data['completed_at']=datetime.now(timezone.utc).astimezone().isoformat()
p.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
PY

  echo "Packing persistent run: $run_dir"
  tar -C "$QCAP_RUNS" -cf "$SL_ARG_5" "$run_name"
  echo "Persistent run retained: $run_dir"
  echo "Final captions retained: $caption_export"
  echo "Archive ready: $SL_ARG_5"
}
