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

def fix_internal_links(soup, current_file_id, all_file_ids):
    """
    Rewrite internal hyperlinks to work within a single document.
    Handles cross-file references by prefixing with target file ID.
    
    Args:
        soup: BeautifulSoup object of current chapter
        current_file_id: ID of the current file (e.g., "xhtml_13_chapter01")
        all_file_ids: Set of all file IDs in the document for validation
    """
    for a in soup.find_all('a', href=True):
        href = a['href']
        
        # Skip external links
        if href.startswith(('http://', 'https://', 'mailto:', 'tel:')):
            continue
        
        # Handle page number references (special case - remove link functionality)
        if '#page_' in href:
            # Convert page links to plain text since page numbers won't match
            anchor_id = href.split('#')[1] if '#' in href else None
            if anchor_id and anchor_id.startswith('page_'):
                # Keep the text but remove the link
                a.unwrap()
                continue
        
        # Parse file and anchor
        if '#' in href:
            parts = href.split('#')
            file_part = parts[0]
            anchor_part = parts[1]
            
            if file_part and file_part.endswith(('.html', '.xhtml', '.htm')):
                # Cross-file reference: extract target file ID
                target_file_id = os.path.splitext(os.path.basename(file_part))[0]
                # Link will be: #targetfile_anchorid
                a['href'] = f"#{target_file_id}_{anchor_part}"
            else:
                # Same-file reference: prefix with current file
                a['href'] = f"#{current_file_id}_{anchor_part}"
                
        elif href.endswith(('.html', '.xhtml', '.htm')):
            # Link to a file without anchor - link to the file's section
            target_file_id = os.path.splitext(os.path.basename(href))[0]
            a['href'] = f"#{target_file_id}"

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
            
            # Add an ID to the chapter container for internal linking
            chapter_id = os.path.splitext(os.path.basename(item.file_name))[0]
            
            # Collect all file IDs for validation (do this once before the loop ideally)
            # For now, we'll pass None and skip validation
            all_file_ids = None
            
            # Fix internal links FIRST (convert file-based to anchor-based with chapter prefixes)
            fix_internal_links(soup, chapter_id, all_file_ids)
            
            # Then deduplicate IDs (this adds the same prefix to all IDs in this chapter)
            deduplicate_ids(soup, chapter_id)
            
            # Fix image paths - need to pass the HTML file's path for relative resolution
            html_file_path = os.path.join(base_root, item.file_name)
            fix_image_paths(soup, base_root, html_file_path)

            # Extract body content
            body_content = extract_body_content(soup)
            
            # Wrap in a section with ID for navigation
            section_wrapper = f'<section id="{chapter_id}" class="chapter">\n{body_content}\n</section>'
            
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
