# Tutorial
Si buscas un tutorial mira aquí -> [https://github.com/Diphendara/MPCFillToPDF/wiki/Tutorial](https://github.com/Diphendara/MPCFillToPDF/wiki/1-%E2%80%90-Tutorial)

# Sugerencias, bugs etc
Puedes buscarme en twitter/bsky como @diphendara o abrirme una issue por github.


# MPCFillToPDF Suite

Suite integral de herramientas para jugadores y coleccionistas de juegos de cartas coleccionables (Magic: The Gathering, One Piece, Lorcana, Riftbound, Yu-Gi-Oh!).

Permite:
1. **🏠 Splash Hub & Navegación Modular**: Acceso rápido y fluido mediante barra de navegación superior persistente a todos los módulos.
2. **📄 Generador de PDFs**: Convierte proyectos de [MPCFill](https://mpcfill.com/) (XML), mazos desde URLs o imágenes locales en PDFs optimizados para imprenta (A4, 3×3 cartas por página, doble cara con dorsos espejados y líneas de corte).
3. **🔍 Validador Técnico**: Audita mazos (Moxfield/Scryfall) comprobando resolución mínima (300 DPI), nitidez (filtro Laplaciano contra imágenes borrosas) y disponibilidad de artes en alta calidad con previsualización flotante instantánea.
4. **📦 Inventario de Cartas & Gestor de Mazos**: Base de datos SQLite local (`inventario.db`), importación de colecciones desde CSV de **ManaBox** (modos *Merge* o *Replace*), cálculo inteligente de stock físico vs. proxies necesarios para imprimir y visor flotante de imágenes al pasar el ratón.

---

## 🚀 Módulos Principales

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

## 🃏 Webs y Juegos Soportados

### Magic: The Gathering
| Web | Formato de URL |
| --- | --- |
| Moxfield | `https://www.moxfield.com/decks/ID` |
| Archidekt | `https://archidekt.com/decks/ID` |
| Deckstats | `https://deckstats.net/decks/ID/...` |
| TappedOut | `https://tappedout.net/mtg-decks/nombre-del-mazo/` |
| ManaBox | `https://manabox.app/decks/ID` |

Las imágenes se obtienen de **Scryfall**, por edición y número de colección o por nombre cuando faltan esos datos. Admite cartas de dos caras (MDFCs) y selección opcional de tokens.

### One Piece Card Game
| Web | Formato de URL |
| --- | --- |
| [onepiece.gg](https://onepiece.gg) | `https://onepiece.gg/decks/nombre-del-mazo` |
| [deckbuilder.egmanevents.com](https://deckbuilder.egmanevents.com) | `https://deckbuilder.egmanevents.com/?deck=CARTA:X,...` o `https://deckbuilder.egmanevents.com/d/CODIGO` |
| [deckbuilder.cardkaizoku.com](https://deckbuilder.cardkaizoku.com) | `https://deckbuilder.cardkaizoku.com/?deck=2xOP01-001\|3xOP01-002\|...` |

Los líderes usan un reverso específico; el resto usa el reverso estándar de One Piece.

### Riftbound TCG
| Web | Formato de URL |
| --- | --- |
| [riftbound.gg](https://riftbound.gg) | `https://riftbound.gg/decks/nombre-del-mazo/` |
| [piltoverarchive.com](https://piltoverarchive.com) | `https://piltoverarchive.com/decks/view/UUID` |
| [riftmana.com](https://riftmana.com) | `https://riftmana.com/decks/nombre-del-mazo` |
| [riftbinder.com](https://riftbinder.com) | `https://riftbinder.com/decks/ID` |
| [riftdex.com](https://riftdex.com) | `https://riftdex.com/deck/UUID` |

Los reversos se asignan según la sección de la carta (leyenda, campo de batalla, runa o mazo).

### Lorcana
| Web | Formato de URL |
| --- | --- |
| [lorcana.gg](https://lorcana.gg) | `https://lorcana.gg/decks/nombre-del-mazo/` |
| [inkdecks.com](https://inkdecks.com) | `https://inkdecks.com/lorcana-metagame/deck-nombre-ID` |

Todas las cartas usan el reverso de Lorcana.

### Yu-Gi-Oh!
Puedes pegar una URL pública de **YGOPRODeck**, **Master Duel Meta**, **Yu-Gi-Oh! Meta** o **Edison Format**, o una cadena `ydke://` exportada desde un constructor. Las cartas se resuelven con la API de YGOPRODeck. Los mazos de Yu-Gi-Oh! generan un PDF con su propio formato japonés (trim de 59 × 86 mm) y reverso oficial.

---

## 🛠️ Instalación y Ejecución

Necesitas **Python 3.10 o superior**. Se recomienda usar [uv](https://github.com/astral-sh/uv) para la gestión del entorno y dependencias:

```sh
# Sincronizar dependencias
uv sync

# Ejecutar la aplicación gráfica (GUI modular)
uv run python -m gui.main

# Ejecutar la herramienta en línea de comandos (CLI)
uv run python -m cli.main --help
```

---

## 🔑 Google Drive: clave de API opcional

Los XML de MPCFill contienen identificadores de imágenes alojadas en Google Drive. Sin clave, el programa descarga mediante `gdown`. Para usar la API de Drive al ejecutar desde el código, copia `config.example.json` a `config.json` en la raíz y sustituye el valor:

```json
{
  "google_drive_api_key": "TU_CLAVE_DE_API"
}
```

---

## 💻 Línea de Comandos (CLI)

```sh
# Procesar lote de XMLs
uv run python -m cli.main --xml-dir xml --out-dir out --workdir workdir

# Generar a partir de imágenes locales
uv run python -m cli.main --local-fronts c1.jpg c2.png --local-cardback back.jpg --locals-base-name mi_mazo --yes

# Generar directamente desde una URL de mazo
uv run python -m cli.main --deck-url "https://www.moxfield.com/decks/TU_MAZO" --out-dir out
```

| Opción | Comportamiento |
| --- | --- |
| `--xml-dir`, `--out-dir`, `--workdir` | Carpetas de entrada, salida y trabajo; por defecto `xml`, `out` y `workdir`. |
| `--deck-url URL` | Descarga y procesa un mazo directamente desde la URL de la plataforma compatible. |
| `--local-fronts IMG ...` | Añade frontales locales o genera exclusivamente con imágenes locales. |
| `--local-backs IMG ...` | Reversos emparejados por orden con los frontales locales. |
| `--local-cardback IMG` | Reverso predeterminado obligatorio cuando no hay XML. |
| `--locals-base-name NAME` | Nombre base para generación sin XML; por defecto `locales`. |
| `--local-needs-crop` | Recorta el borde MPC de las imágenes locales. |
| `--fronts-only` | Genera solo frontales omitiendo las páginas de traseras. |
| `--workers N` | Número de hilos concurrentes para procesamiento. |
| `--chunk-size N` | Tamaño de bloque para procesamiento. |
| `--test` | Conserva `workdir/raw` y `workdir/bled` al terminar. |
| `--yes`, `-y` | Continúa sin pedir confirmación por huecos o avisos de acceso a Drive. |
| `--verbose`, `-v` | Muestra logs de depuración en stderr además de escribir `run.log`. |

---

## 📐 Formato del PDF y Reglas de Impresión

- **A4 vertical**: 210 × 297 mm, 3 columnas × 3 filas = 9 cartas por página.
- **Tamaño estándar (MTG, One Piece, Lorcana, Riftbound)**: Carta recortada a 63,5 × 88,9 mm con sangrado en espejo de 1 mm (imagen final de 65,5 × 90,9 mm).
- **Tamaño japonés (Yu-Gi-Oh!)**: Carta de 59 × 86 mm (imagen final de 61 × 88 mm).
- **Líneas de corte**: Marcas de corte configurables (color, grosor, modo marcas de margen o líneas continuas cruzadas).
- **División inteligente por tamaño**: Límite configurable de MB por PDF (por defecto 200 MB, hasta 2,5 GB) en los ajustes para evitar archivos excesivamente grandes manteniendo intactas las parejas de doble cara.

---

## 📦 Empaquetado (.exe)

```sh
uv run python build_exe.py
```

Genera `dist/MPCFillToPDF.exe` (Windows) o `dist/MPCFillToPDF` (macOS/Linux) empaquetado de forma autónoma con todos los recursos e iconos.

---

## 🧪 Pruebas Automatizadas

```sh
# Ejecutar toda la suite de pruebas unitarias e integración
uv run pytest

# Verificación de estilo y tipado
uv run ruff check .
uv run ruff format --check .
```
