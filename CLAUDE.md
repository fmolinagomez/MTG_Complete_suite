# MPCFillToPDF

Python 3.10+ application that converts MPCFill XML, local images and imported TCG decks into print-ready A4 PDFs. It provides a Tkinter single-window modular GUI and an XML/local-image CLI, with PyInstaller builds for Windows, macOS and Linux. See [README.md](README.md) for user instructions and supported URL formats.

## Architecture and entry points

| Module | Responsibility |
| --- | --- |
| `src/parser.py` | Parse XML into `CardImage` and `CardOrder`; resolve per-slot backs. |
| `src/validator.py` | Return `ValidationWarning` objects for parsing errors, absent fronts, duplicate slots and orphan backs. |
| `src/precheck.py` | Collect/check Drive IDs, count cards, create `XmlReport`, `PdfJob` and `Plan`, write `resumen.txt`. |
| `src/pipeline.py` | Coordinate slot mapping, download, crop and PDF generation (`PrintPipeline`, `PrintJobSpec`). |
| `src/downloader.py` | Drive API/gdown downloads, disk cache, retries, progress and typed errors. |
| `src/deck_importer.py` | Import Magic lists into `FetchedDeck` / `DeckCard`. |
| `src/scryfall.py` | Resolve Magic images by printing or name, including double-faced cards and focus check. |
| `src/op_scraper.py`, `src/rb_scraper.py`, `src/lorcana_scraper.py`, `src/ygo_scraper.py` | Scrape decks, download images and expand quantities into parallel front/back lists. |
| `src/cropper.py` | Optional MPC border crop, rounded-corner fill and mirror bleed. |
| `src/pdf_generator.py` | A4 layout, printer marks, configurable cut lines and size-based splitting (Standard and Yu-Gi-Oh! layouts). |
| `src/app_settings.py` | Persist GUI export path, max PDF size and cut-line preferences. |
| `src/config.py` | Resolve the runtime Drive key. |
| `src/constants.py`, `src/cancellation.py`, `src/scraper_utils.py` | Shared grid/callback types, `Cancelled`, resource paths and fallback backs. |
| `gui/app.py` | Modular single-window application with navigation bar and view router. |
| `gui/views/` | Modular views: `HubView`, `GeneratorView`, `ValidatorView`, `InventoryView`, `SettingsView`. |
| `cli/main.py` | Batch XML/local-image/URL interface. |
| `build_exe.py` | Platform-specific PyInstaller build. |

Pipeline entry points:

- `run`: one XML, with optional local fronts/backs.
- `run_merged`: concatenate multiple XMLs under a supplied base name.
- `run_plan`: build all jobs, deduplicate/download all Drive images, crop all images, then generate PDFs per job. Extra local images attach only to the last job. Supports per-XML download/crop progress and download speed callbacks.
- `run_locals_only`: generate from image paths with a required default local back; accepts cut-line and layout settings.
- `run_print_batch`: render any fully prepared `PrintBatch` through the shared local-image pipeline.
- `run_deck_url`: import a Magic URL, download Scryfall images and delegate to `run_locals_only`; sideboard excluded by default.

The GUI downloads/expands web decks before calling `run_plan` or `run_locals_only`. The Tk loop drains a `queue.Queue` every 80 ms; cancellation uses a `threading.Event` and `Cancelled`.

## XML, ordering and planning contracts

- `<details><quantity>` is parsed as metadata. Precheck counts actual entries in the fronts' slot lists.
- `<fronts>` and `<backs>` contain `<card>` entries with `<id>`, `<name>` and comma-separated `<slots>`. `<query>` may be present but is not consumed.
- Original slot numbers pair fronts and backs. `CardOrder.back_for_slot` uses an explicit back when present, otherwise `<cardback>`. A nonempty `<cardback>` and card IDs are required by the parser.
- `_build_slot_maps` sorts each XML's front entries by `(name.casefold(), original_slot)` and assigns new sequential slots while preserving each original back. Do not assume output follows the original XML slot order.
- `plan` keeps XMLs with counts divisible by 9 as separate jobs. Multiple incomplete XMLs are always merged, even when their sum is not divisible by 9, ordered by descending card count. A single incomplete XML stays separate. `local_count` contributes to the last job's blank count.
- The GUI sorts imported cards by name within each deck. Local fronts retain the user's order.
- `write_manifest` writes `resumen.txt` only for merges and removes a stale manifest otherwise. It reports XML counts and base names.

