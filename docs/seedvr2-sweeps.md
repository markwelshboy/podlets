# SeedVR2 restoration sweeps

The `seedvr2-sweep` Podlet stages one unsorted image directory to the GPU worker, runs the probe-first restoration experiment from `seedvr2-tile`, then fetches the entire comparison/report bundle back through the normal Podlets output path.

The experiment fixes the model to SeedVR2 **3B FP8** so model choice does not become another sweep dimension.

## Probe-first coarse sweep

After both feature branches are merged:

```bash
sl run --mem 18G seedvr2-sweep \
  ~/images/lowlight/ \
  seedvr2_sweep/ \
  --output-dir ~/results
```

While testing the SeedVR2 feature branch before it is merged, point the cached checkout at that branch:

```bash
SEEDVR2_TILE_REF=agent/sweep-harness \
sl run --mem 18G seedvr2-sweep \
  ~/images/lowlight/ \
  seedvr2_sweep/ \
  --output-dir ~/results
```

The source directory is uploaded once. On the worker, the sweep:

1. buckets and samples source images;
2. preprocesses the **full source image** for each candidate pre-MP/noise setting;
3. creates the normal SeedVR2 spatial tile grid after preprocessing;
4. selects up to 3 representative probe tiles (`detail`, `dark`, `center`);
5. maps those normalized probe locations onto each candidate's actual tile grid;
6. sends only the selected processing tiles to SeedVR2;
7. builds per-probe comparison sheets whose first column is the actual post-preprocess input core.

This avoids the invalid shortcut of cropping a source and then independently resizing that crop to the same absolute megapixel target as the full image.

GPU work is deduplicated by source/preprocessing/tile/backend-resolution. In particular, requested scales that hit the same SeedVR2 tile-resolution cap can reuse one inference result. Unique probe tiles are grouped by backend resolution so Numz's normal DiT/VAE caching remains useful within each group.

## Inspect probe selection first

```bash
SEEDVR2_TILE_REF=agent/sweep-harness \
sl run seedvr2-sweep \
  ~/images/lowlight/ \
  seedvr2_sweep_plan/ \
  --output-dir ~/results \
  -- \
  --plan-only
```

This inventories and buckets the uploaded images and records the selected normalized probe locations without SeedVR2 inference.

## Default coarse matrix

The default source sample is 3 images per megapixel bucket. Synthetic noise starts at zero.

```text
small   (<1.25 MP):      native, 0.50, 0.75 MP
medium  (1.25-4.0 MP):   native, 0.75, 1.00 MP
large   (>4.0 MP):       1.00, 1.50, 2.00 MP
scales:                   1.5x, 2x, 3x
probe tiles:              up to 3
model:                    3B FP8
```

A pre-resize target that would enlarge a source is skipped. Candidate full-image outputs predicted to exceed 20 MP are skipped by default.

## Narrow noise refinement

Once the first contact sheets identify a useful resize/scale region, add a small Gaussian-noise axis instead of paying for it in the first broad search:

```bash
SEEDVR2_TILE_REF=agent/sweep-harness \
sl run --mem 18G seedvr2-sweep \
  ~/images/lowlight/ \
  seedvr2_medium_noise/ \
  --output-dir ~/results \
  -- \
  --only-bucket medium \
  --pre-medium 0.75,1.0 \
  --scales 1.5,2 \
  --noise-values 0,0.005,0.01
```

## Useful overrides

Everything after `--` is passed directly to `seedvr2-sweep`:

```text
--samples-per-bucket N
--all-images
--only-bucket small|medium|large
--small-max MP
--medium-max MP
--pre-small native,0.5,0.75
--pre-medium native,0.75,1.0
--pre-large 1.0,1.5,2.0
--scales 1.5,2,3
--noise-values 0,0.005,0.01
--max-output-mp 20
--probe-tiles 3
--cell-size 320
--fbcnn
--jpeg-quality auto
--seed 42
```

`--max-output-mp 0` disables the full-image output-size safety cap.

## Job behavior

This remains a normal throwaway Podlets job:

- the source directory is uploaded once;
- full-image preprocessing, probe inference and report generation run on the worker;
- the declared output directory is fetched after success;
- normal Podlets cleanup policy applies to the heavy remote job workspace.

The returned `manifest.json` records the requested comparison-cell count versus the number of unique SeedVR2 tile inferences actually required. `results.csv` records the exact full-image preprocessing dimensions, tile count, selected tile index and backend resolution behind every comparison cell.
