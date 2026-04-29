from dagster import asset
import glob
import os
from bs4 import BeautifulSoup

@asset(group_name="pre_processing")
def pre_process_html() -> list[str]:
    """
    Nettoie spécifiquement les fichiers HTML en ciblant les contenus superflus
    propres à Packt, O'Reilly, etc.
    """
    source_dir = "/opt/dagster/app/Datas"
    files = glob.glob(f"{source_dir}/**/*.html", recursive=True)
    
    cleaned_files = []
    for f in files:
        if os.path.exists(f) and os.path.getsize(f) > 0:
            try:
                with open(f, 'r', encoding='utf-8') as file:
                    soup = BeautifulSoup(file.read(), 'html.parser')
                
                # Exemples de nettoyage basés sur la requête:
                # O'Reilly = div class="sbo-site-nav", header
                # Packt = div class="packt-header", aside
                for element in soup.find_all(['nav', 'header', 'footer', 'aside']):
                    element.decompose()
                    
                for class_name in ['sbo-site-nav', 'packt-header', 'site-menu']:
                    for element in soup.find_all(class_=class_name):
                        element.decompose()
                
                # Sauvegarde temporaire du fichier nettoyé pour docling
                clean_path = f"{f}.cleaned"
                with open(clean_path, 'w', encoding='utf-8') as out_file:
                    out_file.write(str(soup))
                    
                cleaned_files.append(clean_path)
            except Exception as e:
                print(f"Error processing {f}: {e}")
                
    return cleaned_files
