# MPCFillToPDF

Automated pipeline that converts an MPCFill XML project file into a print-ready PDF for a local print shop.

## What it does

1. Parses an MPCFill XML file to extract card front/back assignments and Google Drive image IDs
2. Downloads images from Google Drive
3. Crops images (removes MPC bleed border: 4.2% width + 3.1% height per side)
4. Generates a duplex-ready PDF with fronts on page 1 and mirrored backs on page 2

## XML structure (MPCFill format)

- `<fronts>` and `<backs>` contain `<card>` entries with `<id>` (Google Drive file ID), `<slots>`, `<name>`, `<query>`
- Slot numbers pair fronts with backs (same slot number = same physical card)
- `<cardback>` is the default back for all slots not listed in `<backs>`
- Cards without a specific entry in `<backs>` use the default cardback

## PDF layout

- Paper: A4 portrait
- Grid: 3 columns × 3 rows = 9 cards per page
- Cards are evenly spaced; crop marks appear in the page margins (not between cards)
- Page 1: front faces in slot order (slots 0–8, left to right, top to bottom)
- Page 2: backs horizontally mirrored so duplex printing aligns correctly
  - Mirroring means slot positions: [2,1,0 / 5,4,3 / 8,7,6] on back page
  - Each slot uses its specific back if defined in `<backs>`, otherwise uses `<cardback>`

## Tech stack

- **Language**: Python 3.10+
- **Database**: SQLite3 (`sqlite3` stdlib) with JSON1 capabilities for local inventory persistence
- **XML parsing**: `xml.etree.ElementTree` (stdlib)
- **Image download**: `requests` (with Google Drive and Scryfall caching)
- **Image processing**: `Pillow`, `OpenCV` (focus blur detection)
- **PDF generation**: `reportlab`

## Interfaces

The project supports two main execution modes:

1. **CLI** — accepts XML paths and batch-processes `xml/*.xml` into `out/` (unattended batch pipeline).
2. **Desktop GUI (Modular Single-Window App)** — Tkinter application featuring:
   - **Persistent Top Navigation Bar**: Seamlessly switch between modules without closing or losing background tasks.
   - **Splash Hub**: Quick-start dashboard with shortcuts and collection overview metrics.
   - **PDF Generator View**: MPCFill XML and Direct Game Deck to PDF pipeline with size-based chunking.
   - **Card Validator View**: Moxfield / Scryfall quality & legality validator with hover previews.
   - **Card Inventory & Deck Binder View**: ManaBox CSV import (Merge/Replace), collection search, advanced collapsible filters (usage state, dynamic sets, rarity), interactive column sorting (▲/▼), card-to-decks usage modal (`CardDecksUsageDialog`), exact vs alternative (`oracle_id`) deck stock synchronization, Moxfield missing proxy generator, and live image tooltips.
   - **Settings View**: Global application configuration.

## Project structure

```
MPCFillToPDF/
├── src/
│   ├── engine/                # Concurrency & background task orchestration (TaskManager, events)
│   ├── inventory/             # Card collection & deck binder domain (SQLite, ManaBox CSV, proxy solver)
│   │   ├── db.py              # SQLite schema, migrations, and connection management
│   │   ├── models.py          # Data models (InventoryCard, DeckRecord, DeckCardEntry, DeckCardMatchDetail, MissingProxyPlan)
│   │   ├── manabox_csv.py     # ManaBox CSV parser with Replace and Merge support
│   │   └── service.py         # Inventory operations, deck sync, exact vs alt stock matching, missing proxy calculation
│   ├── parser.py              # XML parsing → structured card data
│   ├── downloader.py          # Google Drive & Scryfall image download
│   ├── cropper.py             # Bleed removal (Pillow)
│   ├── pdf_generator.py       # PDF layout + chunking (reportlab)
│   ├── pipeline.py            # Orchestrates the full flow
│   ├── deck_importer.py       # Multi-platform deck parser
│   ├── scryfall.py            # Scryfall API client & local image caching
│   └── quality.py             # Focus & resolution quality assessment
├── gui/
│   ├── main.py                # GUI entry point launching gui.app.App
│   ├── app.py                 # Single-window container, top nav bar, and frame switcher
│   ├── paths.py               # Runtime dir resolution (MPCFillToPDF/inventario.db, output, cache)
│   ├── components/            # Reusable widgets (nav_bar, card_preview_hover, status_bar)
│   └── views/                 # Modular screens (hub_view, generator_view, validator_view, inventory_view)
├── cli/
│   └── main.py                # CLI: batch-processes xml/*.xml into out/
├── build_exe.py               # PyInstaller build script (produces dist/MPCFillToPDF.exe)
├── xml/                       # Drop .xml inputs here (CLI mode)
├── MPCFillToPDF/              # Runtime folder next to .exe or in root; gitignored
│   ├── inventario.db          # Standalone SQLite collection & deck database
│   ├── archivos generados/    # Generated PDFs
│   └── procesamiento/         # Download cache, previews/, intermediate files
├── docs/                      # Architectural reviews, ADRs, and implementation plans
│   └── adr/                   # Architecture Decision Records
└── tests/                     # Unit and integration test suites (740+ tests)
```

