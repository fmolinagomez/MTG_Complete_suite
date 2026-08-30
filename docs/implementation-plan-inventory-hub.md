# Implementation Plan: Splash Hub, Modular Navigation & Card Inventory System

Reorganize the application into a single-window modular architecture with a persistent top navigation bar and Splash Hub, and introduce a new **Card Inventory & Deck Binder** module powered by a local standalone SQLite database, ManaBox CSV import, Moxfield missing proxy calculations, and hover image previews.

---

## 1. Architectural Highlights

- **Single-Window Architecture**: Persistent root `tk.Tk` with persistent top navigation bar (`NavBar`), dynamic `FrameSwitcher` container, and global status/progress bar.
- **Background Task Engine (`src/engine/`)**: Dedicated thread-safe task manager with job cancellation and unified event queue.
- **Card Inventory & Deck Binder Domain (`src/inventory/`)**: SQLite hybrid relational + JSON database in `MPCFillToPDF/inventario.db`.
- **ManaBox CSV Sync**: Support for both Replace (full reset of unassigned stock) and Merge (smart upsert keeping deck mappings).
- **Moxfield Proxy Resolution**: Evaluates physical stock vs required cards (Option C: tracks owned vs in-use), queues missing cards for PDF printing, and registers decks.
- **Interactive Card Previews**: Hover image tooltip for cards in inventory and validator tables using cached Scryfall small images.

---

## 2. Proposed Module Structure

### Storage & Domain Layer (`src/inventory/`)
1. `src/inventory/models.py`: Dataclasses for `InventoryCard`, `DeckRecord`, `DeckCardEntry`, `StockSummary`, `MissingProxyPlan`.
2. `src/inventory/db.py`: SQLite connection factory, schema initialization, indexed tables (`inventory_cards`, `decks`, `deck_cards`), transactions.
3. `src/inventory/manabox_csv.py`: ManaBox CSV parser with Replace vs Merge handlers.
4. `src/inventory/service.py`: Business service for collection queries, deck registration, stock allocation, missing proxy calculations, and deck-to-inventory import.

### Background Task Engine (`src/engine/`)
1. `src/engine/task_manager.py`: Job dispatcher, progress reporting, and cancellation tokens.

### GUI Components & Views (`gui/`)
1. `gui/components/nav_bar.py`: Persistent top navigation header with active tab indicator and background spinner.
2. `gui/components/card_preview_hover.py`: Tooltip overlay displaying cached Scryfall `small` card images on mouse hover.
3. `gui/app.py`: Main single-window container and view switcher.
4. `gui/views/hub_view.py`: Welcome Splash / Hub screen with shortcuts and collection statistics.
5. `gui/views/generator_view.py`: Refactored PDF generator view.
6. `gui/views/validator_view.py`: Refactored Scryfall/Moxfield validator view with hover previews.
7. `gui/views/inventory_view.py`: Full-featured Inventory & Deck Binder view.
8. `gui/views/settings_view.py`: Unified settings tab.

---

## 3. Verification & Test Plan

- **Automated Tests**:
  - `tests/test_inventory_db.py`: SQLite schema creation, CRUD operations, transactions, and error handling.
  - `tests/test_manabox_csv.py`: CSV parsing, header mapping, Merge vs Replace data integrity.
  - `tests/test_inventory_service.py`: Stock allocation calculations, missing proxy resolution for Moxfield decks.
  - `tests/test_task_manager.py`: Thread pool dispatching, progress queue, and cancellation checks.
  - Full suite run (`python -m pytest tests/`) to ensure no regressions in existing 20 test files.
- **Manual Verification**:
  - Launch GUI (`python -m gui.main`), verify Splash Hub start and top navigation switching.
  - Import ManaBox CSV in Merge and Replace modes.
  - Hover over card rows and check instant Scryfall small preview tooltip.
  - Test Moxfield missing proxy PDF generation and deck-to-inventory bulk import.
