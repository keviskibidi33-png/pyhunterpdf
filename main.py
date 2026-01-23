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
async def download_single_pdf(
    session: aiohttp.ClientSession,
    url: str,
    index: int,
    progress_callback
) -> dict:
    """Descarga un PDF siguiendo redirecciones y rastrea la cadena completa"""
    
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
    
    try:
        async with session.get(
            url, 
            headers=HEADERS,
            max_redirects=MAX_REDIRECTS,
            timeout=aiohttp.ClientTimeout(total=60),
            allow_redirects=True
        ) as response:
            
            # Registrar cadena de redirecciones
            redirect_chain = [url]
            for hist in response.history:
                redirect_chain.append(str(hist.url))
            redirect_chain.append(str(response.url))
            
            # Eliminar duplicados consecutivos
            unique_chain = [redirect_chain[0]]
            for u in redirect_chain[1:]:
                if u != unique_chain[-1]:
                    unique_chain.append(u)
            
            result['cadena_redirecciones'] = ' -> '.join(unique_chain)
            result['num_redirecciones'] = len(unique_chain) - 1
            result['url_final'] = str(response.url)
            result['http_code'] = response.status
            
            if response.status == 200:
                content_type = response.headers.get('Content-Type', '')
                
                # Intentar obtener nombre real desde Content-Disposition o URL
                disposition = response.headers.get('Content-Disposition', '')
                filename = None
                
                if 'filename=' in disposition:
                    # Extraer el nombre entre comillas o hasta el punto y coma
                    match = re.search(r'filename=["\']?([^"\';\s]+)["\']?', disposition)
                    if match:
                        filename = match.group(1)
                
                if not filename:
                    # Fallback a la última parte de la URL
                    filename = str(response.url).split('/')[-1].split('?')[0]
                
                # Limpiar caracteres inválidos
                filename = re.sub(r'[\\/*?:"<>|]', '_', filename)
                
                if not filename.lower().endswith('.pdf'):
                    filename += '.pdf'
                
                # Verificar que sea PDF
                if 'pdf' in content_type.lower() or filename.lower().endswith('.pdf'):
                    filepath = TEMP_DIR / filename
                    
                    content = await response.read()
                    async with aiofiles.open(filepath, 'wb') as f:
                        await f.write(content)
                    
                    result['archivo_guardado_como'] = filename
                    result['tamano_bytes'] = len(content)
                    result['status_descarga'] = 'Completado'
                    
                    # Extraer fecha en threadpool (no bloquea)
                    loop = asyncio.get_event_loop()
                    fecha = await loop.run_in_executor(
                        executor, 
                        extract_emission_date, 
                        filepath
                    )
                    result['fecha_emision_pdf'] = fecha
                else:
                    result['error_detalle'] = f'No es PDF: {content_type[:50]}'
            else:
                result['error_detalle'] = f'HTTP {response.status}'
                
    except asyncio.TimeoutError:
        result['error_detalle'] = 'Timeout (60s)'
    except aiohttp.ClientError as e:
        result['error_detalle'] = f'Error de red: {str(e)[:100]}'
    except Exception as e:
        result['error_detalle'] = f'Error: {str(e)[:100]}'
    
    # Escribir al CSV inmediatamente (no acumular en memoria)
    append_to_csv(result)
    
    # Actualizar callback para UI
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
    
    # Variables de estado de UI
    links_to_download = []
    
    # =========================================================================
    # CALLBACKS
    # =========================================================================
    async def on_file_upload(e: events.UploadEventArguments):
        nonlocal links_to_download
        
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

            links_to_download = parse_links_file(content, filename)
            
            if links_to_download:
                msg = f'✅ Se cargaron {len(links_to_download):,} enlaces correctamente'
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
        """Callback para actualizar UI después de cada descarga"""
        state.completed += 1
        
        if result['status_descarga'] == 'Completado':
            state.success_count += 1
        else:
            state.error_count += 1
        
        # Mantener solo los últimos 50 en memoria para la tabla
        state.recent_downloads.insert(0, result)
        if len(state.recent_downloads) > UI_TABLE_MAX_ROWS:
            state.recent_downloads.pop()
        
        # Actualizar UI
        progress_bar.value = state.completed / state.total if state.total > 0 else 0
        progress_text.text = f'{state.completed:,} / {state.total:,}'
        success_count.text = f'✅ {state.success_count:,}'
        error_count.text = f'❌ {state.error_count:,}'
        
        # Actualizar tabla
        table.rows = [
            {
                'url': r['url_inicial'][:60] + ('...' if len(r['url_inicial']) > 60 else ''),
                'status': '✅ Completado' if r['status_descarga'] == 'Completado' else '❌ Error',
                'redirects': f"{r['num_redirecciones']} saltos",
                'date': r['fecha_emision_pdf'][:20] if r['fecha_emision_pdf'] else '-',
                'error': r['error_detalle'][:40] if r['error_detalle'] else '-'
            }
            for r in state.recent_downloads[:15]
        ]
        
        await asyncio.sleep(0)  # Yield para actualizar UI
    
    async def start_download():
        nonlocal links_to_download
        
        if not links_to_download:
            ui.notify('⚠️ Primero carga un archivo con enlaces', type='warning')
            return
        
        # Limpiar estado
        cleanup()
        
        state.total = len(links_to_download)
        state.completed = 0
        state.success_count = 0
        state.error_count = 0
        state.is_running = True
        state.is_paused = False
        state.recent_downloads = []
        
        # Actualizar UI
        start_btn.disable()
        pause_btn.enable()
        stop_btn.enable()
        download_btn.disable()
        upload.disable()
        
        progress_bar.value = 0
        progress_text.text = f'0 / {state.total:,}'
        
        ui.notify(f'🚀 Iniciando descarga de {state.total:,} archivos...', type='positive')
        
        try:
            await download_all_pdfs(links_to_download, update_progress)
        finally:
            state.is_running = False
            start_btn.enable()
            pause_btn.disable()
            stop_btn.disable()
            download_btn.enable()
            upload.enable()
            
            ui.notify(
                f'✨ Proceso completado: {state.success_count:,} OK, {state.error_count:,} errores',
                type='positive' if state.error_count == 0 else 'warning'
            )
    
    def pause_download():
        state.is_paused = not state.is_paused
        pause_btn.text = '▶️ Reanudar' if state.is_paused else '⏸️ Pausar'
        ui.notify('⏸️ Pausado' if state.is_paused else '▶️ Reanudado')
    
    def stop_download():
        state.is_running = False
        ui.notify('🛑 Deteniendo...', type='warning')
    
    async def download_results():
        if not CSV_FILE.exists():
            ui.notify('⚠️ No hay resultados para descargar', type='warning')
            return
        
        ui.notify('📦 Creando ZIP...', type='info')
        
        loop = asyncio.get_event_loop()
        zip_path = await loop.run_in_executor(executor, create_final_zip)
        
        ui.download(str(zip_path))
        ui.notify('✅ Descarga iniciada', type='positive')
    
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
