# Photo Manager

A self-hosted application for exploring large, messy photo collections. It finds exact duplicates, near-duplicates and images that carry the same text, and helps you clean them up safely.

Photo Manager scans a folder of photos (hundreds of thousands of files, from any mix of phones, cameras, messaging apps and downloads) and shows each photo's metadata in a web interface. It then groups photos that are copies or edits of the same image, even when file names, formats, dates and metadata differ. It also reads the text inside images, so you can search photos by the words written in them.

Everything runs on your own machine. Your photos are never uploaded, modified or deleted; they are only *moved* when you explicitly send duplicates to quarantine, and every move can be undone.

> [!WARNING]
> **Work in progress.** Photo Manager is under active development and has not yet been validated on large real-world libraries. Expect rough edges, changing defaults and breaking changes to the local database format between versions. It never modifies or deletes your original files and every quarantine move can be undone, but review its suggestions before acting on them, and keep a backup of your photos.

---

## Contents

- [Features](#features)
- [How it works](#how-it-works)
- [Privacy and safety](#privacy-and-safety)
- [Getting started](#getting-started)
- [Usage](#usage)
- [Configuration](#configuration)
- [Performance](#performance)
- [Development](#development)
- [Project status and roadmap](#project-status-and-roadmap)

---

## Features

- **Library overview:** dashboards for formats, capture years, cameras, likely source (WhatsApp, screenshots, camera…), metadata coverage and reclaimable disk space.
- **Metadata explorer:** EXIF (camera, lens, software, orientation, GPS), capture date with its origin (EXIF, file name or modification time), file system dates and content fingerprints for every photo.
- **Duplicate and similarity detection:** five complementary analysis methods, fused into graded groups. Each group shows *why* its photos were matched.
- **Text in images (OCR):** accent- and case-insensitive full-text search, plus grouping of different images that share the same text.
- **Visual search:** describe a picture in plain language ("dog on a beach") and find matching photos.
- **Review workflow:** compare group members side by side with their differences highlighted, mark *keep* / *discard*, and auto-select the best copy.
- **Organize:** create an organized copy of your library, either flat or with one folder per group, with collision-free renaming.
- **Quarantine with undo:** move discarded photos out of your library from inside the app. Nothing is deleted, the last copy of a picture is always kept, and each batch can be restored with one click. Groups and counts update immediately.
- **Auto Process:** saved pipelines that refresh the library, keep the best copy of every exact/duplicate group, write an organized copy and quarantine the rest in one run, with a dry-run preview.
- **Enhance:** merge the copies of a picture into one improved image (alignment + robust multi-image fusion), optionally upscaled by a neural network (Real-ESRGAN), shown with a before/after slider.
- **Exports:** CSV / JSON reports of every group and decision.
- **Broad format support:** JPEG, PNG, GIF, WebP, BMP, TIFF, HEIC/HEIF, AVIF and camera RAW (CR2, CR3, NEF, ARW, DNG, ORF, RW2, RAF…). Files with wrong or missing extensions are identified by their content.

---

## How it works

Photo Manager runs a resumable, incremental pipeline in the background. The UI is usable while it runs, and each stage only processes what changed since the last scan.

```
 Scan ─► Analyse ─► CLIP ─► OCR ─► Candidate pairs ─► Grouping
  │         │         │      │           │                │
  │         │         │      │           │                └─ tiers: exact / duplicate / edited / similar / same text
  │         │         │      │           └─ hash + vector nearest-neighbour search, ORB verification
  │         │         │      └─ text in images (most text-likely images first)
  │         │         └─ semantic image embeddings
  │         └─ SHA-256, metadata, perceptual hashes, thumbnail (one read per file)
  └─ recursive folder walk; detects new, changed and removed files
```

### Analysis methods

| Method | What it detects | Notes |
|---|---|---|
| **SHA-256** | Byte-identical files, whatever their name, folder or date | Exact |
| **Perceptual hashes** (pHash, dHash, wHash, colour hash) | Re-saved, recompressed, resized or format-converted copies (e.g. JPG ↔ PNG) | Also computed on rotated/mirrored variants and with caption bars/borders removed |
| **CLIP embeddings** (ViT-B-32) | Crops, filters, overlays and the same scene; also powers visual search | Neural network, runs on CPU |
| **ORB keypoints + RANSAC** | Geometric proof that two images share content (crop, rotation, watermark) | Ignores text regions, so a shared caption alone is never treated as proof |
| **OCR** (RapidOCR: PaddleOCR models on ONNX Runtime) | Text in memes, screenshots, documents and signs | Latin-script languages, including accented characters |

### Similarity tiers

The scores from each method are combined into five tiers, from strictest to loosest:

| Tier | Meaning | Typical example |
|---|---|---|
| **Exact copies** | Identical bytes | The same file saved in two folders with different dates |
| **Duplicates** | The same picture, re-encoded | A JPEG and its PNG conversion; a resized or rotated copy |
| **Edited versions** | The same picture, geometrically confirmed | A crop; a meme with a caption added |
| **Similar** | Visually or semantically alike, *unverified* | Burst shots; the same scene from another angle |
| **Same text** | Different pictures with the same words | The same meme caption on another photo |

Duplicates are merged transitively (union-find). The looser tiers use star clustering, so every member must be directly similar to the group's centre, and chains of loosely related photos are not merged. The best copy in each group is chosen by resolution, presence of a real capture date and metadata, then age.

All thresholds can be adjusted in **Settings**, either with a single **similarity level** slider (1 = only near-identical images, 5 = default, 10 = very permissive) or one by one, and grouping can be re-run without re-analysing any photos.

---

## Privacy and safety

- **Your originals are never modified or deleted.** Scanning, analysis, Organize and Enhance only read the folders you scan; an automated test checks that the source folder is byte-for-byte identical after a full run.
- **Moves happen only when you ask, and can be undone.** The only operation that touches your folders is **Move to quarantine** (manually, or as the last step of an Auto Process run):
  - files are *moved* into a quarantine folder you choose, keeping their folder structure; nothing is ever deleted or overwritten
  - every move is logged in the database *before* it happens, and a `manifest.csv` is written for each batch
  - the last remaining copy of a group is never moved, and a file that changed since the last scan is skipped
  - **Restore** puts a batch back exactly where it was (same name, same dates); if something now occupies the original path, that file stays in quarantine instead of overwriting it
  - deleting the quarantine folder for good is left to you
- **Local only.** The database, thumbnails, embeddings, OCR text and exports are stored in `./data/`, which is excluded from version control. The server listens on `127.0.0.1` only.
- **One-time model downloads.** On first use, the CLIP model (~600 MB, from Hugging Face) and the OCR models (~15 MB, from ModelScope) are downloaded into `data/models/`. The Enhance upscaling models (5 MB / 64 MB) are downloaded from the official Real-ESRGAN GitHub releases the first time you use them, and verified by SHA-256. No photos or metadata are ever sent anywhere.
- **Decisions are just decisions.** Marking a photo *discard* moves nothing until you confirm **Move to quarantine**. **Organize** and **Enhance** only write new files, outside your scanned folders.
- **Automation is limited to safe cases.** Auto Process only decides *exact* and *duplicate* groups, never overrides a decision you made, and offers a dry run that changes nothing.

> `./data/` holds private information about your collection: file paths, GPS coordinates and text read from your photos. Do not commit or share it.

---

## Getting started

### Requirements

- Linux (developed and tested on Linux; macOS should work but is untested; Windows is untested)
- [uv](https://docs.astral.sh/uv/) (installs Python 3.12 into an isolated environment for you)
- Node.js 22 LTS
- About 1 GB of disk space for models, plus roughly 10–15 KB per photo for thumbnails and index data
- No GPU required

### Installation

```bash
git clone <repository-url> photo-manager
cd photo-manager
./scripts/setup.sh
```

`setup.sh` creates an isolated virtual environment in `backend/.venv` and installs the Python and JavaScript dependencies. Your system Python is not touched. The manual equivalent is:

```bash
cd backend
uv python install 3.12
uv venv --python 3.12 .venv
uv sync                    # PyTorch is installed from the CPU-only index
cd ../frontend
npm install
```

### Running

```bash
./scripts/start.sh         # builds the UI and serves everything at http://127.0.0.1:8000
```

For development with hot reload:

```bash
./scripts/dev.sh           # API on :8000, UI on http://localhost:5173
```

---

## Usage

1. **Add a folder.** Open **Folders & scan**, click **Add folder** and browse to your photo collection. All sub-folders are included at any depth; hidden folders (starting with `.`) are skipped. You can add several folders.
   - **Removing a folder** deletes everything the app derived from it: its photos, their duplicate-group memberships and keep/discard marks. It also removes thumbnails, OCR text and match data for any content that existed only in that folder. Groups are rebuilt, so photos in other folders are no longer listed as duplicates of the removed ones. The files on disk are not touched.
2. **Scan.** Click **Scan now**. CLIP and OCR can be switched off for a quick first pass that finds exact copies and duplicates only. A stopped scan resumes where it left off, and later scans only process new or changed files.
3. **Explore.** Use **Overview** for the big picture, **Library** to filter and sort, and the photo page for full metadata, fingerprints, recognised text and similar photos.
4. **Review duplicates.** Under **Duplicates**, open a group to compare its members side by side. Mark photos *keep* or *discard*, or use **Keep best, discard rest**.
5. **Search.** Look for words inside images (*Text in image*: accents ignored, `"quoted phrases"` for exact matches) or describe what a photo shows (*Visual content*).
6. **Act on the results.**
   - **Organize:** create a copy that is flat or has one folder per group, keeping original names or renaming photos by date or group number. A preview shows every destination path before anything is copied, and each run writes a `manifest.csv`.
   - **Move to quarantine:** from **Duplicates**, a group page or **Quarantine**, move every photo marked *discard*. The preview shows how many files move, how much space is freed and which photos are kept because they are the last copy. Counts and groups update as soon as the move ends. **Quarantine** lists every batch with a **Restore** button.
   - **Enhance:** on a group page (or a photo page) press **✨ Enhance**. The copies are aligned onto the best one and merged, which removes noise and compression artefacts they don't share; the result can then be upscaled ×1/×2/×4 by Real-ESRGAN (*Fast* or *Best quality*). The time estimate is shown first: on a 4-core CPU the fast model needs roughly 45 s per megapixel. Results are listed under **Enhanced**. Neural upscaling invents plausible detail: the result is for viewing and sharing, not a recovered original.
   - **Settings → Export:** produce a CSV/JSON report of every group and decision.
7. **Automate.** **Auto Process** runs a saved pipeline in one go:
   1. *Refresh*: a quick check for moved/deleted files, or a full analysis of new photos.
   2. *Decide*: keep the best copy in every undecided exact/duplicate group.
   3. *Organize*: write an organized copy of everything not discarded. This happens before anything moves, so a full disk stops the run early.
   4. *Quarantine*: move the copies this run discarded.

   Three presets are included (*Remove exact duplicates*, *Clean & organize*, *Organize only*); edit them or save your own. **Dry run** shows what each step would do without changing anything.
8. **Files changed outside the app?** If you moved or deleted photos in your file manager, click **Check for moved/deleted files** on **Folders & scan**. It only checks file system entries, so it takes seconds, and the groups are updated.

---

## Configuration

Settings are read from environment variables at start-up:

| Variable | Default | Purpose |
|---|---|---|
| `PM_DATA_DIR` | `./data` | Where the database, thumbnails, embeddings and exports are stored |
| `PM_MODEL_DIR` | `$PM_DATA_DIR/models` | Where CLIP and OCR models are cached |
| `PM_WORKERS` | number of CPU cores | Worker processes for analysis, OCR and verification |

Similarity thresholds (hash distances, CLIP similarity, ORB keypoint counts, OCR word overlap) can be changed at runtime from the **Settings** page.

If you point `PM_DATA_DIR` at a folder inside the repository other than `./data`, add it to `.gitignore`.

---

## Performance

Indicative first-run times for **~300,000 photos on a 4-core CPU without a GPU**:

| Stage | Throughput | Approx. time |
|---|---|---|
| Metadata, hashes and thumbnails | ~110 ms per 12 MP JPEG per core | 2–3 h |
| CLIP embeddings | ~11 images/s | ~7 h |
| OCR | ~0.4 s (no text) to ~1 s (with text) per image per core | 8–10 h |
| Candidate search and grouping | — | ~15 min |

These figures come from benchmarks on synthetic images. Real-world throughput depends on image sizes, formats and storage speed. Subsequent scans are incremental and usually take minutes. OCR processes the images most likely to contain text first (as judged by CLIP), so text search becomes useful early in the run.

---

## Development

### Tech stack

- **Backend:** Python 3.12, FastAPI, SQLite (WAL, FTS5), Pillow, pillow-heif, rawpy, ImageHash, OpenCV, faiss, PyTorch + open_clip, RapidOCR / ONNX Runtime
- **Frontend:** React 18, TypeScript, Vite, TanStack Query, react-virtuoso

### Project structure

```
backend/
  app/
    scanner.py      folder walk and parallel per-file analysis
    hashing.py      SHA-256, perceptual hashes, thumbnails
    metadata.py     EXIF, dates, GPS, source detection
    decode.py       unified image decoding (incl. HEIC and RAW)
    embed.py        CLIP embeddings and visual search
    ocr.py          OCR, text normalisation, full-text search, same-text pairs
    candidates.py   candidate pair search and score fusion
    verify.py       ORB geometric verification
    grouping.py     similarity tiers
    organize.py     organized copy: planning, naming, copying, manifest
    quarantine.py   move discarded photos to quarantine, restore, last-copy protection
    autoprocess.py  Auto Process pipelines (refresh → decide → organize → quarantine)
    enhance.py      Enhance: alignment, robust multi-image fusion, output
    sr_models.py    Real-ESRGAN networks (CPU) and verified weight download
    maintenance.py  folder removal and start-up self-repair
    jobs.py         background pipeline with progress and ETA
    api/            HTTP API (FastAPI routers)
  tests/            pytest suite and fixture generator
frontend/src/       React application (pages, components, API client)
scripts/            setup, start and development scripts
data/               runtime data (not versioned)
```

### Tests

```bash
cd backend
.venv/bin/pytest -q
```

The suite generates a fixture library covering the cases the app must handle:
- an exact copy with a different date
- a recompressed copy, a PNG conversion, a resized copy and a rotated copy
- a crop
- a meme with a caption, and the same caption on a different picture
- a Portuguese screenshot
- unrelated look-alikes
- a file with a misleading extension and a corrupt file

The tests run the full pipeline through the HTTP API. They check:
- grouping into the right tiers
- text search
- metadata extraction
- incremental rescans
- the Organize output
- that the source files are left untouched by every read-only feature
- quarantine and restore: identical content and dates, last-copy protection, no overwrite on conflicts, cross-drive moves
- Auto Process: dry runs change nothing; real runs leave edited/similar groups alone and can be undone
- Enhance: fusing degraded copies beats every single copy; unrelated images are rejected

---

## Project status and roadmap

**Status: work in progress.** The core pipeline, review UI, search, Organize, quarantine, Auto Process and Enhance are functional and covered by automated tests on synthetic data. They have not yet been benchmarked or tuned on a large real-world library.

### Known limitations

- Default similarity thresholds are tuned on synthetic test images and will probably need adjusting for real collections.
- OCR reads Latin-script languages only. Stylised, very small or handwritten text may be missed.
- "Same text" compares the set of words, not their order.
- XMP and IPTC metadata are not read yet. Videos and Live Photo components are skipped.
- The first CLIP and OCR passes are slow on CPU-only machines. So is neural upscaling, especially the *Best quality* model, which is limited to 1 MP of input (4 MP for *Fast*).
- Enhance can only remove defects the copies don't share: merging several copies of the same low-quality meme won't recover detail none of them has.
- The local database format may change between versions without a migration path.
- Windows is untested.

### Planned

- [ ] Validation and threshold tuning on large real-world libraries
- [ ] XMP / IPTC metadata support
- [ ] Optional GPU acceleration for CLIP and OCR
- [ ] Additional OCR languages / scripts
- [ ] Map view for geotagged photos
- [ ] Video support
- [ ] Packaged releases (single-command install)

---

## License

No license has been chosen yet. Until one is added, all rights are reserved by the author.
