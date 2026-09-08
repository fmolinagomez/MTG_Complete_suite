# ADR 0003: Deck Card Versioning, Stock Fulfillment & Granular Proxy Allocation

## Status
Accepted

## Context
When loading a deck from an external source (Moxfield, Archidekt, ManaBox, etc.), users often own copies of a card in alternative prints/editions rather than the specific edition listed in the deck.
Previously:
1. The system differentiated between exact prints and alternative prints, but hid the details of which prints were owned and how many copies of each edition were available.
2. Users had no control over which specific owned edition fulfilled a deck card.
3. Users could not override physical stock to intentionally print proxies (e.g. for high-value cards they don't wish to play physically).
4. Physical vs. proxy allocations were all-or-nothing without granular quantity control.

## Decisions

### 1. Data Model & Schema Enhancement (Option Y)
- Preserve the original imported deck edition/art intent (`set_code`, `collector_number`).
- Add columns to SQLite table `deck_cards`:
  - `physical_quantity INTEGER NOT NULL DEFAULT 0`: number of physical inventory cards allocated to fulfill the requirement.
  - `proxy_quantity INTEGER NOT NULL DEFAULT 0`: number of copies that must be printed as proxies.
  - `fulfilled_card_id INTEGER NULL REFERENCES inventory_cards(id)`: specific inventory card fulfilling the requirement (or NULL if auto-combination / proxy).
- Automated startup migration in `src/inventory/db.py` backfills legacy rows without data loss.

### 2. Version Discovery & Multi-Edition Stock Allocation
- `StockAllocationEngine.analyze_deck_card_matches` now inspects the collection for all matching prints (exact and alternative by `oracle_id` and normalized name).
- Constructs `available_versions` on each `DeckCardMatchDetail` containing:
  - `card_id`: foreign key to the inventory card.
  - `set_code` & `collector_number`.
  - `is_foil`, `condition`, `is_proxy`.
  - `quantity_available`: remaining unassigned stock across registered decks.
  - `is_exact`: boolean indicating exact print match.
- If `fulfilled_card_id` is set, physical stock is allocated specifically from that print. Otherwise, optimal auto-combination is applied (exact matches first, then alternative prints).
- When physical copies are proxied (`physical_quantity = 0`), the collection copies remain completely unallocated and available for other registered decks.

### 3. Interactive Deck Table in `InventoryView`
- Upgraded `self.deck_tree` columns:
  `Carta`, `Edición Mazo`, `Número`, `Req.`, `Versión Asignada (Inventario)`, `Físicas`, `Proxies`, `Estado`, `Proxy?`.
- **Versión Asignada Dropdown (Combobox)**:
  Clicking the assigned version cell opens an inline overlay with:
  - `Auto: Mejor combinación (Stock disponible)`
  - Individual owned editions: e.g. `DMR #45 - 1 disp. (Foil)`, `ICE #82 - 2 disp.`
  - `Ninguna (Solo Proxy)`
- **Físicas Spinbox**:
  Clicking the physical count cell allows granular adjustment from `0` to max available stock. `Proxies` automatically updates to `Req - Físicas`.
- **Proxy? Checkbox Toggle**:
  Clicking the checkbox column immediately toggles between 100% proxy (`Físicas = 0`, `Proxies = Req`) and restoring max available physical stock.
- Changes are instantly committed to SQLite via `InventoryService.update_deck_card_allocation` and live summary statistics recompute dynamically.

### 4. PDF Missing Proxy Pipeline Integration
- `calculate_missing_proxies` includes all cards where `proxy_quantity > 0` for that exact quantity.
- Generates the PDF using the original deck edition/artwork specified in the deck list.

## Consequences
- Full user agency over card versions and physical vs. proxy splits directly in the GUI.
- 100% backward compatible with existing SQLite databases and external deck imports.
- Verified with unit, integration, and UI tests across all scenarios.
