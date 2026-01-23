"""
🎯 THE PDF HUNTER
Aplicación web para descarga masiva de PDFs con auditoría completa.
Desarrollado con NiceGUI + aiohttp + pdfplumber
"""

import os
import re
import csv
import shutil
import asyncio
import zipfile
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
from concurrent.futures import ThreadPoolExecutor

import aiohttp
import aiofiles
import pdfplumber
from nicegui import ui, app, events

# =============================================================================
# CONFIGURACIÓN
# =============================================================================
DOWNLOAD_DIR = Path("./downloads")
TEMP_DIR = Path("./temp_pdfs")
CSV_FILE = DOWNLOAD_DIR / "auditoria_completa.csv"
MAX_REDIRECTS = 10
MAX_CONCURRENT_DOWNLOADS = 10
UI_TABLE_MAX_ROWS = 50

# Regex mejorado para fechas (soporta YYYY-MM-DD, DD/MM/YYYY, DD-MM-YY y variantes con espacios)
DATE_REGEX = re.compile(
    r'(?:FECHA|F\.?)\s*(?:DE\s+)?EMISI[OÓ]N[:\.\s]*'
    r'(\d{2,4}[\.\-/]\s*\d{2}[\.\-/]\s*\d{2,4})',
    re.IGNORECASE
)