## Technology Stack

- **Language**: Python 3.10+
- **Environment & Tooling**: `uv` package and project manager
- **Database**: SQLite3 (`sqlite3` stdlib) with JSON1 capabilities for local inventory persistence
- **XML parsing**: `xml.etree.ElementTree` (stdlib)
- **Image download**: `requests`, `gdown` (with Google Drive and Scryfall caching)
- **Image processing**: `Pillow`, `OpenCV` (focus blur detection via Laplacian variance)
- **PDF generation**: `reportlab`
- **GUI**: `tkinter` / `ttk` (single-window modular desktop interface)

## Importers and backs

- Magic: Moxfield, Archidekt, Deckstats, TappedOut and ManaBox. Scryfall resolves set/collector number or exact name; normal cards use `resources/backs/mtg/back.jpg`, double-faced cards use their own second image. The GUI allows a local back for normal cards and optional sideboard inclusion.
- One Piece: onepiece.gg, deckbuilder.egmanevents.com (query/short links), deckbuilder.cardkaizoku.com. Leader and standard backs are separate.
- Riftbound: riftbound.gg, piltoverarchive.com, riftmana.com, riftbinder.com and riftdex.com. Backs depend on section; the GUI can omit runes.
- Lorcana: lorcana.gg and inkdecks.com. Shared back; no Dreamborn importer.
- Yu-Gi-Oh!: ygoprodeck.com deck imports, dedicated Japanese layout (59 × 86 mm trim), and default back in `resources/backs/yugioh/back.jpg`.
- These are implemented integrations, not a guarantee that external endpoints are currently available. Network tests are opt-in.

## Execution Modes

1. **CLI** — accepts XML paths, deck URLs, and local images into `out/` (unattended batch pipeline).
2. **Desktop GUI (Modular Single-Window App)** — Tkinter application featuring:
   - **Persistent Top Navigation Bar**: Seamlessly switch between modules without closing or losing background tasks.
   - **Splash Hub (`HubView`)**: Quick-start dashboard with shortcuts and collection overview metrics.
   - **PDF Generator View (`GeneratorView`)**: MPCFill XML and Direct Game Deck to PDF pipeline with size-based chunking.
   - **Card Validator View (`ValidatorView`)**: Moxfield / Scryfall quality & legality validator with hover previews.
   - **Card Inventory & Deck Binder View (`InventoryView`)**: ManaBox CSV import (Merge/Replace), collection search, advanced collapsible filters, interactive column sorting (▲/▼), card-to-decks usage modal, exact vs alternative (`oracle_id`) deck stock synchronization, and live image tooltips.
   - **Settings View (`SettingsView`)**: Global application configuration.

## Project Structure

```
MPCFillToPDF/
├── src/
│   ├── engine/                # Concurrency & background task orchestration (TaskManager, events)
│   ├── inventory/             # Card collection & deck binder domain (SQLite, ManaBox CSV, proxy solver)
│   ├── parser.py              # XML parsing → structured card data
│   ├── downloader.py          # Google Drive & Scryfall image download
│   ├── cropper.py             # Bleed removal (Pillow)
│   ├── pdf_generator.py       # PDF layout + chunking (reportlab)
│   ├── pipeline.py            # Orchestrates the full flow (PrintPipeline)
│   ├── deck_importer.py       # Multi-platform deck parser
│   ├── scryfall.py            # Scryfall API client & local image caching
│   ├── ygo_scraper.py         # Yu-Gi-Oh! deck scraper & image downloader
│   ├── op_scraper.py          # One Piece scraper
│   ├── rb_scraper.py          # Riftbound scraper
│   └── lorcana_scraper.py     # Lorcana scraper
├── gui/                       # Desktop application
│   ├── app.py                 # Single-window App shell & top navigation bar
│   ├── views/                 # Modular views (HubView, GeneratorView, ValidatorView, InventoryView, SettingsView)
│   ├── components/            # Reusable UI components (NavBar, CardHoverPreview)
│   └── widgets.py             # UI helpers & status indicators
├── cli/                       # Headless CLI entrypoint (python -m cli.main)
├── build_exe.py               # PyInstaller build script (produces dist/MPCFillToPDF.exe)
├── xml/                       # Drop .xml inputs here (CLI mode)
├── MPCFillToPDF/              # Runtime folder next to .exe or in root; gitignored
│   ├── inventario.db          # Standalone SQLite collection & deck database
│   ├── archivos generados/    # Generated PDFs
│   └── procesamiento/         # Download cache, previews/, intermediate files
├── docs/                      # Architectural reviews, ADRs, and implementation plans
└── tests/                     # Unit, integration, and corner-case test suites (850+ tests)
```

