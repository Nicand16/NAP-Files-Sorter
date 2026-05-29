import importlib.util
import datetime as _dt
import logging
import mimetypes
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

DEFAULT_MAX_CHARS = 3000
TEXT_EXTENSIONS = {".txt", ".csv", ".md", ".json", ".log", ".yaml", ".yml", ".xml"}
DOCUMENT_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff"}
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".webm"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".ogg", ".m4a"}
ARCHIVE_EXTENSIONS = {".zip", ".rar", ".7z", ".tar", ".gz"}


def _trim(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + " [...]"


def _read_text(path: Path, max_chars: int) -> str:
    with open(path, "r", encoding="utf-8", errors="ignore") as file:
        return file.read(max_chars)


def _read_pdf(path: Path, max_chars: int) -> str:
    if not importlib.util.find_spec("pypdf"):
        return "Aviso: PDF detectado, pero pypdf no esta instalado. Clasifica por nombre, extension y metadatos disponibles."

    from pypdf import PdfReader

    reader = PdfReader(str(path))
    parts = []
    for page in reader.pages[:5]:
        parts.append(page.extract_text() or "")
        if sum(len(part) for part in parts) >= max_chars:
            break
    return _trim(" ".join(parts), max_chars)


def _xml_text_from_zip(path: Path, members: list[str], max_chars: int) -> str:
    parts = []
    with zipfile.ZipFile(path) as archive:
        for member in members:
            if member not in archive.namelist():
                continue
            data = archive.read(member)
            root = ElementTree.fromstring(data)
            parts.extend(text for text in root.itertext() if text.strip())
            if sum(len(part) for part in parts) >= max_chars:
                break
    return _trim(" ".join(parts), max_chars)


def _read_docx(path: Path, max_chars: int) -> str:
    try:
        return _xml_text_from_zip(path, ["word/document.xml"], max_chars)
    except Exception:
        if not importlib.util.find_spec("docx"):
            raise

        from docx import Document

        document = Document(str(path))
        text = " ".join(paragraph.text for paragraph in document.paragraphs)
        return _trim(text, max_chars)


def _read_xlsx(path: Path, max_chars: int) -> str:
    members = ["xl/sharedStrings.xml"]
    with zipfile.ZipFile(path) as archive:
        members.extend(name for name in archive.namelist() if name.startswith("xl/worksheets/sheet") and name.endswith(".xml"))
    return _xml_text_from_zip(path, members, max_chars)


def _read_pptx(path: Path, max_chars: int) -> str:
    with zipfile.ZipFile(path) as archive:
        members = sorted(name for name in archive.namelist() if name.startswith("ppt/slides/slide") and name.endswith(".xml"))
    return _xml_text_from_zip(path, members, max_chars)


def _size_label(size_bytes: int | None) -> str | None:
    if size_bytes is None:
        return None
    value = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size_bytes} B"


