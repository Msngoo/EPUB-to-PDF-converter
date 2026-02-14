import os
import logging
from pathlib import Path
from urllib.parse import urljoin, urlparse
from ebooklib import epub
from bs4 import BeautifulSoup
from weasyprint import HTML, CSS
from tqdm import tqdm

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def is_html_content(item):
    """Check if an item is HTML content."""
    if isinstance(item, epub.EpubHtml):
        return True
    if hasattr(item, 'media_type'):
        if 'html' in item.media_type.lower() or 'xhtml' in item.media_type.lower():
            return True
    if hasattr(item, 'file_name'):
        if item.file_name.endswith(('.html', '.xhtml', '.htm')):
            return True
    return False

def fix_image_paths(soup, base_path, html_file_path):
    """
    Convert all relative image paths to absolute file:// URLs.
    base_path: root of extracted EPUB (e.g., /tmp/epub2pdf_xxx/EPUB)
    html_file_path: path to the current HTML file being processed
    """
    # Get the directory containing the HTML file
    html_dir = os.path.dirname(html_file_path)
    
    for img in soup.find_all('img'):
        if img.get('src'):
            src = img['src']
            
            # Skip if already absolute
            if src.startswith(('http://', 'https://', 'file://', 'data:')):
                continue
            
            # Resolve path relative to the HTML file's location
            if src.startswith('/'):
                # Absolute path within EPUB
                img_path = os.path.join(base_path, src.lstrip('/'))
            else:
                # Relative path - resolve from HTML file's directory
                img_path = os.path.join(html_dir, src)
            
            # Normalize the path (resolve ../ references)
            img_path = os.path.normpath(img_path)
            
            # Check if file exists
            if os.path.exists(img_path):
                # Convert to file:// URL
                img['src'] = f"file://{img_path}"
                logger.debug(f"Fixed image: {src} -> {img['src']}")
            else:
                # Try case-insensitive search
                img_dir = os.path.dirname(img_path)
                img_name = os.path.basename(img_path)
                
                if os.path.exists(img_dir):
                    for file in os.listdir(img_dir):
                        if file.lower() == img_name.lower():
                            found_path = os.path.join(img_dir, file)
                            img['src'] = f"file://{found_path}"
                            logger.debug(f"Fixed image (case-insensitive): {src} -> {img['src']}")
                            break
                    else:
                        logger.warning(f"Image not found: {img_path}")
                else:
                    logger.warning(f"Image directory not found: {img_dir}")

def collect_css_files(book, base_path):
    """
    Extract all CSS content from the EPUB and return as a single string.
    """
    css_content = []
    
    # Iterate through all items and find CSS files
    for item in book.get_items():
        # Check if it's a CSS file by media type or file extension
        is_css = False
        
        if hasattr(item, 'media_type') and item.media_type:
            if 'css' in item.media_type.lower():
                is_css = True
        
        if hasattr(item, 'file_name') and item.file_name:
            if item.file_name.endswith('.css'):
                is_css = True
        
        if not is_css:
            continue
        
        try:
            content = item.get_content().decode('utf-8', errors='ignore')
            
            # Fix relative URLs in CSS (for background images, fonts, etc.)
            import re
            
            def fix_css_url(match):
                url = match.group(1).strip('\'"')
                if url.startswith(('http://', 'https://', 'file://', 'data:')):
                    return match.group(0)
                
                # Build absolute path
                css_dir = os.path.dirname(os.path.join(base_path, item.file_name))
                abs_path = os.path.normpath(os.path.join(css_dir, url))
                
                if os.path.exists(abs_path):
                    return f"url('file://{abs_path}')"
                return match.group(0)
            
            # Fix url() references in CSS
            content = re.sub(r'url\(["\']?([^)]+?)["\']?\)', fix_css_url, content)
            
            css_content.append(content)
            logger.info(f"Collected CSS: {item.file_name}")
            
        except Exception as e:
            logger.warning(f"Failed to process CSS {item.file_name}: {e}")
    
    if not css_content:
        logger.warning("No CSS files found in EPUB")
    
    return '\n\n'.join(css_content)

