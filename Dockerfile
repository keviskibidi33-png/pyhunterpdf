# ============================================================
# THE PDF HUNTER - Dockerfile
# Optimizado para Coolify / Docker deployment
# Subdominio: py.geofal.com.pe
# ============================================================

FROM python:3.11-slim

# Metadatos
LABEL maintainer="PDF Hunter"
LABEL description="Aplicación web para descarga masiva de PDFs con auditoría"

# Variables de entorno
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Directorio de trabajo
WORKDIR /app

# Instalar dependencias del sistema (pdfplumber + curl para healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copiar requirements e instalar dependencias Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código de la aplicación
COPY . .

# Crear directorios necesarios
RUN mkdir -p downloads temp_pdfs

# Exponer puerto
EXPOSE 8085

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8085/ || exit 1

# Comando de inicio
CMD ["python", "main.py"]
