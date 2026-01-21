import click
import tempfile
from pathlib import Path
from utils import extract_epub, cleanup_temp
from converter import process_epub

@click.command()
@click.argument('input_path', type=click.Path(exists=True, path_type=Path))
@click.option(
    '--output', '-o', 
    type=click.Path(path_type=Path), 
    required=False, 
    help='Optional output path. Defaults to input filename with .pdf extension.'
)
def main(input_path, output):
    """
    Converts large EPUB files to PDF while preserving styling, images, and links.
    
    INPUT_PATH: The path to the .epub file to convert.
    """
    if output is None:
        output = input_path.with_suffix('.pdf')
    
    print(f"Input:  {input_path}")
    print(f"Output: {output}")
    
    temp_dir = tempfile.mkdtemp(prefix="epub2pdf_")
    
    try:
        print("Extracting EPUB assets...")
        extract_epub(str(input_path), temp_dir)
        
        print("Converting to PDF...")
        process_epub(str(input_path), str(output), temp_dir)
        
        print(f"\n✓ Conversion complete: {output}")
        
    except Exception as e:
        print(f"\n✗ An error occurred: {e}")
        raise
    finally:
        print("Cleaning up temporary files...")
        cleanup_temp(temp_dir)

if __name__ == '__main__':
    main()