def extract_toc_with_hierarchy(book):
    """
    Extract the hierarchical table of contents from EPUB.
    Returns a list of tuples: (level, title, href)
    where level indicates indentation depth (0 = root, 1 = child, etc.)
    """
    toc_entries = []
    
    def process_toc_item(item, level=0):
        """Recursively process TOC items."""
        if isinstance(item, tuple):
            # Format: (Section, [children]) or (title, href)
            if len(item) == 2:
                section, children = item
                if hasattr(section, 'title') and hasattr(section, 'href'):
                    # It's a Section object
                    toc_entries.append((level, section.title, section.href))
                    # Process children
                    if isinstance(children, list):
                        for child in children:
                            process_toc_item(child, level + 1)
                elif isinstance(section, str):
                    # Format: (title, href)
                    toc_entries.append((level, section, children))
        elif hasattr(item, 'title') and hasattr(item, 'href'):
            # It's a Section object directly
            toc_entries.append((level, item.title, item.href))
        elif isinstance(item, list):
            # It's a list of items
            for subitem in item:
                process_toc_item(subitem, level)
    
    # Get TOC from book
    try:
        toc = book.toc
        if isinstance(toc, list):
            for item in toc:
                process_toc_item(item, 0)
        logger.info(f"Extracted {len(toc_entries)} TOC entries")
    except Exception as e:
        logger.warning(f"Could not extract TOC: {e}")
        return []
    
    return toc_entries

