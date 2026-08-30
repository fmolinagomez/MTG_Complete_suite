# ADR 0001: Moxfield Validator GUI Script

## Status
Proposed

## Context
The user wants a standalone script to perform technical validation of Moxfield decks. The validator needs a graphical user interface (GUI) to:
1. Input a Moxfield deck URL.
2. Fetch and parse the deck cards (mainboard, sideboard, and tokens).
3. Query the Scryfall API to validate the existence, set/collector number matching, double-faced status, and high-resolution availability.
4. Display the card list (showing name, quantity, zone, set, number, type, and validation status) in a table.
5. Show a card image preview in a side panel by downloading Scryfall's `small` image format.

## Decision
We will implement an independent script `gui/moxfield_validator.py` built with Tkinter to maintain visual and platform-level consistency with the main application. 

### Key Technical Aspects:
- **Scryfall Integration**: We will reuse/adapt `src/scryfall.py` to query card metadata. We will modify `src/scryfall._extract_images` to support the `small` image quality parameter.
- **Async Loading**: The UI will load the deck and perform Scryfall validation requests asynchronously on a background thread so the GUI does not freeze.
- **Image Caching & Preview**: We will download Scryfall's `small` size images to a temporary subdirectory inside the download cache (`MPCFillToPDF/procesamiento/previews/`) and display them using Pillow in a dedicated image label.
- **Validation Criteria**:
  - 🟢 **Válido**: Found on Scryfall with high-resolution image available.
  - ⚠️ **Advertencia**: Found but has issues, such as missing set/collector number in Moxfield (resolved via Scryfall search), digital-only, or missing high-resolution image.
  - 🔴 **Error**: Not found on Scryfall, API error, or missing image.

## Consequences
- A new standalone GUI entry point `gui/moxfield_validator.py` will be created.
- Scryfall image extraction is updated to natively support downloading smaller images for quick visual previews, minimizing bandwidth usage.
- Domain terminology in `CONTEXT.md` is respected.
