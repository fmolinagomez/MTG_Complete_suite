# Next Session Handoff: Splash Hub & Card Inventory System

All architectural decisions, domain concepts, database schemas, and implementation plans have been thoroughly grilled, finalized, and documented. The project is 100% ready for coding.

---

## 1. Summary of Completed Specifications & Decisions

- **Window Lifecycle**: Single-Window application (`gui.app.App`) starting on a **Splash Hub** with a **Persistent Top Navigation Bar** (`NavBar`) and dynamic view switching (`FrameSwitcher`).
- **Concurrency & Task Management**: Background operations (PDF generation, bulk Scryfall checks, CSV imports) dispatched through `src/engine/task_manager.py` with unified event queue.
- **Card Inventory & Deck Binder Domain (`src/inventory/`)**:
  - Embedded SQLite database in `MPCFillToPDF/inventario.db`.
  - ManaBox CSV parser with user prompt for **Merge** (smart upsert preserving deck bindings) vs. **Replace** (full reset of unassigned stock).
  - Stock allocation strategy **Option C**: Tracks owned physical stock vs. allocated in-use quantity across registered decks.
  - Moxfield deck integration: Computes missing cards, automatically generates a print-ready PDF of **only missing proxies**, and registers the deck.
  - Deck-to-inventory import: 1-click action to dump all cards of a deck into the inventory as real cards or proxies.
- **Interactive Previews**: Hover tooltip (`card_preview_hover.py`) displaying cached Scryfall `small` card images on mouse hover over table rows.

---

## 2. Updated Project Documentation

- **Domain Glossary**: [CONTEXT.md](file:///Users/molins/Dev/MPCFillToPDF/CONTEXT.md)
- **Developer Guide & Project Structure**: [CLAUDE.md](file:///Users/molins/Dev/MPCFillToPDF/CLAUDE.md)
- **Architecture Decision Record**: [docs/adr/0002-modular-hub-and-inventory-system.md](file:///Users/molins/Dev/MPCFillToPDF/docs/adr/0002-modular-hub-and-inventory-system.md)
- **Detailed Implementation Plan**: [docs/implementation-plan-inventory-hub.md](file:///Users/molins/Dev/MPCFillToPDF/docs/implementation-plan-inventory-hub.md)
- **Previous Architecture Review**: [docs/architecture-review.md](file:///Users/molins/Dev/MPCFillToPDF/docs/architecture-review.md)

---

## 3. Ready-to-Execute Implementation Steps

When resuming in the next session, follow this step-by-step roadmap:

1. **Step 1: Domain & Storage Layer** (`src/inventory/`)
   - Implement `src/inventory/models.py`, `src/inventory/db.py`, and `tests/test_inventory_db.py`.
   - Implement `src/inventory/manabox_csv.py` and `tests/test_manabox_csv.py`.
   - Implement `src/inventory/service.py` and `tests/test_inventory_service.py`.
2. **Step 2: Task Engine** (`src/engine/`)
   - Implement `src/engine/task_manager.py` and `tests/test_task_manager.py`.
3. **Step 3: UI Components & Image Hover Tooltip** (`gui/components/`)
   - Implement `gui/components/nav_bar.py` and `gui/components/card_preview_hover.py`.
4. **Step 4: Modular Views & Hub Application** (`gui/views/` & `gui/app.py`)
   - Implement `gui/views/hub_view.py`, `gui/views/inventory_view.py`.
   - Refactor `gui/views/generator_view.py`, `gui/views/validator_view.py`, `gui/views/settings_view.py`.
   - Wire up `gui/app.py` and update `gui/main.py`.
5. **Step 5: Full Test Suite & Validation**
   - Run `python -m pytest tests/` and perform manual UX verification.
