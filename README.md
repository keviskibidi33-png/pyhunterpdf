# The PDF Hunter

🎯 Aplicación web para descarga masiva de PDFs con auditoría completa de redirecciones.

## Características

- **Descarga Masiva**: Procesa miles de enlaces de forma asíncrona
- **Rastreo de Redirecciones**: Captura la cadena completa de redirecciones (hasta 10 niveles)
- **Extracción de Fechas**: Extrae automáticamente la fecha de emisión de cada PDF
- **Auditoría Completa**: Genera CSV detallado con toda la información técnica
- **UI en Tiempo Real**: Barra de progreso y tabla de descargas actualizada en vivo
- **Eficiente en Memoria**: Escribe al CSV línea por línea, sin acumular en RAM

## Instalación Local

```bash
# Instalar dependencias
pip install -r requirements.txt

# Ejecutar aplicación
python main.py
```

Abrir navegador en: http://localhost:8080

## Despliegue con Docker

```bash
# Construir y ejecutar
docker-compose up -d

# Ver logs
docker-compose logs -f
```

## Despliegue en Coolify

1. Sube este repositorio a GitHub
2. En Coolify, crea un nuevo servicio usando el Dockerfile
3. Expone el puerto 8080
4. ¡Listo!

## Formato de Archivo de Entrada

### Archivo .txt
```
https://ejemplo.com/documento1.pdf
https://ejemplo.com/documento2.pdf
https://ejemplo.com/documento3.pdf
```

### Archivo .csv
```csv
id,link,descripcion
1,https://ejemplo.com/doc1.pdf,Documento 1
2,https://ejemplo.com/doc2.pdf,Documento 2
```

## Columnas del CSV de Auditoría

| Columna | Descripción |
|---------|-------------|
| id | Número secuencial |
| url_inicial | URL original del input |
| url_final | URL que entregó el PDF |
| cadena_redirecciones | Trace completo (url1 -> url2 -> url3) |
| num_redirecciones | Cantidad de saltos |
| status_descarga | SUCCESS / ERROR |
| http_code | Código HTTP final |
| error_detalle | Descripción del error (si aplica) |
| fecha_emision_pdf | Fecha extraída del PDF |
| archivo_guardado_como | Nombre del archivo local |
| tamano_bytes | Tamaño del archivo |
| timestamp | Fecha/hora de la descarga |

## Stack Tecnológico

- **NiceGUI**: Framework web moderno con WebSocket
- **aiohttp**: Cliente HTTP asíncrono
- **pdfplumber**: Extracción de texto de PDFs
- **aiofiles**: I/O asíncrono para archivos
