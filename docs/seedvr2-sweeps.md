# SeedVR2 restoration sweeps

The `seedvr2-sweep` command stages one unsorted image directory to the GPU worker, lets `seedvr2-sweep` in `seedvr2-tile` bucket/sample/run the experiment there, then fetches the entire result/report bundle back through the normal Podlets output path.

The experiment deliberately fixes the model to SeedVR2 **3B FP8** so model choice does not become another sweep dimension.

## Initial coarse sweep

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

The default coarse experiment selects a deterministic spread of 3 images from each megapixel bucket and holds synthetic noise at zero. For each bucket it sweeps three pre-resize settings against three reconstruction scales. The resulting bundle contains the processed PNGs, `manifest.json`, `results.csv`, an `index.html` report, full-image comparison sheets, and same-location normalized crop sheets.

## Inspect the plan first

```bash
SEEDVR2_TILE_REF=agent/sweep-harness \
sl run seedvr2-sweep \
  ~/images/lowlight/ \
  seedvr2_sweep_plan/ \
  --output-dir ~/results \
  -- \
  --plan-only
```

This inventories and buckets the uploaded images and writes the selected sample/parameter plan without running SeedVR2 inference.

## Narrow noise refinement

Once the first contact sheets identify a useful resize/scale region, add a small Gaussian-noise axis instead of paying for it in the first broad search:

```bash
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
--crop-fraction 0.30
--cell-size 320
--fbcnn
--jpeg-quality auto
--seed 42
--strict
```

`--max-output-mp 0` disables the safety cap. A pre-resize target that would actually enlarge a particular source image is automatically skipped rather than treated as a downscale experiment.

## Job behavior

This remains a normal throwaway Podlets job:

- the source directory is uploaded once;
- the sweep and report generation run on the worker;
- the declared output directory is fetched after success;
- normal Podlets cleanup policy applies to the heavy remote job workspace.

By default the sweep records individual variant failures in the report and continues so a mostly successful experiment is still fetched. Add `--strict` if any failed variant should make the whole Podlets job fail.
