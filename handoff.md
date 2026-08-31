# Next Session Handoff: Card Inventory Enhancements & Deck Synchronization

All features for Advanced Collection Filtering, Natural Column Sorting, Card Deck Usage Popup, and Exact vs Alternative (`oracle_id`) Deck Synchronization have been fully implemented, tested, and documented.

---

## 1. Summary of Completed Features

- **Card Decks Usage Popup (`CardDecksUsageDialog`)**:
  - Modal dialog triggered via double-click on table rows or `"🔍 Ver en Mazos"` button.
  - Cross-references all registered decks to display deck name, format, required copies, version, and physical vs proxy type.
  - Real-time resolution via `InventoryService.get_card_deck_usages`.
- **Advanced Collection Filtering & Smart Column Sorting**:
  - Collapsible filter panel with usage state (*Todas*, *Solo Disponibles*, *Solo Asignadas a Mazos*), dynamic set codes, and rarity.
  - Instant *"Limpiar Filtros"* reset action.
  - Interactive table headers with ascending (▲) / descending (▼) toggling.
  - Natural sorting: numeric for quantities/numbers, case-insensitive for text, and boolean grouping for foil/proxy flags.
- **Detailed Deck ↔ Inventory Synchronization (Exact vs Alternative Stock)**:
  - When loading or selecting a deck in the decks view:
    - Calculates available **Exact Stock** (`set_code` + `collector_number`).
    - Calculates available **Alternative Stock** (matching `oracle_id` or canonical name in other editions).
    - Calculates **Missing Copies** (proxies to print in PDF).
    - Status badges: 🟢 `Exacta`, 🔵 `Alternativa (Mismo Oracle)`, ⚠️ `Parcial`, 🔴 `Faltante (Proxy)`.
    - Live summary banner: `"Requeridas: X | Stock Exacto: Y | Stock Alternativo: Z | Faltantes (Proxies a Imprimir): W"`.

---

## 2. Updated Project Documentation

- **Domain Glossary**: [CONTEXT.md](file:///Users/molins/Dev/MPCFillToPDF/CONTEXT.md)
- **Developer Guide & Project Structure**: [CLAUDE.md](file:///Users/molins/Dev/MPCFillToPDF/CLAUDE.md)
- **Project Overview & Setup**: [README.md](file:///Users/molins/Dev/MPCFillToPDF/README.md)
- **Test Suite**: 772 passing tests covering 100% of domain services, corner-case resilience, GUI views, and allocation engines.
- **Deepened Architecture Modules**:
  - `src/deck_importer.py` (`GameDeckAdapter` strategy engine for MTG, Lorcana, One Piece, Riftbound with centralized networking & caching in `src/scraper_utils.py`).
  - `src/pipeline.py` (`PrintPipeline.execute(PrintJobSpec) -> PrintJobResult` unifying drive checks, cropping, layout, chunking, and manifests).
  - `src/inventory/allocation_engine.py` (`StockAllocationEngine` and `AllocationPolicy` providing pure in-memory card matching and multi-deck stock solver).
  - `gui/views/generator_tabs.py` & `gui/components/generator_scraper_tab.py` (Modular sub-tabs replacing monolithic legacy tab layer; clean `gui/main.py` entrypoint).

---

## 3. Potential Next Steps

1. **Live Pricing & Collection Value**: Integration of Scryfall live EUR/USD price caching in inventory metadata.
2. **Export Options**: Exporting collection or decks to CSV, ManaBox format, or Moxfield text lists.
3. **Batch Operations**: Multi-selection actions in the inventory table (batch delete, batch tag as proxy/foil, batch assign to deck).

