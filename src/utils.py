import os
import zipfile
import shutil

def extract_epub(epub_path, extract_dir):
    """Unzips the EPUB to a temporary directory."""
    if not os.path.exists(extract_dir):
        os.makedirs(extract_dir)
    
    with zipfile.ZipFile(epub_path, 'r') as zip_ref:
        zip_ref.extractall(extract_dir)
    
    return extract_dir

def cleanup_temp(extract_dir):
    """Removes the temporary directory."""
    if os.path.exists(extract_dir):
        shutil.rmtree(extract_dir)
