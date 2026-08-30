# ADR 0002: Modular Single-Window Hub & Card Inventory System

## Status
Accepted

## Context
The MPCFillToPDF desktop application has expanded from an MPCFill XML-to-PDF utility into a comprehensive tool suite including multi-game scrapers (Moxfield, Archidekt, Lorcana, One Piece, Riftbound), Scryfall image quality analyzers, and deck validators.
Users need:
1. A unified entry point (Splash Hub) with quick access to the different capabilities.
2. A persistent navigation bar that allows seamlessly switching views without interrupting background operations.
3. A local **Card Inventory and Deck Binder** system to register physical cards (from ManaBox CSV exports), track registered decks, calculate missing proxy cards for Moxfield decks, generate print-ready PDFs of missing proxies, and view live hover card image previews from Scryfall cache.

## Decisions

### 1. Window & Lifecycle Architecture (Option A: Single-Window Frame Switcher)
- The application runs in a single persistent window managed by `gui.app.App`.
- A persistent `NavBar` widget at the top allows switching between **Hub / Inicio**, **Generador PDF**, **Validador**, **Inventario & Mazos**, and **Configuración**.
- View containers are cached/swapped dynamically inside a central `FrameSwitcher` container.

### 2. Concurrency & Background Job Engine (`src/engine/`)
- Long-running operations (PDF generation, bulk Scryfall resolution, ManaBox CSV parsing) run on background worker threads managed by `TaskManager`.
- Communication to UI views and the global status bar uses thread-safe events pumped through `queue.Queue` and drained on Tkinter's event loop via `root.after()`.

### 3. Card Inventory & Deck Binder Domain (`src/inventory/`)
- **Database**: SQLite3 stored at `MPCFillToPDF/inventario.db` (next to the executable for portability across updates).
- **Schema**:
  - `inventory_cards`: Stores owned physical cards and registered proxies with `oracle_id`, `name`, `set_code`, `collector_number`, `quantity`, `is_foil`, `is_proxy`, and JSON metadata.
  - `decks`: Stores registered deck metadata (name, source URL, format, notes).
  - `deck_cards`: Maps cards and required quantities to registered decks.
- **ManaBox Import**: Supports both **Replace** (wipe unassigned inventory) and **Merge** (smart upsert preserving deck assignments).
- **Missing Proxy Calculator**: Compares deck requirements against physical collection stock (Option C: tracks owned vs. allocated quantity). Automatically queues missing cards to be printed as proxies and marks them in the deck's proxy register.
- **Bulk Deck-to-Inventory**: Provides an action to import all cards of a deck into the inventory as either physical cards or proxies.

### 4. Interactive Card Preview Tooltip
- `gui/components/card_preview_hover.py` provides a floating image preview when the user hovers over card names/rows in table views.
- Small images are fetched asynchronously from Scryfall and cached on disk in `MPCFillToPDF/procesamiento/previews/`.

## Consequences
- The codebase is structured into clear domain modules (`src/inventory/`, `src/engine/`, `src/pdf_pipeline/`, `gui/views/`, `gui/components/`).
- The CLI mode (`python -m cli.main`) and standalone `.exe` packaging remain 100% backward-compatible.
- All new domain models, database operations, and CSV parsers are covered by automated unit tests in `tests/`.
