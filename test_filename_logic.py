
import unittest
from pathlib import Path
from main import determine_best_filename
import shutil

# Mock Temp Dir for collision tests
TEMP_DIR = Path("./test_temp_pdfs")

class TestFilenameLogic(unittest.TestCase):
    
    def test_priority_url_slug(self):
        """Caso 1: El slug de la URL es un código válido (>3 chars). DEBE GANAR."""
        final_url = "https://example.com/IN-N-015-25-SU19"
        initial_url = "https://initial.com/123"
        disposition = 'attachment; filename="informe.pdf"'
        
        result = determine_best_filename(final_url, initial_url, disposition)
        self.assertEqual(result, "IN-N-015-25-SU19", "Debe priorizar el código URL sobre el nombre genérico")

    def test_priority_initial_url_slug(self):
        """Caso 2: La URL final es genérica, pero la inicial tenía código."""
        final_url = "https://example.com/view/documento.pdf"
        initial_url = "https://docqr.geofal.com.pe/auth/IN-N-044-25"
        disposition = 'attachment; filename="descarga.pdf"'
        
        result = determine_best_filename(final_url, initial_url, disposition)
        self.assertEqual(result, "IN-N-044-25", "Debe recuperar el código de la URL inicial si la final se perdió")

    def test_ignore_meaningless_slugs(self):
        """Caso 3: El slug es muy corto o irrelevante."""
        final_url = "https://example.com/pdf"
        initial_url = "https://example.com/go"
        disposition = 'attachment; filename="Manual_Tecnico.pdf"'
        
        result = determine_best_filename(final_url, initial_url, disposition)
        self.assertEqual(result, "Manual_Tecnico.pdf", "Debe usar el nombre del servidor si los slugs son basura")

    def test_collision_logic_simulated(self):
        """Simulación de la lógica de colisión (copiada de main.py para verificar)"""
        # Setup
        if TEMP_DIR.exists(): shutil.rmtree(TEMP_DIR)
        TEMP_DIR.mkdir()
        
        # Crear archivo original
        original = TEMP_DIR / "IN-N-015.pdf"
        original.touch()
        
        # Verificar lógica de counter
        filename = "IN-N-015.pdf"
        filepath = TEMP_DIR / filename
        
        counter = 1
        stem = filepath.stem
        suffix = filepath.suffix
        while filepath.exists():
            filepath = TEMP_DIR / f"{stem}_({counter}){suffix}"
            counter += 1
            
        expected_name = "IN-N-015_(1).pdf"
        self.assertEqual(filepath.name, expected_name, "Debe generar sufijo (1) para evitar colisión")
        
        # Crear la colisión nivel 1
        filepath.touch()
        
        # Verificar nivel 2
        filepath = TEMP_DIR / "IN-N-015.pdf"
        counter = 1
        while filepath.exists():
            filepath = TEMP_DIR / f"{stem}_({counter}){suffix}"
            counter += 1
            
        expected_name_2 = "IN-N-015_(2).pdf"
        self.assertEqual(filepath.name, expected_name_2, "Debe generar sufijo (2) si el (1) existe")
        
        # Cleanup
        shutil.rmtree(TEMP_DIR)

if __name__ == '__main__':
    print("\n🔬 INICIANDO VALIDACIÓN QUIRÚRGICA DE NOMBRES Y COLISIONES\n")
    unittest.main()