# Headers para simular navegador
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/pdf,*/*',
}

# =============================================================================
# ESTADO GLOBAL
# =============================================================================
@dataclass
class DownloadState:
    total: int = 0
    completed: int = 0
    success_count: int = 0
    error_count: int = 0
    is_running: bool = False
    is_paused: bool = False
    links_to_download: list[str] = field(default_factory=list)
    current_filename: str = ""
    recent_downloads: list = field(default_factory=list)  # Solo últimos 50 para UI
    
state = DownloadState()
executor = ThreadPoolExecutor(max_workers=4)

# =============================================================================
# UTILIDADES
# =============================================================================
def cleanup():
    """Limpia archivos de ejecuciones anteriores de forma robusta"""
    def safe_delete(path: Path):
        if not path.exists():
            return
        if path.is_file():
            try:
                path.unlink()
            except PermissionError:
                print(f"WARN: No se pudo eliminar {path} (está siendo usado)")
        elif path.is_dir():
            for child in path.iterdir():
                safe_delete(child)
            try:
                path.rmdir()
            except (PermissionError, OSError):
                 print(f"WARN: No se pudo eliminar carpeta {path}")

    # Limpiar contenido sin borrar las carpetas base necesariamente
    if TEMP_DIR.exists():
        for f in TEMP_DIR.glob('*'): safe_delete(f)
    if DOWNLOAD_DIR.exists():
        for f in DOWNLOAD_DIR.glob('*'):
            # Intentar borrar todo menos el CSV si falla
            safe_delete(f)
    
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    
    # Crear CSV con headers de forma segura
    try:
        with open(CSV_FILE, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                'id', 'url_inicial', 'url_final', 'cadena_redirecciones',
                'num_redirecciones', 'status_descarga', 'http_code', 
                'error_detalle', 'fecha_emision_pdf', 'archivo_guardado_como',
                'tamano_bytes', 'timestamp'
            ])
    except PermissionError:
        ui.notify('⚠️ El archivo CSV está abierto en otro programa. Ciérralo para actualizar los resultados.', type='warning', duration=10)
        print("ERROR: Permission denied on CSV file")
    except Exception as e:
        print(f"CRITICAL ERROR creating CSV: {e}")
        ui.notify(f'⚠️ Error creando CSV: {e}', type='negative')

def extract_emission_date(pdf_path: Path) -> str:
    """Extrae la fecha de emisión del PDF usando pdfplumber"""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            # Buscar en las primeras 3 páginas
            for i, page in enumerate(pdf.pages[:3]):
                text = page.extract_text() or ""
                match = DATE_REGEX.search(text)
                if match:
                    return match.group(1)
        return "NO ENCONTRADA"
    except Exception as e:
        return f"ERROR: {str(e)[:50]}"

def parse_links_file(content: str, filename: str) -> list[str]:
    """Parsea archivo .txt o .csv para extraer links y normaliza caracteres extraños"""
    links = []
    
    # Normalización de encoding mojibake (Común: Â° -> °)
    content = content.replace('\u00c2\u00b0', '\u00b0').replace('\u00c2', '')
    
    if filename.lower().endswith('.csv'):
        # Intentar detectar la columna de links
        import io
        reader = csv.reader(io.StringIO(content))
        try:
            headers = next(reader, None)
        except Exception:
            headers = None
        
        # Buscar columna que contenga 'link', 'url', 'enlace'
        link_col = 0
        if headers:
            for i, h in enumerate(headers):
                if any(kw in h.lower() for kw in ['link', 'url', 'enlace', 'href']):
                    link_col = i
                    break
        
        for row in reader:
            if row and len(row) > link_col:
                url = row[link_col].strip()
                if url.startswith('http'):
                    # Limpiar espacios y caracteres invisibles
                    url = re.sub(r'[\s\u200b\u200c\u200d\ufeff]', '', url)
                    links.append(url)
    else:
        # Archivo TXT: una URL por línea
        for line in content.split('\n'):
            url = line.strip()
            if url.startswith('http'):
                # Limpiar espacios y caracteres invisibles
                url = re.sub(r'[\s\u200b\u200c\u200d\ufeff]', '', url)
                links.append(url)
    
    return links

def append_to_csv(row: dict):
    """Añade una fila al CSV de auditoría de forma segura"""
    try:
        with open(CSV_FILE, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                row['id'],
                row['url_inicial'],
                row['url_final'],
                row['cadena_redirecciones'],
                row['num_redirecciones'],
                row['status_descarga'],
                row['http_code'],
                row['error_detalle'],
                row['fecha_emision_pdf'],
                row['archivo_guardado_como'],
                row['tamano_bytes'],
                row['timestamp']
            ])
    except PermissionError:
        print(f"WARN: No se pudo escribir en el CSV (bloqueado) para ID {row['id']}")

def create_final_zip() -> Path:
    """Crea el ZIP final con PDFs y CSV"""
    zip_path = DOWNLOAD_DIR / f"pdf_hunter_resultado_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        # Añadir CSV
        if CSV_FILE.exists():
            zf.write(CSV_FILE, 'auditoria_completa.csv')
        
        # Añadir PDFs
        for pdf_file in TEMP_DIR.glob('*.pdf'):
            zf.write(pdf_file, f'pdfs/{pdf_file.name}')
    
    return zip_path

# =============================================================================
# MOTOR DE DESCARGA ASYNC
# =============================================================================


def determine_best_filename(final_url: str, initial_url: str, content_disposition: str) -> str:
    """
    Fuerza el uso del slug de la URL INICIAL (ej: IN-IN-N-2605-25-CO12)
    ignorando los nombres raros de la redirección final.
    """
    
    # Lista de nombres que NO queremos (por si acaso)
    GENERIC_NAMES = {
        'login-download-file', 'download', 'file', 'archivo', 'documento', 'ver'
    }

    def get_slug(url: str) -> str:
        if not url: return ""
        # Decodificar %20 y otros
        from urllib.parse import unquote
        url = unquote(url)
        # Quitar parámetros (?id=...)
        base = url.split('?')[0]
        # Obtener lo que está después de la última barra /
        slug = base.split('/')[-1]
        
        # Limpieza especifica para tus links:
        # Si termina en .pdf lo quitamos para normalizar
        if slug.lower().endswith('.pdf'):
            slug = slug[:-4]
            
        return slug.strip()

    # 1. Extraemos el código de TU enlace original
    slug_initial = get_slug(initial_url)
    
    # 2. Extraemos el código del enlace final (el raro)
    slug_final = get_slug(final_url)

    # =========================================================================
    # REGLA DE ORO: La URL inicial MANDA
    # =========================================================================
    
    # Si el slug inicial parece un código válido (tiene longitud y no es "download")
    if slug_initial and len(slug_initial) > 4 and slug_initial.lower() not in GENERIC_NAMES:
        return f"{slug_initial}.pdf"

    # =========================================================================
    # FALLBACKS (Solo si tu URL inicial estuviera vacía o rota)
    # =========================================================================

    # Intentar sacar nombre del servidor (Header Content-Disposition)
    if 'filename=' in content_disposition:
        match = re.search(r'filename\*?=(?:UTF-8\'\')?["\']?([^"\';\s]+)["\']?', content_disposition, re.IGNORECASE)
        if match:
            server_name = match.group(1)
            if server_name.lower().endswith('.pdf'):
                return server_name
            return f"{server_name}.pdf"

    # Último recurso: Usar el nombre raro de la URL final
    if slug_final and len(slug_final) > 4:
        return f"{slug_final}.pdf"

    return f"documento_sin_nombre_{datetime.now().strftime('%H%M%S')}.pdf"

async def download_single_pdf(
    session: aiohttp.ClientSession,
    url: str,
    index: int,
    progress_callback
) -> dict:
    """Descarga un PDF con validación real de magic bytes y reintentos"""
    
    # -----------------------------------------------------------
    # CONFIGURACIÓN DE REINTENTOS Y VALIDACIÓN
    # -----------------------------------------------------------
    MAX_RETRIES = 3  # Intentar 3 veces antes de rendirse
    RETRY_DELAY = 2  # Segundos de espera entre intentos
    # -----------------------------------------------------------

    result = {
        'id': index,
        'url_inicial': url,
        'url_final': url,
        'cadena_redirecciones': url,
        'num_redirecciones': 0,
        'status_descarga': 'Error',
        'http_code': 0,
        'error_detalle': '',
        'fecha_emision_pdf': '',
        'archivo_guardado_como': '',
        'tamano_bytes': 0,
        'timestamp': datetime.now().isoformat()
    }

    for attempt in range(MAX_RETRIES):
        try:
            # Añadimos un pequeño delay aleatorio para no parecer un robot agresivo
            if attempt > 0:
                await asyncio.sleep(RETRY_DELAY * (attempt + 1))
            
            async with session.get(
                url, 
                headers=HEADERS, # Asegúrate de que HEADERS tenga un User-Agent de navegador real
                max_redirects=MAX_REDIRECTS,
                timeout=aiohttp.ClientTimeout(total=90), # Subimos timeout a 90s
                allow_redirects=True,
                ssl=False # Ignorar errores SSL
            ) as response:
                
                # Mapeo de redirecciones
                redirect_chain = [url] + [str(h.url) for h in response.history] + [str(response.url)]
                # Limpiar duplicados manteniendo orden
                seen = set()
                unique_chain = [x for x in redirect_chain if not (x in seen or seen.add(x))]
                
                result['cadena_redirecciones'] = ' -> '.join(unique_chain)
                result['num_redirecciones'] = len(unique_chain) - 1
                result['url_final'] = str(response.url)
                result['http_code'] = response.status
                
                if response.status == 200:
                    content = await response.read()
                    
                    # --- VALIDACIÓN DE BYTES MÁGICOS (La prueba definitiva) ---
                    # Un PDF real SIEMPRE empieza con %PDF
                    if not content.startswith(b'%PDF'):
                        # Si es HTML disfrazado, mostramos el título de la página como error
                        error_preview = content[:200].decode('utf-8', errors='ignore').replace('\n', ' ')
                        if '<html' in error_preview.lower() or '<!doctype' in error_preview.lower():
                            raise ValueError(f"Servidor devolvió HTML/Captcha en lugar de PDF: {error_preview[:50]}...")
                        else:
                            # A veces envían binarios corruptos
                            raise ValueError(f"El archivo descargado no es un PDF válido (Header incorrecto)")

                    # Si pasa la validación, procedemos a guardar
                    content_disposition = response.headers.get('Content-Disposition', '')
                    filename = determine_best_filename(str(response.url), url, content_disposition)
                    
                    # Limpieza segura
                    filename = re.sub(r'[\\/*?:"<>|]', '_', filename)
                    # Forzamos extensión .pdf si no la tiene (ya validamos que el contenido ES pdf)
                    if not filename.lower().endswith('.pdf'):
                        filename += '.pdf'
                    
                    # Guardar archivo
                    filepath = TEMP_DIR / filename
                    counter = 1
                    stem = filepath.stem
                    while filepath.exists(): # Evitar sobrescribir
                        filepath = TEMP_DIR / f"{stem}_({counter}).pdf"
                        counter += 1
                        
                    async with aiofiles.open(filepath, 'wb') as f:
                        await f.write(content)
                    
                    result['archivo_guardado_como'] = filepath.name
                    result['tamano_bytes'] = len(content)
                    result['status_descarga'] = 'Completado'
                    result['error_detalle'] = '' # Limpiar errores previos si hubo reintentos
                    
                    # Extraer fecha (fuera del hilo principal)
                    loop = asyncio.get_event_loop()
                    fecha = await loop.run_in_executor(executor, extract_emission_date, filepath)
                    result['fecha_emision_pdf'] = fecha
                    
                    break # ¡Éxito! Salir del bucle de reintentos
                
                elif response.status in [429, 503, 502, 504]:
                    # Errores temporales del servidor -> Reintentar
                    result['error_detalle'] = f'HTTP {response.status} (Reintentando...)'
                    continue 
                else:
                    # Errores fatales (404, 403 permanente) -> No reintentar
                    result['error_detalle'] = f'HTTP {response.status}'
                    break

        except asyncio.TimeoutError:
            result['error_detalle'] = f'Timeout (Intento {attempt+1}/{MAX_RETRIES})'
        except Exception as e:
            result['error_detalle'] = f'{str(e)[:100]}'
    
    # Guardar resultado final (sea éxito o el último error)
    append_to_csv(result)
    await progress_callback(result)
    return result

async def download_all_pdfs(links: list[str], progress_callback):
    """Descarga todos los PDFs con concurrencia limitada"""
    
    connector = aiohttp.TCPConnector(limit=MAX_CONCURRENT_DOWNLOADS, ssl=False)
    
    async with aiohttp.ClientSession(connector=connector) as session:
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
        
        async def bounded_download(url: str, index: int):
            async with semaphore:
                if state.is_paused:
                    while state.is_paused and state.is_running:
                        await asyncio.sleep(0.5)
                
                if not state.is_running:
                    return None
                    
                return await download_single_pdf(session, url, index, progress_callback)
        
        tasks = [
            bounded_download(url, i + 1) 
            for i, url in enumerate(links)
        ]
        
        await asyncio.gather(*tasks, return_exceptions=True)

# =============================================================================
# INTERFAZ NICEGUI
# =============================================================================
@ui.page('/')
async def main_page():
    """Página principal de la aplicación"""
    
    # Eliminada variable local para persistencia global
    
    # =========================================================================
    # CALLBACKS
    # =========================================================================
    async def on_file_upload(e: events.UploadEventArguments):
        
        # Debug seguro
        print("DEBUG: Upload event received")
        try:
            print(f"DEBUG: Event dir: {dir(e)}")
            if hasattr(e, 'file'):
                print(f"DEBUG: e.file type: {type(e.file)}")
                print(f"DEBUG: e.file attributes: {dir(e.file)}")
        except Exception as e_debug:
            print(f"DEBUG: Could not inspect event: {e_debug}")

        try:
            content_bytes = None
            filename = "uploaded_file.txt" # Default
            
            # Identificar el objeto que tiene los datos
            source_obj = None
            if hasattr(e, 'file') and e.file:
                source_obj = e.file
            elif hasattr(e, 'content') and e.content:
                source_obj = e.content
            
            # Intentar obtener nombre del archivo
            if hasattr(e, 'name'):
                filename = e.name
            elif source_obj and hasattr(source_obj, 'name'):
                filename = source_obj.name
            elif source_obj and hasattr(source_obj, 'filename'): # werkzeug/starlette style
                filename = source_obj.filename
            
            print(f"DEBUG: Processing filename: {filename}")

            if source_obj:
                try:
                    # Intentar leer como stream
                    if hasattr(source_obj, 'read'):
                        if hasattr(source_obj, 'seek'):
                            source_obj.seek(0)
                        
                        # Manejo de read() asíncrono vs síncrono
                        read_result = source_obj.read()
                        if asyncio.iscoroutine(read_result):
                            content_bytes = await read_result
                        else:
                            content_bytes = read_result
                            
                        print(f"DEBUG: Read {len(content_bytes) if content_bytes else 0} bytes from stream")
                    
                    # Intentar leer como bytes directo
                    elif isinstance(source_obj, (bytes, bytearray)):
                        content_bytes = source_obj
                        print(f"DEBUG: Source is direct bytes, len: {len(content_bytes)}")

                except Exception as read_err:
                    print(f"DEBUG: Error reading source: {read_err}")
            
            if content_bytes is None:
                raise ValueError(f"CRITICAL: Content is None. Event structure: {dir(e)}")

            # Decodificación robusta
            content = ""
            decoded = False
            for encoding in ['utf-8', 'latin-1', 'cp1252']:
                try:
                    content = content_bytes.decode(encoding)
                    decoded = True
                    print(f"DEBUG: Decoded successfully with {encoding}")
                    break
                except Exception:
                    continue
            
            if not decoded:
                content = content_bytes.decode('utf-8', errors='ignore')
                print("DEBUG: Decoded with utf-8 (ignore errors)")

            state.links_to_download = parse_links_file(content, filename)
            state.current_filename = filename
            
            if state.links_to_download:
                msg = f'✅ Se cargaron {len(state.links_to_download):,} enlaces desde {filename}'
                links_count.set_text(msg)
                links_count.classes(remove='text-gray-400 text-red-500', add='text-green-500 text-lg font-bold')
                start_btn.enable()
                ui.notify(msg, type='positive')
            else:
                msg = '❌ No se encontraron enlaces válidos'
                links_count.set_text(msg)
                links_count.classes(remove='text-gray-400 text-green-500', add='text-red-500 text-lg')
                start_btn.disable()
                ui.notify(msg, type='negative')
                
        except Exception as ex:
            import traceback
            traceback.print_exc()
            error_msg = str(ex)
            print(f"ERROR UI: {error_msg}")
            
            links_count.set_text(f'❌ Error: {error_msg}')
            links_count.classes(remove='text-gray-400 text-green-500', add='text-red-500 text-lg')
            ui.notify(f'Error: {error_msg}', type='negative', close_button=True, multi_line=True)
    
    async def update_progress(result: dict):
        """Callback que solo actualiza el estado global (seguro para cualquier sesión)"""
        state.completed += 1
        
        if result['status_descarga'] == 'Completado':
            state.success_count += 1
        else:
            state.error_count += 1
        
        # Guardar en historial global para que cualquier ventana nueva lo vea
        state.recent_downloads.insert(0, result)
        if len(state.recent_downloads) > UI_TABLE_MAX_ROWS:
            state.recent_downloads.pop()
        
        await asyncio.sleep(0)
    
    async def start_download():
        if not state.links_to_download:
            try: ui.notify('⚠️ Primero carga un archivo con enlaces', type='warning')
            except Exception: pass
            return
        
        cleanup()
        state.total = len(state.links_to_download)
        state.completed = 0
        state.success_count = 0
        state.error_count = 0
        state.is_running = True
        state.is_paused = False
        state.recent_downloads = []
        
        try: ui.notify(f'🚀 Iniciando descarga...', type='positive')
        except Exception: pass
        
        try:
            await download_all_pdfs(state.links_to_download, update_progress)
        finally:
            state.is_running = False

    def pause_download():
        state.is_paused = not state.is_paused
        try: ui.notify('⏸️ Pausado' if state.is_paused else '▶️ Reanudado')
        except Exception: pass

    def stop_download():
        state.is_running = False
        try: ui.notify('🛑 Deteniendo...', type='warning')
        except Exception: pass

    async def download_results():
        if not CSV_FILE.exists():
            try: ui.notify('⚠️ No hay resultados', type='warning')
            except Exception: pass
            return
        
        try: ui.notify('📦 Creando ZIP...', type='info')
        except Exception: pass
        
        loop = asyncio.get_event_loop()
        zip_path = await loop.run_in_executor(executor, create_final_zip)
        
        try:
            ui.download(str(zip_path))
        except Exception: pass
    
    # =========================================================================
    # LAYOUT
    # =========================================================================
    ui.add_head_html('''
    <style>
        body { 
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
            min-height: 100vh;
        }
        .glass-card {
            background: rgba(255, 255, 255, 0.05);
            backdrop-filter: blur(10px);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 16px;
        }
        .glow-text {
            text-shadow: 0 0 20px rgba(79, 209, 197, 0.5);
        }
    </style>
    ''')
    
    with ui.column().classes('w-full max-w-5xl mx-auto p-6 gap-6'):
        
        # Header
        with ui.row().classes('w-full justify-center items-center gap-4 mb-4'):
            ui.icon('picture_as_pdf', size='xl').classes('text-cyan-400')
            ui.label('THE PDF HUNTER').classes(
                'text-4xl font-bold text-white glow-text tracking-wider'
            )
            ui.icon('download', size='xl').classes('text-cyan-400')
        
        ui.label('Descarga masiva de PDFs con auditoría completa de redirecciones').classes(
            'text-center text-gray-400 text-lg -mt-4 mb-4'
        )
        
        # Upload Section
        with ui.card().classes('glass-card w-full p-6'):
            ui.label('📁 Cargar Lista de Enlaces').classes('text-xl text-white font-semibold mb-4')
            
            with ui.row().classes('w-full gap-4 items-center'):
                upload = ui.upload(
                    label='Arrastra tu archivo .txt o .csv aquí',
                    on_upload=on_file_upload,
                    auto_upload=True
                ).classes('w-full').props('accept=".txt,.csv"')
            
            links_count = ui.label('Esperando archivo...').classes('text-gray-400 text-lg mt-2')
        
        # Control Buttons
        with ui.card().classes('glass-card w-full p-6'):
            with ui.row().classes('w-full justify-center gap-4'):
                start_btn = ui.button(
                    '🚀 INICIAR DESCARGA', 
                    on_click=start_download
                ).classes('bg-gradient-to-r from-cyan-500 to-blue-600 text-white px-8 py-3 text-lg font-bold rounded-xl')
                start_btn.disable()
                
                pause_btn = ui.button(
                    '⏸️ Pausar', 
                    on_click=pause_download
                ).classes('bg-yellow-600 text-white px-6 py-3 rounded-xl')
                pause_btn.disable()
                
                stop_btn = ui.button(
                    '🛑 Detener', 
                    on_click=stop_download
                ).classes('bg-red-600 text-white px-6 py-3 rounded-xl')
                stop_btn.disable()
        
        # Progress Section
        with ui.card().classes('glass-card w-full p-6'):
            ui.label('📊 Progreso').classes('text-xl text-white font-semibold mb-4')
            
            with ui.row().classes('w-full items-center gap-4'):
                progress_bar = ui.linear_progress(value=0, show_value=False).classes(
                    'flex-grow h-4 rounded-full'
                ).props('color="cyan"')
                progress_text = ui.label('0 / 0').classes('text-white text-xl font-mono min-w-32 text-right')
            
            with ui.row().classes('w-full justify-center gap-8 mt-4'):
                success_count = ui.label('✅ 0').classes('text-green-400 text-2xl font-bold')
                error_count = ui.label('❌ 0').classes('text-red-400 text-2xl font-bold')
        
        # Live Table
        with ui.card().classes('glass-card w-full p-6'):
            ui.label('📋 Últimas Descargas').classes('text-xl text-white font-semibold mb-4')
            
            columns = [
                {'name': 'url', 'label': 'URL Original', 'field': 'url', 'align': 'left'},
                {'name': 'status', 'label': 'Estado', 'field': 'status', 'align': 'center'},
                {'name': 'redirects', 'label': 'Redirecciones', 'field': 'redirects', 'align': 'center'},
                {'name': 'date', 'label': 'F. Emisión', 'field': 'date', 'align': 'center'},
                {'name': 'error', 'label': 'Detalle', 'field': 'error', 'align': 'left'},
            ]
            
            table = ui.table(
                columns=columns,
                rows=[],
                row_key='url'
            ).classes('w-full').props('dark dense')
        
        # Download Section
        with ui.card().classes('glass-card w-full p-6'):
            with ui.row().classes('w-full justify-center'):
                download_btn = ui.button(
                    '📦 DESCARGAR ZIP + CSV DE AUDITORÍA',
                    on_click=download_results
                ).classes(
                    'bg-gradient-to-r from-green-500 to-emerald-600 text-white px-10 py-4 text-xl font-bold rounded-xl'
                )
                download_btn.disable()
        
        # Footer
        ui.label('💡 Los PDFs se guardan en el servidor. Al finalizar, descarga el ZIP con todo.').classes(
            'text-center text-gray-500 text-sm mt-4'
        )

    # =========================================================================
    # SINCRONIZACIÓN DE UI (Polling)
    # =========================================================================
    def sync_ui():
        """Sincroniza los componentes de esta ventana con el estado global"""
        try:
            # Actualizar progreso
            progress_bar.value = state.completed / state.total if state.total > 0 else 0
            progress_text.text = f'{state.completed:,} / {state.total:,}'
            success_count.text = f'✅ {state.success_count:,}'
            error_count.text = f'❌ {state.error_count:,}'
            
            # Actualizar botones según estado de ejecución
            if state.is_running:
                start_btn.disable()
                pause_btn.enable()
                stop_btn.enable()
                download_btn.disable()
                upload.disable()
                pause_btn.text = '▶️ Reanudar' if state.is_paused else '⏸️ Pausar'
            else:
                # Mostrar mensaje de éxito si ya terminó y hubo progreso
                if state.total > 0 and state.completed >= state.total:
                    links_count.set_text(f'✨ ¡Análisis completado! ({state.success_count} OK, {state.error_count} Errores)')
                    links_count.classes(remove='text-gray-400 text-red-500', add='text-green-500 text-lg font-bold')
                elif state.links_to_download:
                    links_count.set_text(f'✅ {len(state.links_to_download):,} enlaces listos ({state.current_filename})')
                    links_count.classes(remove='text-gray-400 text-red-500', add='text-green-500 text-lg font-bold')
                
                pause_btn.disable()
                stop_btn.disable()
                download_btn.enable()
                upload.enable()

            # Actualizar tabla (solo si hay cambios en la longitud o contenido básico)
            new_table_rows = [
                {
                    'url': r['url_inicial'][:60] + ('...' if len(r['url_inicial']) > 60 else ''),
                    'status': '✅ Completado' if r['status_descarga'] == 'Completado' else '❌ Error',
                    'redirects': f"{r['num_redirecciones']} saltos",
                    'date': r['fecha_emision_pdf'][:20] if r['fecha_emision_pdf'] else '-',
                    'error': r['error_detalle'][:40] if r['error_detalle'] else '-'
                }
                for r in state.recent_downloads[:15]
            ]
            
            if len(new_table_rows) != len(table.rows):
                table.rows = new_table_rows
            
        except Exception:
            pass # Ignorar errores si la ventana se cierra

    # Timer de refresco constante para recuperar estado tras refresh
    ui.timer(1.0, sync_ui)

# =============================================================================
# INICIAR APLICACIÓN
# =============================================================================
if __name__ in {"__main__", "__mp_main__"}:
    # Crear directorios si no existen
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    
    ui.run(
        title='🎯 The PDF Hunter',
        host='0.0.0.0',
        port=8085,
        reload=False,
        show=False
    )