## Downloads, cache and cancellation

- Drive uses 5 workers. With a key it tries API v3 first; recognised permission failures fall back to gdown. Without a key it uses gdown directly.
- Runtime key order: `src._bundled_key` → project-root `config.json` → no key. The downloader loads it once at import. `DRIVE_API_KEY` is a build-time fallback, not a runtime environment lookup.
- Drive images are cached as `<drive_id><extension>`. Downloads use a temporary file and rename on completion. Existing final files are reused.
- Connect/read timeouts are 10/30 seconds; the read timeout is inactivity, not total duration. Rate-limit retries use delays of 1, 2, 4 and 8 seconds. Batch timeouts get one sequential retry after a 5-second pause; remaining permission/timeout failures become `DownloadPartialError`.
- Scryfall uses 5 image workers, a shared 0.1-second API request interval, in-memory metadata/name caches and disk image cache.
- Scryfall quality control & fallback: focus check via OpenCV Laplacian variance (`cv2.Laplacian(gray, cv2.CV_64F).var()`, threshold 100.0) and resolution check (>= 745x1040). If quality is insufficient, it queries Scryfall for candidate prints (`oracle_id`) in preferred language first, then English, falling back to the highest focus score print if none pass. Configurable via `scryfall_quality_check` and `scryfall_blur_threshold`.
- Local paths use stable synthetic IDs derived from their resolved path. Crop cache reuse compares output/input modification times; `_nocrop` distinguishes local images processed without MPC cropping.
- Cancellation is cooperative. In-flight network calls, sleeps and crop work may need to finish before the executor joins.

## Image processing and PDF contract

- MPC crop per side: `round(width * 0.042)` horizontally and `round(height * 0.031)` vertically.
- Local images default to no crop; web deck images also skip MPC cropping. With cropping disabled, rounded-corner fill runs before mirror bleed.
- Standard Card Trim: 63.5 × 88.9 mm. Mirror bleed: 1 mm per side; placed image: 65.5 × 90.9 mm.
- Yu-Gi-Oh! (Japanese) Trim: 59 × 86 mm. Placed image: 61 × 88 mm.
- A4 portrait, 3 columns × 3 rows = 9 cards per page.
- Fronts occupy rows left to right, top to bottom. Backs reverse column positions within each row: `[2,1,0 / 5,4,3 / 8,7,6]`. The artwork itself is not mirrored. Last-page empty slots remain blank.
- Default cut lines: black `#000000`, 1 pt, `ticks` in margins. `full` spans the page. Color, width, layering and front/back application are configurable.
- Page labels are `1`, `1B`, `2`, `2B`, etc., placed near the bottom center and continuing across chunks.
- Splitting uses projected unique-image bytes per chunk: JPEG ×1.30, PNG/other formats ×2.00. `MAX_PDF_BYTES` default is 480,000,000 (~480 MB) to guarantee outputs stay below 500 MB. Duplex pairs never span separate output files.

## Running and runtime paths

- GUI: `uv run python -m gui.main`
- CLI: `uv run python -m cli.main --help`; defaults are `--xml-dir xml --out-dir out --workdir workdir`.
- CLI runs create `out/DD_MM_YYYY_HH-MM-SS/` with PDFs, `run.log` and optional `resumen.txt`.
- GUI base directory is the repository root in source mode and the directory containing `sys.executable` when frozen. Outputs default to `<base>/MPCFillToPDF/archivos generados/DD_MM_YYYY_HH-MM-SS/`.
- GUI cache/logs live in `<base>/MPCFillToPDF/procesamiento/`.
- GUI settings live in `<base>/MPCFillToPDF/settings.json`.

## Packaging and development tooling