def _timestamp_label(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return _dt.datetime.fromtimestamp(timestamp).isoformat(timespec="seconds")


def _clean_metadata_value(value) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def _compact_dict(data: dict) -> dict:
    return {key: value for key, value in data.items() if value not in (None, "", [], {})}


def _type_group_for_extension(suffix: str) -> str:
    if suffix in DOCUMENT_EXTENSIONS or suffix in TEXT_EXTENSIONS:
        return "document"
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    if suffix in AUDIO_EXTENSIONS:
        return "audio"
    if suffix in ARCHIVE_EXTENSIONS:
        return "archive"
    if suffix in {".exe", ".msi", ".bat", ".cmd", ".ps1"}:
        return "software"
    return "other"


def _pdf_metadata(path: Path) -> dict:
    if not importlib.util.find_spec("pypdf"):
        return {}

    from pypdf import PdfReader

    reader = PdfReader(str(path))
    raw = reader.metadata or {}
    fields = {
        "title": raw.get("/Title"),
        "author": raw.get("/Author"),
        "subject": raw.get("/Subject"),
        "keywords": raw.get("/Keywords"),
        "creator": raw.get("/Creator"),
        "producer": raw.get("/Producer"),
        "pages": len(reader.pages),
    }
    return _compact_dict({key: _clean_metadata_value(value) for key, value in fields.items()})


def _office_metadata(path: Path) -> dict:
    metadata = {}
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if "docProps/core.xml" in names:
                root = ElementTree.fromstring(archive.read("docProps/core.xml"))
                for element in root.iter():
                    tag = element.tag.rsplit("}", 1)[-1].casefold()
                    if tag in {"title", "subject", "creator", "keywords", "description", "category"}:
                        value = _clean_metadata_value(element.text)
                        if value:
                            metadata[tag] = value
            if "docProps/app.xml" in names:
                root = ElementTree.fromstring(archive.read("docProps/app.xml"))
                for element in root.iter():
                    tag = element.tag.rsplit("}", 1)[-1].casefold()
                    if tag in {"application", "pages", "slides", "worksheets", "company"}:
                        value = _clean_metadata_value(element.text)
                        if value:
                            metadata[tag] = value
    except Exception as exc:
        logger.debug("No se pudieron leer metadatos Office de %s: %s", path.name, exc)
    return metadata


def _image_metadata(path: Path) -> dict:
    if not importlib.util.find_spec("PIL"):
        return {}

    try:
        from PIL import ExifTags, Image

        with Image.open(path) as image:
            metadata = {
                "format": image.format,
                "width": image.width,
                "height": image.height,
                "mode": image.mode,
            }
            exif = image.getexif()
            if exif:
                decoded = {}
                for key, value in exif.items():
                    name = ExifTags.TAGS.get(key, str(key))
                    if name in {"DateTime", "DateTimeOriginal", "Make", "Model", "Software"}:
                        cleaned = _clean_metadata_value(value)
                        if cleaned:
                            decoded[name] = cleaned
                if decoded:
                    metadata["exif"] = decoded
            return _compact_dict(metadata)
    except Exception as exc:
        logger.debug("No se pudieron leer metadatos de imagen de %s: %s", path.name, exc)
        return {}


def _archive_metadata(path: Path) -> dict:
    if path.suffix.casefold() != ".zip":
        return {}
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            return _compact_dict({
                "entries": len(names),
                "sample_names": names[:20],
            })
    except Exception as exc:
        logger.debug("No se pudieron leer metadatos ZIP de %s: %s", path.name, exc)
        return {}


def collect_file_metadata(
    file_path: str,
    max_chars: int = DEFAULT_MAX_CHARS,
    include_content: bool = True,
) -> dict:
    """
    Build compact, JSON-serializable context for local and LLM classification.
    """
    path = Path(file_path)
    suffix = path.suffix.casefold()
    info = {
        "filename": path.name,
        "extension": suffix,
        "type_group": _type_group_for_extension(suffix),
        "mime_type": mimetypes.guess_type(path.name)[0],
    }

    try:
        stat = path.stat()
        info.update({
            "size_bytes": stat.st_size,
            "size_label": _size_label(stat.st_size),
            "modified_time": _timestamp_label(stat.st_mtime),
        })
    except OSError as exc:
        info["stat_error"] = str(exc)
        return _compact_dict(info)

    try:
        if suffix == ".pdf":
            info["document_metadata"] = _pdf_metadata(path)
        elif suffix in {".docx", ".xlsx", ".pptx"}:
            info["document_metadata"] = _office_metadata(path)
        elif suffix in IMAGE_EXTENSIONS:
            info["media_metadata"] = _image_metadata(path)
        elif suffix in ARCHIVE_EXTENSIONS:
            info["archive_metadata"] = _archive_metadata(path)
    except Exception as exc:
        info["metadata_error"] = str(exc)

    if include_content and (suffix in DOCUMENT_EXTENSIONS or suffix in TEXT_EXTENSIONS):
        info["content_preview"] = extract_document_content(str(path), max_chars)

    return _compact_dict(info)


def extract_document_content(file_path: str, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    path = Path(file_path)
    if not path.exists():
        return "Error: Archivo no encontrado en disco."
    if not path.is_file():
        return "Error: La ruta no corresponde a un archivo."

    suffix = path.suffix.lower()
    try:
        if suffix in TEXT_EXTENSIONS:
            content = _read_text(path, max_chars)
        elif suffix == ".pdf":
            content = _read_pdf(path, max_chars)
        elif suffix == ".docx":
            content = _read_docx(path, max_chars)
        elif suffix == ".xlsx":
            content = _read_xlsx(path, max_chars)
        elif suffix == ".pptx":
            content = _read_pptx(path, max_chars)
        else:
            return (
                f"Aviso: El archivo {path.name} tiene formato {suffix or 'sin extension'} "
                "y no es legible por el parser actual. Clasifica por nombre y extension."
            )

        if not content:
            return f"Aviso: No se pudo extraer texto util de {path.name}. Clasifica por nombre y extension."

        logger.info("Accion (analyze_document_content): contenido extraido de %s", path.name)
        return f"El contenido inicial del documento es:\n\n{_trim(content, max_chars)}"
    except Exception as e:
        logger.error("Error analizando documento %s: %s", path, e)
        return f"Error tecnico al leer documento: {str(e)}"


@tool
def analyze_document_content(file_path: str) -> str:
    """
    Extrae texto preliminar de txt/csv/md/json/log/yaml/xml, PDF si pypdf esta instalado,
    y documentos Office modernos (.docx/.xlsx/.pptx) con extraccion parcial segura.
    """
    return extract_document_content(file_path, DEFAULT_MAX_CHARS)


def get_parser_tools():
    """Retorna la lista de herramientas de analisis multimodal para LangChain."""
    return [analyze_document_content]
