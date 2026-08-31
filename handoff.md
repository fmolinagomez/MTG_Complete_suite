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
- **Test Suite**: 740 passing tests covering 100% of domain services, corner-case resilience, and GUI components.
- **Corner-Case Test Suites**:
  - `tests/test_scryfall_corner_cases.py` (Rate limits HTTP 429, zero-byte cache recovery, MDFC without back URIs, digital-only cards).
  - `tests/test_quality_and_image_corner_cases.py` (0-byte corrupt files, color modes P/1/LA/CMYK, Laplacian variance on solid colors).
  - `tests/test_downloader_resilience.py` (Timeouts, connection drops with backoff, Google Drive virus warning pages).
  - `tests/test_pipeline_corner_cases.py` (Cancellation mid-step, 0-card generation, exact chunking boundaries).
  - `tests/test_inventory_corner_cases.py` (BOM/Latin-1/Windows-1252 CSVs, non-standard headers, Option C multi-deck allocation).
  - `tests/test_cli_corner_cases.py` (Argument validation <=0, quiet mode, missing XML handling, SIGINT/130 exit).
  - `tests/test_hover_preview_corner_cases.py` (Screen boundary tooltip flips, rapid mouse motion debounce, async error safety).
  - `tests/test_views_corner_cases.py` (Drag-and-drop filtering, PDF collision handling, validator CSV export).

---

## 3. Potential Next Steps

1. **Live Pricing & Collection Value**: Integration of Scryfall live EUR/USD price caching in inventory metadata.
2. **Export Options**: Exporting collection or decks to CSV, ManaBox format, or Moxfield text lists.
3. **Batch Operations**: Multi-selection actions in the inventory table (batch delete, batch tag as proxy/foil, batch assign to deck).