- Dependencies managed via `pyproject.toml` and `uv.lock`.
- `uv run python build_exe.py` invokes PyInstaller with `--onefile --windowed --noupx`, bundling `src/assets/`, `icons/` and `resources/`. Produces `dist/MPCFillToPDF.exe` (Windows) or `dist/MPCFillToPDF` (macOS/Linux).
- `pytest.ini` defaults to `-m "not network"`.
- Run all tests via `uv run pytest`.
- Lint & format checks: `uv run ruff check .` and `uv run ruff format --check .`.

---

## Development style

### Image-source policy

- Never generate, request, or add AI-created images. The application may only process local
  images, bundled resources, or images downloaded from its supported external sources.

### Language and Python version
- Python 3.10+. Use built-in generics (`list[Path]`, `dict[str, str]`, `X | None`) — never import `List`, `Dict`, `Optional` from `typing`.
- User-facing strings (UI labels, error messages, warnings) in **Spanish**. Code identifiers, log messages, and docstrings in **English**.

### Types and data structures
- Use `@dataclass` for any structured return value with more than ~3 fields. Use a plain tuple for simpler multi-value returns; annotate the return type explicitly.
- Use `Path` everywhere internally; convert `str | Path` inputs at function entry with `Path(x)`.
- Use `frozenset` for immutable constant sets (e.g., `SUPPORTED_IMAGE_EXTS`).
- Use `class Stage(str, Enum)` for typed string constants that must compare equal to raw strings (allows dict lookups with either key type).
- Use `field(default_factory=list)` for mutable defaults in dataclasses — never bare `[]`.

### Naming conventions
- Module-level constants: `SCREAMING_SNAKE_CASE`.
- Private module helpers and private instance attributes: `_underscore_prefix`.
- Dict variables: `key_to_value` pattern (e.g., `id_to_path`, `slot_to_id`, `xml_needed_ids`).
- Parallel lists: name them consistently (`local_fronts` / `local_front_crop` — same index = same card).

### Module and function design
- One concern per function. Build-functions (`_build_*`) only compute and return; they do not download, write files, or have side effects.
- Extract any loop that appears in two places into a shared private helper immediately. Do not tolerate ~20-line duplications.
- Place shared constants in `src/constants.py`. Do not re-define a constant in a consuming module.
- Place logic that belongs to a data model inside that model (e.g., `CardOrder.back_for_slot`, `CardOrder.all_drive_ids`) rather than reimplementing it in callers.

### Concurrency and cancellation
- All worker functions that can take time accept `cancel_event: Event | None` and call `_check_cancel(cancel_event)` at safe checkpoints.
- Use `ThreadPoolExecutor` + `as_completed` for parallel I/O (download, crop). Do not use `map` when you need per-item error handling or cancellation.
- GUI ↔ worker communication goes through `queue.Queue`; the Tk loop drains it with `after()`. Never call Tk methods from the worker thread.

### Error handling
- Define a custom exception class (`DownloadPermissionError`, `Cancelled`, etc.) for each distinct failure mode that callers need to handle differently.
- For user-visible errors (missing file, bad XML, permission denied), raise `ValueError` with a clear Spanish message. Catch and translate at the CLI/GUI boundary.
- Do not catch broad `Exception` in production code except at top-level handlers (CLI `main`, GUI worker wrapper) and optional-dependency guards (e.g., `plyer`).

### Comments and docstrings
- No comments that explain *what* the code does — identifiers do that.
- Add a one-line docstring only when the return value or non-obvious contract needs stating (e.g., `"""Return (drive_id, raw_path, bled_path, crop_borders) for each image."""`).
- Add an inline comment only for *why*: a hidden constraint, a workaround, or a number that comes from an external spec (e.g., `# matches mpc-autofill behaviour`).

### Testing
- Test files live in `tests/`. Shared helpers and fixtures go in `tests/conftest.py`.
- Use real tiny Pillow images for crop and PDF tests — do not mock the image pipeline.
- Mock only at module boundaries (`patch("src.pipeline.download_all")`), never inside `src/`.
- Use `tmp_path` (pytest built-in) for all temporary files.
- Group related tests in a class named `TestFeatureName`; keep each test focused on one behaviour.
- Run `uv run pytest` to run the full suite. All tests must pass before committing.
