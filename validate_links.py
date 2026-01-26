#!/usr/bin/env python3
"""
Validador de Links - Verifica links de coso.txt sin descargar los PDFs
Genera un reporte rápido del estado de cada link
"""

import asyncio
import aiohttp
from pathlib import Path
from datetime import datetime

INPUT_FILE = Path("coso.txt")
OUTPUT_REPORT = Path("validation_report.txt")
MAX_CONCURRENT = 50
TIMEOUT = 10

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'application/pdf,*/*',
}

async def check_link(session, url, index):
    """Verifica un link sin descargarlo (solo HEAD request)"""
    result = {
        'index': index,
        'url': url,
        'status': 'ERROR',
        'http_code': 0,
        'content_type': '',
        'error': ''
    }
    
    try:
        # Usar GET pero sin leer el contenido, solo headers
        async with session.get(
            url,
            headers=HEADERS,
            timeout=aiohttp.ClientTimeout(total=TIMEOUT),
            allow_redirects=True
        ) as response:
            result['http_code'] = response.status
            result['content_type'] = response.headers.get('Content-Type', '')
            
            if response.status == 200:
                # Verificar si es PDF
                if 'pdf' in result['content_type'].lower():
                    result['status'] = 'OK'
                else:
                    result['status'] = 'NOT_PDF'
                    result['error'] = f"Content-Type: {result['content_type']}"
            else:
                result['error'] = f"HTTP {response.status}"
    
    except asyncio.TimeoutError:
        result['error'] = 'Timeout'
    except aiohttp.ClientError as e:
        result['error'] = f'Network: {str(e)[:50]}'
    except Exception as e:
        result['error'] = f'Error: {str(e)[:50]}'
    
    return result

async def validate_all_links():
    """Valida todos los links del archivo"""
    
    # Leer links
    with open(INPUT_FILE, 'r', encoding='utf-8') as f:
        links = [line.strip() for line in f if line.strip().startswith('http')]
    
    total = len(links)
    print(f"🔍 Validando {total} links...")
    print(f"⚙️  Concurrencia: {MAX_CONCURRENT}")
    print(f"⏱️  Timeout: {TIMEOUT}s")
    print("-" * 60)
    
    results = {
        'ok': 0,
        'error': 0,
        'notpdf': 0,
        'details': []
    }
    
    connector = aiohttp.TCPConnector(limit=MAX_CONCURRENT, ssl=False)
    
    async with aiohttp.ClientSession(connector=connector) as session:
        semaphore = asyncio.Semaphore(MAX_CONCURRENT)
        
        async def bounded_check(url, index):
            async with semaphore:
                result = await check_link(session, url, index)
                
                # Actualizar contadores
                if result['status'] == 'OK':
                    results['ok'] += 1
                elif result['status'] == 'NOT_PDF':
                    results['notpdf'] += 1
                else:
                    results['error'] += 1
                
                results['details'].append(result)
                
                # Progreso cada 100
                if (index + 1) % 100 == 0:
                    print(f"  Progreso: {index + 1}/{total} | OK: {results['ok']} | Errores: {results['error']}")
                
                return result
        
        tasks = [bounded_check(url, i) for i, url in enumerate(links)]
        await asyncio.gather(*tasks)
    
    # Mostrar resumen
    print("\n" + "=" * 60)
    print("📊 RESUMEN")
    print("=" * 60)
    print(f"✅ Links OK (PDF válido):        {results['ok']}")
    print(f"⚠️  Links que NO son PDF:        {results['notpdf']}")
    print(f"❌ Links con error:              {results['error']}")
    print(f"📝 Total validado:               {total}")
    print(f"📈 Tasa de éxito:                {(results['ok']/total*100):.2f}%")
    
    # Guardar reporte
    with open(OUTPUT_REPORT, 'w', encoding='utf-8') as f:
        f.write(f"=== REPORTE DE VALIDACIÓN ===\n")
        f.write(f"Fecha: {datetime.now().isoformat()}\n")
        f.write(f"Archivo: {INPUT_FILE}\n")
        f.write(f"Total links: {total}\n\n")
        
        f.write(f"RESUMEN:\n")
        f.write(f"  OK: {results['ok']}\n")
        f.write(f"  NOT PDF: {results['notpdf']}\n")
        f.write(f"  ERROR: {results['error']}\n\n")
        
        # Links con error
        if results['error'] > 0 or results['notpdf'] > 0:
            f.write("\n=== LINKS CON PROBLEMAS ===\n\n")
            for r in results['details']:
                if r['status'] != 'OK':
                    f.write(f"[{r['index']+1}] {r['status']} - {r['url']}\n")
                    f.write(f"    Error: {r['error']}\n")
                    f.write(f"    HTTP: {r['http_code']}\n\n")
    
    print(f"\n📄 Reporte guardado en: {OUTPUT_REPORT}")

if __name__ == "__main__":
    asyncio.run(validate_all_links())