def build_anchor_to_page_map(pdf_path, id_registry, file_to_prefix):
    """
    Parse the PDF to find which page each anchor ID appears on.
    Returns a dict mapping anchor_id -> page_number (0-indexed).
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        try:
            from PyPDF2 import PdfReader
        except ImportError:
            logger.error("pypdf or PyPDF2 not installed. Cannot map anchors to pages.")
            return {}
    
    logger.info("Scanning PDF pages to map anchors...")
    reader = PdfReader(pdf_path)
    anchor_to_page = {}
    
    total_pages = len(reader.pages)
    
    # Build a set of all possible anchor IDs we're looking for
    all_anchor_ids = set()
    for key in id_registry.keys():
        all_anchor_ids.add(key)
        # Also add just the anchor part after #
        if '#' in key:
            all_anchor_ids.add(key.split('#', 1)[1])
    
    for value in id_registry.values():
        all_anchor_ids.add(value)
    
    for value in file_to_prefix.values():
        all_anchor_ids.add(value)
    
    # Search through each page
    found_count = 0
    for page_num in range(total_pages):
        try:
            page = reader.pages[page_num]
            
            # Extract text content
            text = page.extract_text() if hasattr(page, 'extract_text') else ""
            
            # Also check annotations and links
            if '/Annots' in page:
                annotations = page['/Annots']
                if annotations:
                    for annot in annotations:
                        annot_obj = annot.get_object()
                        # Check for named destinations or anchors
                        if '/Dest' in annot_obj:
                            dest = str(annot_obj['/Dest'])
                            for anchor_id in all_anchor_ids:
                                if anchor_id in dest:
                                    if anchor_id not in anchor_to_page:
                                        anchor_to_page[anchor_id] = page_num
                                        found_count += 1
            
            # Search for anchor IDs in the text content
            # WeasyPrint may embed IDs as part of the rendered content
            for anchor_id in all_anchor_ids:
                if anchor_id in text and anchor_id not in anchor_to_page:
                    anchor_to_page[anchor_id] = page_num
                    found_count += 1
            
        except Exception as e:
            logger.debug(f"Error scanning page {page_num}: {e}")
            continue
    
    logger.info(f"Mapped {found_count} anchors to pages (out of {len(all_anchor_ids)} total)")
    
    return anchor_to_page

def add_bookmarks_to_pdf(pdf_path, toc_entries, id_registry, file_to_prefix):
    """
    Add hierarchical bookmarks to an existing PDF file with accurate page numbers.
    
    Args:
        pdf_path: Path to the PDF file
        toc_entries: List of (level, title, href) tuples from extract_toc_with_hierarchy
        id_registry: Global ID registry mapping
        file_to_prefix: File to prefix mapping
    """
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        try:
            from PyPDF2 import PdfReader, PdfWriter
        except ImportError:
            logger.error("pypdf or PyPDF2 not installed. Cannot add bookmarks.")
            return
    
    import os
    
    logger.info(f"Adding bookmarks to PDF: {len(toc_entries)} entries")
    
    # First, build a map of anchor IDs to page numbers
    anchor_to_page = build_anchor_to_page_map(pdf_path, id_registry, file_to_prefix)
    
    # Read the existing PDF
    reader = PdfReader(pdf_path)
    writer = PdfWriter()
    
    # Copy all pages
    for page in reader.pages:
        writer.add_page(page)
    
    # Build bookmark hierarchy
    # Stack to track parent bookmarks: [(level, bookmark_object), ...]
    parent_stack = []
    bookmarks_added = 0
    bookmarks_skipped = 0
    
    for level, title, href in toc_entries:
        try:
            # Parse the href to get the target ID
            target_id = None
            page_num = 0  # Default to first page
            
            if '#' in href:
                file_part, anchor_part = href.split('#', 1)
                
                # Try to resolve using the ID registry
                lookup_keys = [
                    href,  # Full href as-is
                    f"{file_part}#{anchor_part}",
                    anchor_part,  # Just the anchor
                ]
                
                # Add basename variant
                if file_part:
                    basename = os.path.basename(file_part)
                    lookup_keys.append(f"{basename}#{anchor_part}")
                
                # Find the prefixed ID in registry
                for key in lookup_keys:
                    if key in id_registry:
                        target_id = id_registry[key]
                        break
                
                # If not found in registry, try file_to_prefix for file-only
                if not target_id and file_part:
                    for key in [file_part, os.path.basename(file_part)]:
                        if key in file_to_prefix:
                            target_id = file_to_prefix[key]
                            break
                
                # Last resort: use the anchor directly
                if not target_id:
                    target_id = anchor_part
                    
            elif href.endswith(('.html', '.xhtml', '.htm')):
                # File-only reference (no anchor)
                lookup_keys = [href, os.path.basename(href)]
                for key in lookup_keys:
                    if key in file_to_prefix:
                        target_id = file_to_prefix[key]
                        break
            
            # Look up the page number for this target ID
            if target_id:
                # Try various forms of the target ID
                search_keys = [
                    target_id,
                    target_id.lower(),
                    target_id.replace('_', '-'),
                ]
                
                for key in search_keys:
                    if key in anchor_to_page:
                        page_num = anchor_to_page[key]
                        break
            
            # Adjust the parent stack to the current level
            while len(parent_stack) > 0 and parent_stack[-1][0] >= level:
                parent_stack.pop()
            
            # Determine the parent bookmark
            parent = parent_stack[-1][1] if parent_stack else None
            
            # Add the bookmark
            new_bookmark = writer.add_outline_item(
                title=title,
                page_number=page_num,
                parent=parent
            )
            
            # Add to stack for potential children
            parent_stack.append((level, new_bookmark))
            
            bookmarks_added += 1
            logger.debug(f"Added bookmark: {'  ' * level}{title} -> page {page_num + 1}")
            
        except Exception as e:
            logger.warning(f"Error adding bookmark '{title}': {e}")
            bookmarks_skipped += 1
            continue
    
    # Write the PDF with bookmarks
    temp_path = pdf_path + ".tmp"
    with open(temp_path, 'wb') as output_file:
        writer.write(output_file)
    
    # Replace the original file
    import shutil
    shutil.move(temp_path, pdf_path)
    
    logger.info(f"✓ Successfully added {bookmarks_added} bookmarks to PDF")
    if bookmarks_skipped > 0:
        logger.warning(f"  Skipped {bookmarks_skipped} bookmarks due to errors")

def build_global_id_registry(book, spine_items, base_root):
    """
    Pass 1: Scan all chapters and build a mapping of original IDs to prefixed IDs.
    Returns:
        - id_registry: {original_id: prefixed_id, "file.xhtml#id": prefixed_id}
        - file_to_prefix: {file_path: chapter_prefix}
    """
    id_registry = {}
    file_to_prefix = {}
    
    logger.info("Building global ID registry (Pass 1)...")
    
    for item_id, _ in spine_items:
        try:
            item = book.get_item_with_id(item_id)
            
            if not item or not is_html_content(item):
                continue
            
            # Get content
            try:
                content = item.get_content().decode('utf-8', errors='ignore')
            except:
                content = item.content.decode('utf-8', errors='ignore')
            
            soup = BeautifulSoup(content, 'html.parser')
            
            # Generate unique chapter prefix from full file path to avoid collisions
            chapter_prefix = item.file_name.replace('/', '_').replace('\\', '_')
            chapter_prefix = os.path.splitext(chapter_prefix)[0]
            
            # Store the mapping from file path to prefix
            file_to_prefix[item.file_name] = chapter_prefix
            
            # Register all IDs in this chapter
            for element in soup.find_all(id=True):
                original_id = element['id']
                prefixed_id = f"{chapter_prefix}_{original_id}"
                
                # Store both formats for lookup flexibility
                id_registry[original_id] = prefixed_id  # For same-file refs
                id_registry[f"{item.file_name}#{original_id}"] = prefixed_id  # For cross-file refs
                
                # Also handle just the basename for cross-file refs
                basename = os.path.basename(item.file_name)
                id_registry[f"{basename}#{original_id}"] = prefixed_id
            
            logger.debug(f"Registered IDs from: {item.file_name} (prefix: {chapter_prefix})")
            
        except Exception as e:
            logger.warning(f"Error scanning {item_id} for registry: {e}")
            continue
    
    logger.info(f"Registry complete: {len(id_registry)} ID mappings, {len(file_to_prefix)} files")
    return id_registry, file_to_prefix


def fix_internal_links_with_registry(soup, current_file_path, current_prefix, id_registry, file_to_prefix):
    """
    Pass 2: Update links using the global ID registry.
    
    Args:
        soup: BeautifulSoup object of current chapter
        current_file_path: Full file path of current chapter (e.g., "OEBPS/text/ch01.xhtml")
        current_prefix: Chapter prefix for this file (e.g., "OEBPS_text_ch01")
        id_registry: Global mapping of IDs
        file_to_prefix: Mapping of file paths to their prefixes
    """
    for a in soup.find_all('a', href=True):
        href = a['href']
        
        # Skip external links
        if href.startswith(('http://', 'https://', 'mailto:', 'tel:')):
            continue
        
        # Handle page number references (remove functionality)
        if '#page_' in href:
            anchor_id = href.split('#')[1] if '#' in href else None
            if anchor_id and anchor_id.startswith('page_'):
                a.unwrap()
                continue
        
        # Parse href
        if '#' in href:
            file_part, anchor_part = href.split('#', 1)
            
            if file_part:
                # Cross-file reference
                # Try different lookup formats
                lookup_keys = [
                    f"{file_part}#{anchor_part}",  # Full path with anchor
                    f"{os.path.basename(file_part)}#{anchor_part}",  # Basename with anchor
                ]
                
                # Try to resolve the target directory relative to current file
                if not file_part.startswith('/'):
                    current_dir = os.path.dirname(current_file_path)
                    resolved_path = os.path.normpath(os.path.join(current_dir, file_part))
                    lookup_keys.append(f"{resolved_path}#{anchor_part}")
                
                found = False
                for key in lookup_keys:
                    if key in id_registry:
                        a['href'] = f"#{id_registry[key]}"
                        found = True
                        break
                
                if not found:
                    logger.warning(f"Broken cross-file link in {current_file_path}: {href} (tried: {lookup_keys})")
            else:
                # Same-file reference
                prefixed_anchor = f"{current_prefix}_{anchor_part}"
                if anchor_part in id_registry:
                    # If the anchor is registered globally, use its prefixed version
                    a['href'] = f"#{id_registry[anchor_part]}"
                else:
                    # Fallback: use current file's prefix
                    a['href'] = f"#{prefixed_anchor}"
                    logger.debug(f"Same-file link fallback: {href} -> {a['href']}")
                    
        elif href.endswith(('.html', '.xhtml', '.htm')):
            # Link to a file without anchor - link to the chapter section
            # Try to find the target file's prefix
            lookup_keys = [href, os.path.basename(href)]
            
            if not href.startswith('/'):
                current_dir = os.path.dirname(current_file_path)
                resolved_path = os.path.normpath(os.path.join(current_dir, href))
                lookup_keys.append(resolved_path)
            
            found = False
            for key in lookup_keys:
                if key in file_to_prefix:
                    a['href'] = f"#{file_to_prefix[key]}"
                    found = True
                    break
            
            if not found:
                logger.warning(f"Broken file link in {current_file_path}: {href}")

def extract_body_content(soup):
    """
    Extract just the body content, stripping out <html>, <head>, <body> tags.
    Returns the inner HTML of the body.
    """
    body = soup.find('body')
    if body:
        return body
    # If no body tag, return the whole soup
    return soup

def process_epub(input_path, output_path, temp_dir):
    """
    Main conversion function using single-document approach.
    """
    logger.info(f"Loading EPUB: {input_path}")
    book = epub.read_epub(input_path)
    
    # Find the base directory for assets
    base_root = temp_dir
    for root, dirs, files in os.walk(temp_dir):
        for file in files:
            if file.endswith('.opf'):
                base_root = root
                logger.info(f"Found OPF base: {base_root}")
                break
    
    # Collect all CSS
    logger.info("Collecting CSS stylesheets...")
    all_css = collect_css_files(book, base_root)
    
    # Process spine to build master HTML
    spine_items = list(book.spine)
    logger.info(f"Found {len(spine_items)} spine items")
    
    chapter_htmls = []
    processed_count = 0

    # Build global ID registry BEFORE processing chapters
    logger.info("Pass 1: Building global ID registry...")
    id_registry, file_to_prefix = build_global_id_registry(book, spine_items, base_root)
    logger.info(f"Registry complete: {len(file_to_prefix)} files indexed")

    # Extract TOC structure for bookmarks
    logger.info("Extracting table of contents...")
    toc_entries = extract_toc_with_hierarchy(book)

    
    logger.info("Processing chapters...")
    for i, (item_id, _) in enumerate(tqdm(spine_items, desc="Building master document")):
        try:
            item = book.get_item_with_id(item_id)
            
            if not item or not is_html_content(item):
                continue
            
            # Get content
            try:
                content = item.get_content().decode('utf-8', errors='ignore')
            except:
                content = item.content.decode('utf-8', errors='ignore')
            
            # Parse HTML
            soup = BeautifulSoup(content, 'html.parser')
            
            # Get the chapter prefix from registry (using full path for uniqueness)
            chapter_prefix = file_to_prefix.get(item.file_name)
            
            if not chapter_prefix:
                # Fallback if not in registry
                chapter_prefix = item.file_name.replace('/', '_').replace('\\', '_')
                chapter_prefix = os.path.splitext(chapter_prefix)[0]
                logger.warning(f"Chapter {item.file_name} not in registry, using fallback prefix")
            
            # Fix internal links using global registry
            fix_internal_links_with_registry(soup, item.file_name, chapter_prefix, id_registry, file_to_prefix)
            
            # Deduplicate IDs
            deduplicate_ids(soup, chapter_prefix)
            
            # Fix image paths - need to pass the HTML file's path for relative resolution
            html_file_path = os.path.join(base_root, item.file_name)
            fix_image_paths(soup, base_root, html_file_path)

            # Extract body content
            body_content = extract_body_content(soup)
            
            # Wrap in a section with ID for navigation (use chapter_prefix for consistency)
            section_wrapper = f'<section id="{chapter_prefix}" class="chapter">\n{body_content}\n</section>'
            
            chapter_htmls.append(section_wrapper)
            processed_count += 1
            
        except Exception as e:
            logger.error(f"Error processing {item_id}: {e}")
            import traceback
            traceback.print_exc()
            continue
    
    logger.info(f"Successfully processed {processed_count}/{len(spine_items)} items")
    
    if processed_count == 0:
        raise Exception("No content was successfully processed!")
    
    # Build master HTML document
    logger.info("Building master HTML document...")
    
    master_html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Converted EPUB</title>
    <style>
        /* Original EPUB CSS */
        {all_css}
        
        /* PDF-specific pagination rules */
        @page {{
            size: A4;
            margin: 2cm;
        }}
        
        /* Ensure sections start on new page if desired */
        section.chapter {{
            page-break-before: auto;
        }}
        
        /* Image handling */
        img {{
            max-width: 100%;
            height: auto;
            page-break-inside: avoid;
        }}
        
        /* Prevent awkward breaks */
        h1, h2, h3, h4, h5, h6 {{
            page-break-after: avoid;
        }}
        
        /* Ensure links are colored and underlined */
        a {{
            color: #0066cc;
            text-decoration: underline;
        }}
    </style>
</head>
<body>
    {''.join(chapter_htmls)}
</body>
</html>
"""
    
    # Render to PDF
    logger.info("Rendering PDF (this may take several minutes for large files)...")
    
    try:
        # Use base_url to help resolve any remaining relative references
        HTML(string=master_html, base_url=f"file://{base_root}/").write_pdf(
            output_path,
            stylesheets=None,  # CSS is already inlined
        )
        
        final_size = os.path.getsize(output_path)
        logger.info(f"✓ PDF created successfully: {final_size:,} bytes ({final_size/1024/1024:.1f} MB)")

        # Add bookmarks to the PDF
        if toc_entries:
            logger.info("Adding table of contents bookmarks...")
            add_bookmarks_to_pdf(output_path, toc_entries, id_registry, file_to_prefix)
        else:
            logger.warning("No TOC found in EPUB, skipping bookmark creation")
        
    except Exception as e:
        logger.error(f"Error rendering PDF: {e}")
        import traceback
        traceback.print_exc()
        raise

def deduplicate_ids(soup, chapter_prefix):
    """
    Add a prefix to all IDs and update href references to prevent duplicates.
    """
    id_map = {}
    
    # First pass: rename all IDs
    for element in soup.find_all(id=True):
        old_id = element['id']
        new_id = f"{chapter_prefix}_{old_id}"
        element['id'] = new_id
        id_map[old_id] = new_id
    
    # Second pass: update all internal href references
    for a in soup.find_all('a', href=True):
        href = a['href']
        if href.startswith('#'):
            old_id = href[1:]
            if old_id in id_map:
                a['href'] = f"#{id_map[old_id]}"
