# EPUB to PDF Converter

A Python command-line tool for converting large EPUB textbooks to high-quality PDFs while preserving styling, images, colors, and structure. Optimized for handling massive files (600+ MB EPUBs).

## Project Structure

```
epub2pdf_converter/
├── README.md                # This file
├── pyproject.toml          # Project dependencies (managed by uv)
├── uv.lock                 # Dependency lock file (managed by uv)
├── .python-version         # Python version specification
└── src/
    ├── __init__.py         # Package initialization
    ├── main.py             # CLI entry point
    ├── converter.py        # Core conversion logic
    └── utils.py            # File handling utilities
```

### File Descriptions

- **`main.py`**: Handles command-line arguments and orchestrates the conversion workflow
- **`converter.py`**: Contains the main conversion engine:
  - HTML/CSS extraction and processing
  - Image path resolution
  - Internal link fixing
  - Single-document PDF rendering
- **`utils.py`**: Utility functions for extracting and cleaning up temporary files

## Features

- Preserves original EPUB styling (colors, fonts, layout)  
- Maintains all images with proper resolution  
- Handles internal hyperlinks and cross-references  
- Optimized for large files (670+ MB EPUBs)  
- Progress bar for tracking conversion  
- Auto-generates output filename from input  

## Prerequisites

### System Requirements

- **macOS** (Intel or Apple Silicon)
- **Homebrew** package manager
- **Python 3.13+**

### System Dependencies

Install GTK3 libraries required by WeasyPrint:

```bash
brew install pango libffi
```

Set the library path (add to your `~/.zshrc` or `~/.bash_profile`):

```bash
# For Apple Silicon Macs (M1/M2/M3):
export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib:$DYLD_FALLBACK_LIBRARY_PATH

# For Intel Macs:
export DYLD_FALLBACK_LIBRARY_PATH=/usr/local/lib:$DYLD_FALLBACK_LIBRARY_PATH
```

After adding, reload your shell:
```bash
source ~/.zshrc  # or source ~/.bash_profile
```

## Installation

### 1. Clone or Download the Project

```bash
git clone <repository-url> epub2pdf_converter
cd epub2pdf_converter
```

Or if you received the project as a ZIP file, extract it and navigate to the directory:
```bash
cd epub2pdf_converter
```

### 2. Install Dependencies from Lock File

The project includes a `uv.lock` file that ensures you get the exact same dependency versions that were tested. Install them with:

```bash
uv sync
```

This will:
- Read the exact package versions from `uv.lock`
- Create a virtual environment (`.venv/`)
- Install all required dependencies

**That's it!** You're ready to convert EPUBs.

### Alternative: Manual Dependency Installation (Not Recommended)

If you need to start fresh without the lock file:

```bash
uv add ebooklib beautifulsoup4 weasyprint pypdf tqdm click
```

This will create a new `uv.lock` file with the latest compatible versions.

## Usage

### Basic Usage (Auto-named Output)

Convert an EPUB to PDF with the same filename:

```bash
uv run src/main.py path/to/textbook.epub
```

**Example:**
```bash
uv run src/main.py ~/Documents/Neuroscience_Textbook.epub
```

This creates `Neuroscience_Textbook.pdf` in the same directory as the input file.

### Custom Output Name

Specify a custom output filename using the `-o` flag:

```bash
uv run src/main.py input.epub -o custom_name.pdf
```

**Example:**
```bash
uv run src/main.py ~/Documents/Textbook.epub -o ~/Desktop/MyBook.pdf
```

### Tab Completion

The tool supports shell tab-completion for file paths. Just start typing and press **Tab**:

```bash
uv run src/main.py ~/Doc<TAB>
# Auto-completes to: ~/Documents/
```

Files with spaces in names are automatically handled by your shell.

## How It Works

The converter uses a **single-document approach** for optimal quality:

1. **Extraction**: Unzips the EPUB to access HTML, CSS, and image files
2. **CSS Collection**: Gathers all stylesheets to preserve original formatting
3. **HTML Processing**: For each chapter:
   - Converts image paths to absolute file:// URLs
   - Fixes internal hyperlinks for single-document navigation
   - Deduplicates anchor IDs to prevent conflicts
4. **Master Document**: Concatenates all chapters into one HTML file with inline CSS
5. **PDF Rendering**: WeasyPrint renders the complete document in a single pass
6. **Cleanup**: Removes temporary files

### Why Single-Document?

Unlike chunk-and-merge approaches, rendering the entire book as one PDF:
- Preserves internal hyperlinks
- Maintains consistent styling across chapters
- Avoids merge artifacts
- More memory-efficient than expected (WeasyPrint streams rendering)

## Troubleshooting

### "cannot load library 'libgobject-2.0-0'"

**Solution**: Ensure you've installed GTK3 libraries and set `DYLD_FALLBACK_LIBRARY_PATH`:

```bash
brew install pango libffi
export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib:$DYLD_FALLBACK_LIBRARY_PATH
```

### "No module named 'src'"

**Solution**: Always run the script from the project root directory:

```bash
cd epub2pdf_converter
uv run src/main.py input.epub
```

### Images Not Appearing in PDF

**Cause**: EPUB uses relative paths that need to be resolved.

**Solution**: The script automatically handles this. If images are still missing, check that the EPUB file isn't corrupted by opening it in an EPUB reader first.

### Conversion Takes Too Long

**Expected behavior**: Large EPUBs (500+ MB) can take 2-3+ minutes to render. You'll see a progress bar during chapter processing. The final "Rendering PDF" step is the longest but shows no progress—be patient!

### Output PDF is Huge

**Normal**: A 670 MB EPUB typically produces a 400-600 MB PDF because images are uncompressed in the PDF format. This is expected for image-heavy textbooks.

## Known Limitations

- **Page number links**: Links to print page numbers (e.g., "see page 42") are removed since PDF pagination differs
- **Some cross-references**: Complex cross-chapter links may not resolve if the EPUB has non-standard linking
- **CSS warnings**: Some proprietary CSS (webkit gradients, invalid values) generates warnings but doesn't affect output
- **Duplicate anchors**: EPUBs with repeated IDs may show warnings but are automatically handled

## Technical Stack

- **EbookLib**: EPUB parsing and extraction
- **BeautifulSoup4**: HTML manipulation
- **WeasyPrint**: HTML/CSS to PDF rendering engine
- **pypdf**: PDF metadata handling
- **Click**: CLI framework
- **tqdm**: Progress bars
- **uv**: Fast Python package and project manager

## License

This tool is provided as-is for personal and educational use.