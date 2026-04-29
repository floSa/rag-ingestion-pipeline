from dagster import asset
import glob
import os

@asset(group_name="pre_processing")
def pre_process_pdf() -> list[str]:
    """Identifie et valide les fichiers PDF à traiter."""
    source_dir = "/opt/dagster/app/Datas"
    files = glob.glob(f"{source_dir}/**/*.pdf", recursive=True)
    
    valid_pdfs = []
    for f in files:
        if os.path.exists(f) and os.path.getsize(f) > 0:
            valid_pdfs.append(f)
            
    return valid_pdfs
