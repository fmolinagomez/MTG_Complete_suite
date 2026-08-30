# Tutorial
Si buscas un tutorial mira aquí -> [https://github.com/Diphendara/MPCFillToPDF/wiki/Tutorial](https://github.com/Diphendara/MPCFillToPDF/wiki/1-%E2%80%90-Tutorial)

# Sugerencias, bugs etc
Puedes buscarme en twitter/bsky como @diphendara o abrirme una issue por github


# MPCFillToPDF Suite

Suite integral de herramientas para jugadores y coleccionistas de juegos de cartas coleccionables (Magic: The Gathering, One Piece, Lorcana, Riftbound). 

Permite:
1. **🏠 Splash Hub & Navegación Modular**: Acceso rápido y fluido mediante barra de navegación superior persistente a todos los módulos.
2. **📄 Generador de PDFs**: Convierte proyectos de [MPCFill](https://mpcfill.com/) (XML) o mazos desde URLs en PDFs optimizados para imprenta (A4, 3×3 cartas por página, doble cara con dorsos espejados y líneas de corte).
3. **🔍 Validador Técnico**: Audita mazos (Moxfield/Scryfall) comprobando resolución mínima (300 DPI), nitidez (filtro Laplaciano contra imágenes borrosas) y disponibilidad de artes en alta calidad con previsualización flotante instantánea.
4. **📦 Inventario de Cartas & Gestor de Mazos**: Base de datos SQLite local (`inventario.db`), importación de colecciones desde CSV de **ManaBox** (modos *Merge* o *Replace*), cálculo inteligente de stock físico vs. proxies necesarios para imprimir y visor flotante de imágenes al pasar el ratón.

---

## 🚀 Novedades y Módulos

### 1. Inventario de Cartas y Gestor de Mazos (`src/inventory/`)
- **Base de Datos Local SQLite (`MPCFillToPDF/inventario.db`)**: 100% portable y persistente entre ejecuciones y actualizaciones.
- **Importador de CSV de ManaBox**:
  - **Combinar (Merge)**: Actualiza cantidades y versiones de cartas físicas sin alterar tus mazos ni borrar registros previos.
  - **Sustituir (Replace)**: Reinicia la colección física no asignada cargando el nuevo archivo limpio.
- **Resolución de Proxies para Mazos de Moxfield**:
  - Lee cualquier URL de mazo y calcula el stock disponible frente a las copias requeridas.
  - Genera con un solo clic el PDF exclusivo con **únicamente las cartas que te faltan por imprimir**.
- **Volcado de Mazo a Inventario**: Permite incorporar todas las cartas de un mazo directamente a tu colección (como cartas físicas o como proxies).
- **Previsualización Flotante al Pasar el Ratón (Hover Preview)**: Muestra el arte oficial descargado y cacheado en `MPCFillToPDF/procesamiento/previews/` al posicionar el cursor sobre cualquier fila de las tablas.

### 2. Motor de Tareas Asíncrono (`src/engine/`)
- Permite generar PDFs pesados o importar miles de cartas en segundo plano sin congelar la ventana gráfica, con indicador de estado y progreso en la barra superior.

---

## Webs soportadas para diferentes TCG

### Magic: The Gathering
- **Moxfield, Archidekt, TappedOut, ManaBox**: Pega la URL del mazo para validarlo, importarlo a tu inventario o generar el PDF de proxies faltantes.

### One Piece Card Game

| Web | URL de ejemplo |
|-----|---------------|
| [onepiece.gg](https://onepiece.gg) | `https://onepiece.gg/decks/nombre-del-mazo` |
| [deckbuilder.egmanevents.com](https://deckbuilder.egmanevents.com) | `https://deckbuilder.egmanevents.com/?deck=CARTA:X,...` o `https://deckbuilder.egmanevents.com/d/CODIGO` |
| [deckbuilder.cardkaizoku.com](https://deckbuilder.cardkaizoku.com) | `https://deckbuilder.cardkaizoku.com/?deck=2xOP01-001\|3xOP01-002\|...` |

---

### Riftbound TCG

| Web | URL de ejemplo |
|-----|---------------|
| [riftbound.gg](https://riftbound.gg) | `https://riftbound.gg/decks/nombre-del-mazo/` |
| [piltoverarchive.com](https://piltoverarchive.com) | `https://piltoverarchive.com/decks/view/<UUID>` |
| [riftmana.com](https://riftmana.com) | `https://riftmana.com/decks/nombre-del-mazo` |
| [riftbinder.com](https://riftbinder.com) | `https://riftbinder.com/decks/<ID>` |
| [riftdex.com](https://riftdex.com) | `https://riftdex.com/deck/<UUID>` |

---

### Lorcana

| Web | URL de ejemplo |
|-----|---------------|
| [lorcana.gg](https://lorcana.gg) | `https://lorcana.gg/decks/nombre-del-mazo/` |
| [inkdecks.com](https://inkdecks.com) | `https://inkdecks.com/lorcana-metagame/deck-nombre-ID` |
| [dreamborn.ink](https://dreamborn.ink) | `https://dreamborn.ink/es/decks/ID` |

---

## Instalación y Ejecución

### 1. Requisitos previos
- **Python 3.10 o superior** ([descarga oficial](https://www.python.org/downloads/)).

### 2. Entorno virtual e instalación
```bash
python -m venv .venv
source .venv/bin/activate    # En Linux / macOS
.venv\Scripts\activate       # En Windows

pip install -r requirements.txt
```

### 3. Lanzar la aplicación
```bash
python -m gui.main
```

### 4. Modo por lotes en línea de comandos (CLI)
```bash
python -m cli.main
```
Procesa todos los archivos `.xml` ubicados en `xml/` y deposita los PDFs resultantes en `out/`.

### 5. Empaquetar como `.exe` portable (Windows)
```bash
pip install pyinstaller
python build_exe.py
```
Genera `dist/MPCFillToPDF.exe`. El ejecutable es completamente portable: en el directorio donde se ejecute creará automáticamente:
- `MPCFillToPDF/inventario.db` (Base de datos SQLite de tu colección y mazos).
- `MPCFillToPDF/archivos generados/` (PDFs listos para imprimir).
- `MPCFillToPDF/procesamiento/` (Caché de imágenes intermedias y miniaturas).

---

## Estructura del Proyecto

```
MPCFillToPDF/
├── src/
│   ├── engine/                # Motor de tareas en segundo plano (TaskManager, colas de eventos)
│   ├── inventory/             # Dominio de Inventario SQLite, importador ManaBox y solver de proxies
│   │   ├── db.py              # Esquema SQLite, WAL mode e índices
│   │   ├── models.py          # Modelos InventoryCard, DeckRecord, MissingProxyPlan
│   │   ├── manabox_csv.py     # Parser CSV con soporte Merge y Replace
│   │   └── service.py         # CRUD de colección y asignación de stock
│   ├── parser.py              # Parser de XMLs de MPCFill
│   ├── downloader.py          # Descargador concurrente con control de reintentos
│   ├── cropper.py             # Recorte de sangrado MPC y generación de espejo
│   ├── pdf_generator.py       # Ensamblado de PDFs (ReportLab) con límites de tamaño
│   ├── pipeline.py            # Orquestador del flujo de impresión
│   ├── scryfall.py            # Cliente API Scryfall y gestión de caché
│   └── quality.py             # Análisis de resolución y nitidez (Laplaciano)
├── gui/
│   ├── main.py                # Punto de entrada de la aplicación GUI
│   ├── app.py                 # Ventana única, enrutador y ciclo de vida
│   ├── paths.py               # Resolución de rutas de datos persistentes
│   ├── components/            # Widgets reutilizables (NavBar, CardHoverPreview)
│   └── views/                 # Pantallas (HubView, InventoryView, GeneratorView, ValidatorView, SettingsView)
├── cli/                       # Procesador desatendido en consola
├── docs/                      # Documentos de arquitectura, ADRs y planes
└── tests/                     # Suite completa de pruebas automatizadas (635+ tests)
```