### Environment management (uv)
- Use `uv sync` to set up or update dependencies and tools.
- Run all python scripts, tools, and tests via `uv run`.

### CLI usage
- Run `uv run python -m cli.main` (or `uv run mpcfill-cli`) to process every `xml/*.xml` and write its PDF(s) to `out/`.
- Output names: `out/<xml_stem>.pdf`, or `out/<xml_stem>_1.pdf`, `out/<xml_stem>_2.pdf`, … when split.

### GUI usage
- Run `uv run python -m gui.main` (or `uv run mpcfill-gui`) to launch the single-window desktop application.
- Top navigation bar allows jumping between **Inicio**, **Generador**, **Validador**, **Inventario** y **Configuración**.
- Background tasks (like PDF generation or bulk Scryfall fetches) run asynchronously via `TaskManager` while users browse their inventory.

### Packaging (V2 → .exe)
- `uv run python build_exe.py` runs PyInstaller with `--onefile --windowed`, bundling `src/assets/` and GUI resources.
- Output: `dist/MPCFillToPDF.exe`. The .exe is portable — drop it anywhere and it creates `MPCFillToPDF/inventario.db`, `MPCFillToPDF/archivos generados/` and `MPCFillToPDF/procesamiento/` next to itself on first run.
- `gui/_build_flags.py` is generated by `build_exe.py` at build time and deleted afterwards; it is gitignored and never committed.

### Size-based splitting
- Cap: each output PDF stays under 500 MB on disk (decimal MB). `MAX_PDF_BYTES` in `pdf_generator.py` is set to 480 MB so the projected estimate has a safety margin.
- The cut is taken after the next even page (back), so each chunk remains independently duplex-ready.
- Per-pair size is projected from the cropped image file sizes with per-extension multipliers: JPEG ×1.30 (kept as `/DCTDecode` with ~25% ASCII85 overhead), PNG ×2.00 (reportlab decodes PNGs and re-encodes with Flate+ASCII85, roughly doubling photographic card art). When a pair's projected new bytes would push the chunk past the cap, a new chunk starts — even if that leaves the next chunk with a single page-pair (2 pages).

## Key implementation notes

### Image download
- `GET https://lh4.googleusercontent.com/d/{id}=d` via `requests` with `stream=True`. Works for both "Anyone with the link" and "Public on the web" Drive sharing modes; returns the original file unchanged (same approach as mpc-autofill).
- Download with 5 parallel threads (matches mpc-autofill behaviour)

### Scryfall Image Quality Control & Fallback
- **Focus Check:** Grayscale conversion and Laplacian variance score (`cv2.Laplacian(gray, cv2.CV_64F).var()`). Images below the threshold (default `100.0`) are considered blurry/unfocused.
- **Resolution Check:** Verifies dimensions are at least 745 × 1040 pixels (standard 300 DPI target for Scryfall PNGs).
- **Fallback Logic:** If quality is insufficient (and check is enabled), queries Scryfall API for all prints (`oracle_id`) in preferred language first, then English. Downloads and tests candidates until one passes, otherwise falls back to the highest focus score print.
- **Settings:** Configurable in the GUI's Configuración tab or `settings.json` via `scryfall_quality_check` (bool) and `scryfall_blur_threshold` (float).

### Image cropping
- Crop formula: `border_x = round(width * 0.042)`, `border_y = round(height * 0.031)`
- Crop box: `(border_x, border_y, width - border_x, height - border_y)`

### PDF layout (matches examples/example.pdf exactly)
- Paper: A4 portrait (210mm × 297mm)
- Grid: 3 columns × 3 rows = 9 cards per page
- Card trim size: 63.5mm × 88.9mm (MPC standard)
- Bleed: 1mm kept around each trim (image size is 65.5 × 90.9mm)
- Margin page-edge → trim: 5.75mm horizontal, 11.15mm vertical
- Gap between trims: 4mm horizontal and vertical (= 2mm visible white between images)
- Cut lines: thin black lines (0.5pt) extending from the page edges to the card corners and across the gaps, forming a continuous trim grid
- Page 1 fronts slot order: left→right, top→bottom (slots 0–8)
- Page 2 backs are horizontally mirrored: `col_back = 2 - col_front`, same row
  - Each slot uses its specific back if in `<backs>`, otherwise uses `<cardback>`

### Cards per page
- Always 9 (3×3); when total cards > 9 generate multiple front/back page pairs
- Last page pair may have fewer than 9 cards; empty slots left blank

---

## Development style

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
