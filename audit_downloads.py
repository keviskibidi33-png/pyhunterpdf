
import csv
from pathlib import Path
from collections import Counter

csv_path = Path(r"c:\Users\Lenovo\Desktop\pdfinacal\PDFINCAL\auditoria_completa.csv")
pdfs_dir = Path(r"c:\Users\Lenovo\Desktop\pdfinacal\PDFINCAL\pdfs")

def run_audit():
    if not csv_path.exists():
        print(f"Error: CSV not found at {csv_path}")
        return

    records = []
    # Try different encodings
    for enc in ['utf-8', 'latin-1', 'cp1252', 'utf-8-sig']:
        try:
            with open(csv_path, 'r', encoding=enc) as f:
                # Use semicolon delimiter
                reader = csv.DictReader(f, delimiter=';')
                for row in reader:
                    records.append(row)
            if records:
                print(f"DEBUG: Successfully read {len(records)} records with {enc}")
                break
        except Exception as e:
            print(f"DEBUG: Failed with {enc}: {e}")
            records = []
            continue

    if not records:
        print("No records found.")
        return

    completados = [r for r in records if r.get('status_descarga') == 'Completado']
    errores = [r for r in records if r.get('status_descarga') == 'Error']
    
    # Filenames that supposedly were saved
    saved_filenames = [r.get('archivo_guardado_como') for r in completados if r.get('archivo_guardado_como')]
    
    # Detect collisions
    counts = Counter(saved_filenames)
    collisions = {name: count for name, count in counts.items() if count > 1}
    
    # Actual files in dir
    actual_files = set(f.name for f in pdfs_dir.glob("*.pdf"))
    
    print(f"\n--- RESULTADOS AUDITORÍA ---")
    print(f"Total registros en CSV: {len(records)}")
    print(f"Registros con estado 'Completado': {len(completados)}")
    print(f"Registros con estado 'Error': {len(errores)}")
    print(f"Nombres de archivo (únicos) en CSV: {len(counts)}")
    print(f"Archivos reales físicamente en carpeta: {len(actual_files)}")
    
    missing_due_to_collision = len(saved_filenames) - len(counts)
    print(f"Archivos 'perdidos' por tener el mismo nombre (sobrescritura): {missing_due_to_collision}")

    if collisions:
        print(f"\nAuditando colisión crítica: 'INFORME.pdf'")
        informe_urls = [r['url_inicial'] for r in completados if r.get('archivo_guardado_como') == 'INFORME.pdf']
        for i, url in enumerate(informe_urls[:10]):
            print(f"  {i+1}. {url}")
        print(f"  ... y {len(informe_urls)-10} URLs más que generan el mismo nombre.")
            
    # Which "Completado" files are actually missing?
    missing_from_disk = []
    for r in completados:
        fname = r.get('archivo_guardado_como')
        if fname and fname not in actual_files:
            missing_from_disk.append((r.get('id'), fname))
            
    # print(f"\nArchivos marcados como 'Completado' que NO están en disco: {len(missing_from_disk)}")

if __name__ == "__main__":
    run_audit()
