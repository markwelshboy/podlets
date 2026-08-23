# sl:name seedvr2-sweep
# sl:description Bucket/sample/sweep SeedVR2 restoration settings and build comparison sheets
# sl:input 1
# sl:output 2
# sl:setup-version 1
# sl:memcheck

sl_prepare() {
  local cache="$SL_CACHE_DIR/seedvr2-tile"
  local repo="$cache/repo"
  local venv="$cache/venv"
  local branch="${SEEDVR2_TILE_REF:-main}"
  local py="${PYTHON:-python3}"

  mkdir -p "$cache"
  if [[ ! -d "$repo/.git" ]]; then
    git clone --depth 1 --single-branch --branch "$branch" \
      https://github.com/markwelshboy/seedvr2-tile.git "$repo"
  else
    git -C "$repo" fetch --depth 1 origin "$branch"
    git -C "$repo" checkout --detach FETCH_HEAD
  fi

  if [[ ! -x "$venv/bin/python" ]]; then "$py" -m venv "$venv"; fi
  source "$venv/bin/activate"
  unset PIP_CONSTRAINT PIP_BUILD_CONSTRAINT
  python -m pip install -e "$repo"
}

sl_setup() {
  local venv="$SL_CACHE_DIR/seedvr2-tile/venv"
  source "$venv/bin/activate"
  unset PIP_CONSTRAINT PIP_BUILD_CONSTRAINT
  seedvr2-tile setup --install-deps --fbcnn
}

sl_run() {
  local repo="$SL_CACHE_DIR/seedvr2-tile/repo"
  local venv="$SL_CACHE_DIR/seedvr2-tile/venv"
  source "$venv/bin/activate"
  cd "$repo"
  seedvr2-sweep "$SL_ARG_1" "$SL_ARG_2" "${SL_EXTRA_ARGS[@]}"
}
