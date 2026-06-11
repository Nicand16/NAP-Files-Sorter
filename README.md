# NAP Files-Sorter

NAP Files-Sorter organiza automaticamente una carpeta elegida por el usuario, normalmente Descargas. Corre en segundo plano, usa reglas locales y LLM (Groq como proveedor principal, Gemini como respaldo) cuando hace falta, y mueve los archivos a carpetas ordenadas sin que el usuario tenga que editar configuraciones.

Version actual: `1.1.0` (ver [CHANGELOG.md](CHANGELOG.md)).

## Para Usuarios

1. Descarga `nap_v1.1.0.zip` desde [Releases](https://github.com/Nicand16/Files-Sorter/releases/latest).
2. Extrae el zip completo.
3. Ejecuta `Install.bat`.
4. Selecciona la carpeta a organizar y pega tu API key de Groq (obligatoria) y opcionalmente la de Gemini.

Despues de instalar, puedes manejar lo importante desde NAP Monitor o desde el icono de bandeja:

- Cambiar carpeta monitoreada.
- Cambiar API key de Groq o Gemini y recargarla sin reiniciar.
- Pausar o reanudar la organizacion.
- Forzar un escaneo inmediato.
- Deshacer el ultimo movimiento.
- Abrir la carpeta `Documentos por Revisar`.
- Ver logs e historial.

No se necesita Python en el equipo del usuario final.

## LLM con Fallback Inteligente

NAP Files-Sorter usa **Groq** como proveedor LLM principal (14.400 req/dia gratis) con **Gemini** como respaldo automatico. Si Groq falla, el agente conmuta a Gemini en tiempo real sin necesidad de reiniciar. Ambos proveedores tienen circuit breakers independientes con tiempos de recuperacion inteligentes.

## Seguridad

NAP Files-Sorter no borra archivos permanentemente. Si una herramienta de limpieza necesita retirar un archivo basura, lo mueve a:

```text
_NAP Quarantine\AAAA-MM
```

Los documentos ambiguos no se mandan directamente a `Varios` cuando hay poca confianza. Se revisan con nombre, tipo, metadatos y contenido parcial; si siguen sin evidencia suficiente, van a:

```text
7. Varios\Documentos por Revisar
```

## Carpetas de Destino

| Carpeta | Contenido |
|---|---|
| `1. Universidad y Estudio` | Tareas, libros, modulos, tramites academicos |
| `2. Software y Herramientas` | Instaladores, comprimidos, portables |
| `3. Juegos y Emulacion` | ROMs, ISOs, torrents, emuladores |
| `4. Multimedia` | Imagenes, videos, audio |
| `5. Trabajo y Empleo` | CVs, contratos, ofertas, procesos de seleccion |
| `6. Documentos Personales` | Identificacion, impuestos, facturas, salud |
| `7. Varios` | Revision manual y archivos sin categoria clara |

## Documentacion

| Documento | Descripcion |
|---|---|
| [MANUAL_USO.md](MANUAL_USO.md) | Guia para instalar y usar NAP Files-Sorter como usuario final |
| [README_WINDOWS.md](README_WINDOWS.md) | Guia tecnica rapida para desarrollo/build en Windows |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Arquitectura, pipeline, IPC, base de datos y build |

## Desarrollo

```powershell
cd briner_agent
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

python -m pytest tests/ -q
```

Build local:

```powershell
cd briner_agent
python -m PyInstaller --clean --noconfirm NAPSorter.spec
python -m PyInstaller --clean --noconfirm NAPBackground.spec
python -m PyInstaller --clean --noconfirm NAPMonitor.spec
```

Release:

```powershell
Compress-Archive -Path "release\nap_v1.1.0\*" -DestinationPath "nap_v1.1.0.zip" -Force
```
