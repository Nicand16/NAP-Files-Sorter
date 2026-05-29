from dataclasses import dataclass
from pathlib import Path
import re
import unicodedata


@dataclass(frozen=True)
class ClassificationDecision:
    action: str
    category: str | None
    reason: str
    confidence: float


# ── Default taxonomy (fallback when no config is loaded) ──────────────────────
DEFAULT_TAXONOMY = [
    {
        "category": "Universidad y Estudio/Actividades y Tareas",
        "keywords": [
            # tipos de entregables (es/en)
            "actividad", "activity", "taller", "workshop", "protocolo", "protocol",
            "ensayo", "essay", "tcc", "ejercicio", "exercise", "tarea", "homework", "assignment",
            "produccion textual", "parcial", "midterm", "quiz", "evaluacion", "evaluation",
            "laboratorio", "lab report", "entrega", "submission", "rubrica", "rubric",
            "informe", "report", "practica", "practicum", "solucionario", "answer key",
            "solutions manual", "sustentacion", "thesis defense", "dissertation defense",
            "monografia", "monograph", "research paper",
            "trabajo de grado", "thesis", "dissertation", "degree project",
            "trabajo final", "final project", "final paper",
            "proyecto integrador", "integration project",
            "proyecto de aula", "classroom project",
            "anteproyecto", "research proposal", "project proposal",
            "exposicion", "ejercicios resueltos", "solved exercises",
            "trabajo colaborativo", "group work", "team project",
            "propuesta de investigacion", "correccion", "correction",
            "retroalimentacion", "feedback", "actividad colaborativa", "collaborative activity",
            # materias y asignaturas (es/en)
            "algebra", "calculo", "calculus", "calculo diferencial", "differential calculus",
            "calculo integral", "integral calculus", "algebra lineal", "linear algebra",
            "ecuaciones diferenciales", "differential equations",
            "estadistica", "statistics", "probabilidad", "probability",
            "fisica", "physics", "quimica", "chemistry", "biologia", "biology",
            "historia", "history", "filosofia", "philosophy", "sociologia", "sociology",
            "matematicas", "mathematics", "trigonometria", "trigonometry",
            "geometria", "geometry", "programacion", "programming",
            "algoritmos", "algorithms", "estructuras de datos", "data structures",
            "base de datos", "database", "bd1", "bd2",
            "sistemas operativos", "operating systems",
            "redes de computadores", "computer networks", "networking",
            "ingenieria de software", "software engineering",
            "logica de programacion", "programming logic",
            "analisis de sistemas", "systems analysis",
            "desarrollo de software", "software development",
            "seguridad informatica", "cybersecurity", "information security",
            "inteligencia artificial", "artificial intelligence", "machine learning",
            "economia", "economics", "contabilidad", "accounting",
            "administracion", "business administration", "management",
            "derecho", "law", "psicologia", "psychology", "literatura", "literature",
            # instituciones
            "unad", "unal", "uninorte", "icfes", "saber pro", "saber 11", "preicfes",
        ],
    },
    {
        "category": "Universidad y Estudio/Material de Estudio y Modulos",
        "keywords": [
            "modulo", "module", "libro", "book", "textbook", "guia", "guide", "manual",
            "normas apa", "apa format", "normas icontec", "glosario", "glossary",
            "latex", "syllabus", "clase", "class", "lecture", "lectura", "reading",
            "diapositivas", "diapositiva", "slides", "slide deck",
            "apuntes", "notes", "class notes", "lecture notes",
            "presentacion", "presentation", "tutorial",
            "capitulo", "chapter", "unidad", "unit", "semana", "week", "leccion", "lesson",
            "formulario", "formula sheet", "material de apoyo", "study material",
            "support material", "recurso educativo", "educational resource", "learning resource",
            "notas de clase", "resumen de clase", "formulas",
            "referencias bibliograficas", "references", "bibliography",
            "ebook", "libro electronico", "coursera", "udemy", "platzi", "edx",
            "linkedin learning", "google learning",
            "fundamentos", "fundamentals", "foundations",
            "introduccion a", "introduction to",
        ],
    },
    {
        "category": "Universidad y Estudio/Tramites Academicos",
        "keywords": [
            "reglamento", "regulations", "acuerdo", "matricula", "enrollment", "registration",
            "diploma", "certificado academico", "academic certificate", "academic transcript",
            "transcript", "constancia de estudio", "enrollment certificate",
            "notas", "grades", "calificaciones", "homologacion", "credit transfer",
            "course validation", "inscripcion", "paz y salvo", "pensum",
            "study plan", "horario academico", "academic schedule", "class schedule",
            "plan de estudios", "grado", "degree", "graduation", "graduacion",
            "acta de grado", "degree certificate", "graduation record",
            "promedio academico", "gpa", "grade point average",
            "creditos academicos", "credit hours", "academic credits",
            "admision", "admission", "certificado de estudios", "certificate of enrollment",
            "beca", "scholarship", "grant", "semestre", "semester",
            "certificado sena", "certificado google", "certificado microsoft",
            "certificado coursera", "certificado platzi", "certificado udemy",
            "certificado de participacion", "certificate of participation",
            "completion certificate", "certificacion", "certification", "sena",
        ],
    },
    {
        "category": "Trabajo y Empleo/CVs y Portafolios",
        "keywords": [
            "cv", "resume", "hoja de vida", "curriculum", "portfolio", "portafolio",
            "perfil profesional", "professional profile", "linkedin",
            "carta de presentacion profesional", "cover letter",
            "experiencia laboral", "work experience", "professional experience",
            "logros profesionales", "professional achievements", "accomplishments",
        ],
    },
    {
        "category": "Trabajo y Empleo/Procesos de Seleccion",
        "keywords": [
            "interview", "entrevista", "oferta", "offer", "technical support",
            "simetrik", "openprovider", "js held",
            "contract", "contrato", "prueba tecnica", "prueba_tecnica",
            "technical test", "coding test", "technical assessment",
            "ticket", "recruiter", "reclutador", "job offer", "contrato laboral",
            "assessment", "psicotecnico", "psychometric test", "aptitude test",
            "code challenge", "take home", "take home test", "onboarding",
            "proceso de seleccion", "selection process", "hiring process",
            "oferta de empleo", "carta de aceptacion", "acceptance letter", "offer letter",
            "acuerdo de confidencialidad", "confidentiality agreement",
            "nda", "non-disclosure agreement", "propuesta economica",
            "salary proposal", "compensation offer", "carta de oferta",
            "reclutamiento", "recruitment", "vacante", "vacancy", "job opening",
            "solicitud de empleo", "job application", "aplicacion de empleo",
            "referencia laboral", "professional reference", "work reference",
            "carta de recomendacion laboral", "recommendation letter", "reference letter",
            "liquidacion laboral", "severance", "final settlement",
            "despido", "termination", "dismissal",
            "renuncia", "resignation",
            "contrato de trabajo", "employment contract",
            "contrato de prestacion de servicios", "service agreement", "freelance contract",
            "prestacion de servicios",
        ],
    },
    {
        "category": "Documentos Personales/Identificacion e Impuestos",
        "keywords": [
            "cedula", "national id", "id card", "rut", "identificacion", "identification",
            "passport", "pasaporte", "visa", "dni",
            "impuesto", "tax", "declaracion de renta", "income tax return", "tax return",
            "nit", "registro civil", "civil registry", "birth certificate",
            "libreta militar", "military booklet", "soat",
            "licencia de conduccion", "driver license", "driving license",
            "tarjeta de identidad", "identity card",
            "tarjeta profesional", "professional license",
            "documento de identidad", "identity document",
            "tributaria", "formulario tributario", "tax form", "dian",
            "retenciones", "withholding tax", "tax withholding",
            "certificado de retenciones", "impuesto de renta", "income tax",
            "ica", "declaracion tributaria", "tax declaration",
            "permiso de trabajo", "work permit",
            "permiso de residencia", "residence permit",
        ],
    },
    {
        "category": "Documentos Personales/Finanzas y Salud",
        "keywords": [
            # financiero general (es/en)
            "certificado", "certificate", "factura", "invoice", "comprobante",
            "proof of payment", "pago", "payment", "afiliacion", "membership",
            "eps", "receipt", "recibo de pago", "payment receipt",
            "banco", "bank", "extracto", "bank statement", "account statement",
            "cuenta", "account", "nomina", "payroll", "pay stub", "payslip",
            "desprendible de nomina", "pension", "cesantias", "severance fund",
            "prima", "bonus", "subsidio", "subsidy", "benefit",
            "seguro", "insurance", "poliza", "insurance policy", "policy",
            "estado de cuenta", "inversion", "investment",
            "portafolio de inversiones", "investment portfolio",
            "dividendo", "dividend", "transferencia bancaria", "bank transfer", "wire transfer",
            "certificado de ingresos", "income certificate", "salary certificate",
            "constancia de pago", "payment confirmation",
            # bancos y entidades colombianas
            "bancolombia", "davivienda", "nequi", "daviplata", "bbva",
            "banco de occidente", "colpatria", "banco popular", "banco bogota",
            "nubank", "rappipay",
            # pensiones y seguros
            "colpensiones", "porvenir", "proteccion", "skandia", "colfondos",
            "pension obligatoria", "sura", "bolivar seguros", "liberty seguros",
            # salud (es/en)
            "medico", "doctor", "medical", "salud", "health",
            "historia clinica", "medical record", "medical history",
            "laboratorio clinico", "clinical lab", "medical lab",
            "formula medica", "receta medica", "prescription",
            "cita medica", "medical appointment", "doctor appointment",
            "resultado de examen", "test result", "lab result", "exam result",
            "examen medico", "medical exam", "medical test",
            "radiografia", "x-ray", "xray", "ecografia", "ultrasound",
            "odontologia", "dentistry", "dental",
            "medicina prepagada", "health insurance", "prepaid health",
            "arl", "incapacidad medica", "medical leave", "sick leave",
            "foscal", "clinica", "clinic", "hospital",
            "diagnostico medico", "diagnosis", "medical diagnosis",
            "sangre", "blood", "hemograma", "blood count", "cbc",
            "glucosa", "glucose", "colesterol", "cholesterol",
            "trigliceridos", "triglycerides", "hemoglobina", "hemoglobin",
            "analisis clinico", "clinical analysis", "lab test",
            "analisis de sangre", "blood test",
            "reporte medico", "medical report",
            "consulta medica", "medical consultation",
            "reporte clinico", "clinical report",
            "informe medico", "examen de laboratorio", "laboratory test",
        ],
    },
    {
        "category": "Juegos y Emulacion/Torrents",
        "extensions": [".torrent"],
    },
    {
        "category": "Juegos y Emulacion/ROMs e ISOs",
        "extensions": [
            ".gb", ".gbc", ".gba", ".nds", ".3ds", ".cia", ".nro",
            ".nes", ".sfc", ".smc", ".n64", ".z64", ".gcm", ".gcz", ".wbfs",
            ".nsp", ".xci", ".cso", ".ciso", ".iso", ".rvz", ".srm",
            ".vpk", ".xiso", ".ngp", ".ws", ".wsc", ".vb",
        ],
    },
    {
        "category": "Juegos y Emulacion/Emuladores y Mods",
        "keywords": [
            # emuladores (nombres propios — iguales en ambos idiomas)
            "yuzu", "cemu", "sudachi", "ryujinx", "retrobat", "dolphin", "snes9x",
            "rpcs3", "ppsspp", "pcsx2", "mame", "retroarch", "desmume", "melonds",
            "xenia", "project64", "epsxe", "zsnes", "mgba", "citra", "lime3ds",
            # franquicias y titulos (nombres propios)
            "mario", "zelda", "pokemon", "minecraft", "grand theft auto", "gta",
            "fortnite", "valorant", "league of legends", "cyberpunk", "elden ring",
            "dark souls", "red dead", "call of duty", "super mario", "metroid",
            "kirby", "donkey kong", "smash bros", "fire emblem", "animal crossing",
            # plataformas (nombres propios)
            "steam", "epic games", "battle.net", "origin", "ubisoft connect", "gog",
            "itch.io",
            # terminos de gaming (es/en)
            "save game", "game save", "guardado", "savegame",
            "rom hack", "trainer", "dlc", "contenido descargable", "downloadable content",
            "repack", "game crack", "game patch", "mod de juego",
        ],
    },
    {
        "category": "Multimedia/Audio y Musica",
        "extensions": [
            ".mp3", ".wav", ".flac", ".ogg", ".aac", ".m4a", ".wma",
            ".opus", ".aiff", ".alac", ".mka", ".ape", ".wv",
        ],
        "keywords": [
            "musica", "music", "cancion", "song", "album", "playlist",
            "discografia", "discography", "pista", "track", "remix",
            "instrumental", "acustico", "acoustic", "spotify", "soundcloud",
            "mixtape", "single", "ep",
        ],
    },
    {
        "category": "Multimedia/Videos y Grabaciones",
        "extensions": [
            ".mp4", ".mkv", ".mov", ".avi", ".webm", ".wmv", ".flv",
            ".ts", ".m4v", ".3gp", ".vob", ".mpg", ".mpeg", ".rmvb", ".rm", ".asf",
        ],
        "keywords": [
            "video", "pelicula", "movie", "film", "serie", "series", "tv show", "show",
            "documental", "documentary", "grabacion", "recording", "clip",
            "youtube", "twitch", "streaming", "episodio", "episode",
            "temporada", "season", "capitulo",
        ],
    },
    {
        "category": "Multimedia/Imagenes y Capturas",
        "extensions": [
            ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp",
            ".tiff", ".tif", ".heic", ".heif", ".raw", ".cr2", ".nef",
            ".arw", ".dng", ".ico", ".svg", ".psd", ".xcf", ".ai",
        ],
        "keywords": [
            "foto", "photo", "fotografia", "photography", "imagen", "image",
            "captura", "capture", "screenshot", "wallpaper", "fondo de pantalla",
            "background", "retrato", "portrait", "paisaje", "landscape",
            "selfie", "picture", "gallery", "galeria",
        ],
    },
    {
        "category": "Software y Herramientas/Instaladores",
        "extensions": [".exe", ".msi", ".apk", ".pkg", ".deb", ".dmg", ".rpm", ".appimage"],
        "keywords": [
            "installer", "instalador", "setup", "portable", "driver", "controlador",
            "firmware", "actualizacion", "update", "parche", "patch",
            "plugin", "extension", "addon", "add-on",
        ],
    },
    {
        "category": "Software y Herramientas/Comprimidos y Portables",
        "extensions": [
            ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz",
            ".cab", ".ace", ".zst", ".lzh",
        ],
    },
]

DOCUMENT_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods"}
_WORD_RE = re.compile(r"[a-z0-9]+")

# ── Compiled pattern rules ─────────────────────────────────────────────────────
# Each entry: (extension_set_or_None, pattern, category, confidence, reason)
# extension_set=None means the pattern applies regardless of extension.
_VIDEO_EXTS = frozenset({"mkv", "mp4", "avi", "mov", "wmv", "ts", "m4v", "webm", "flv", "vob", "mpg", "mpeg", "rmvb", "rm", "asf", "3gp"})
_IMAGE_EXTS = frozenset({"png", "jpg", "jpeg", "webp", "bmp", "tiff", "tif", "heic", "heif", "raw", "cr2", "nef", "arw", "dng"})
_INSTALLER_EXTS = frozenset({"exe", "msi", "apk", "pkg", "deb", "dmg", "rpm", "appimage"})
_ARCHIVE_EXTS = frozenset({"zip", "rar", "7z", "tar", "gz", "bz2", "xz", "cab", "ace", "zst"})

_PATTERN_RULES: list[tuple[frozenset | None, re.Pattern, str, float, str]] = [
    # ── Video: quality/source markers ──────────────────────────────────────
    (
        _VIDEO_EXTS,
        re.compile(
            r"\b(1080[pi]|720p|480p|2160p|4k|uhd|blu.?ray|bdrip|webrip|web.?dl|hdtv"
            r"|x264|x265|hevc|hdrip|dvdrip|xvid|h\.?264|h\.?265|avc|hq\b|sd\b|fullhd)\b",
            re.IGNORECASE,
        ),
        "Multimedia/Videos y Grabaciones",
        0.97,
        "Marcador de calidad/fuente de video en nombre de archivo.",
    ),
    # ── Video: series episode pattern ──────────────────────────────────────
    (
        _VIDEO_EXTS,
        re.compile(r"\bS\d{1,2}E\d{1,2}\b|\bSeason\s*\d+\b|\b\d{1,2}x\d{2}\b", re.IGNORECASE),
        "Multimedia/Videos y Grabaciones",
        0.97,
        "Patron de serie/temporada/episodio en nombre de archivo.",
    ),
    # ── Images: screenshot naming patterns ─────────────────────────────────
    (
        _IMAGE_EXTS,
        re.compile(
            r"^(screenshot|screen[_\s]?shot|screen[_\s]?grab|captura[_\s]de[_\s]pantalla"
            r"|captura[_\s]pantalla|snap[_\s]?shot)\b"
            r"|^Screenshot_\d{8}"      # Android: Screenshot_20240512_...
            r"|^Captura de pantalla \d{4}-\d{2}-\d{2}"  # Windows Snip
            r"|^IMG_\d{8}_",           # common phone photo naming
            re.IGNORECASE,
        ),
        "Multimedia/Imagenes y Capturas",
        0.96,
        "Patron de captura de pantalla o foto de dispositivo movil.",
    ),
    # ── ROMs: region/revision tags ──────────────────────────────────────────
    (
        None,  # any extension — region tags uniquely identify ROM files
        re.compile(
            r"\((USA|Europe|Japan|Multi\d*|World|EN|PAL|NTSC|Region Free|En,Es|Es,En)\)"
            r"|\[!\]|\(Rev\s*[A-Z\d]\)|\(V\d[\d.]*\)",
            re.IGNORECASE,
        ),
        "Juegos y Emulacion/ROMs e ISOs",
        0.97,
        "Tag de region/revision tipico de archivos ROM.",
    ),
    # ── Software: installer keyword in installer extension ──────────────────
    (
        _INSTALLER_EXTS,
        re.compile(r"\b(setup|installer|install|inno[_\s]setup|nsis)\b", re.IGNORECASE),
        "Software y Herramientas/Instaladores",
        0.97,
        "Patron de instalador en nombre de archivo.",
    ),
    # ── Financial documents: invoice/receipt with number ───────────────────
    (
        None,
        re.compile(
            r"\b(invoice|factura|receipt|recibo|comprobante|extracto|voucher|estado[_\s]de[_\s]cuenta)"
            r"\s*[-_#]?\s*\d{2,}",
            re.IGNORECASE,
        ),
        "Documentos Personales/Finanzas y Salud",
        0.93,
        "Patron de documento financiero con numero de referencia.",
    ),
    # ── Archives containing software-typical keywords ──────────────────────
    (
        _ARCHIVE_EXTS,
        re.compile(r"\b(portable|setup|installer|software|programa|tool|herramienta)\b", re.IGNORECASE),
        "Software y Herramientas/Comprimidos y Portables",
        0.90,
        "Archivo comprimido con nombre tipico de software portable.",
    ),
]


# ── Core text utilities ────────────────────────────────────────────────────────

def normalize_text(value: object) -> str:
    text = "" if value is None else str(value)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[_\-.]+", " ", text.casefold())
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokens(text: str) -> set[str]:
    return set(_WORD_RE.findall(text))


def is_document_extension(extension: str | None) -> bool:
    return (extension or "").casefold() in DOCUMENT_EXTENSIONS


def get_taxonomy(config: dict | None = None) -> list[dict]:
    return (config or {}).get("taxonomy", {}).get("categories") or DEFAULT_TAXONOMY


def taxonomy_categories(config: dict | None = None) -> list[str]:
    return [rule["category"] for rule in get_taxonomy(config) if rule.get("category")]


def build_taxonomy_prompt(config: dict | None = None) -> str:
    lines = []
    for index, rule in enumerate(get_taxonomy(config), start=1):
        details = []
        if rule.get("keywords"):
            details.append("Nombre/contenido contiene: " + ", ".join(rule["keywords"][:10]))
        if rule.get("extensions"):
            details.append("Extension es: " + ", ".join(rule["extensions"][:8]))
        lines.append(f"{index}. {rule['category']}: {'; '.join(details)}.")
    lines.append(f"{len(lines) + 1}. Varios: Solo para archivos sin evidencia razonable en nombre, tipo, metadatos ni contenido.")
    return "\n".join(lines)


def _flatten_context(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, dict):
        parts = []
        for item in value.values():
            parts.extend(_flatten_context(item))
        return parts
    if isinstance(value, (list, tuple, set)):
        parts = []
        for item in value:
            parts.extend(_flatten_context(item))
        return parts
    return [str(value)]


def metadata_to_search_text(metadata: dict | None) -> str:
    return " ".join(_flatten_context(metadata or {}))


def _keyword_score(keyword: str, normalized_text: str, token_set: set[str]) -> int:
    normalized_keyword = normalize_text(keyword)
    if not normalized_keyword:
        return 0
    keyword_tokens = _tokens(normalized_keyword)
    if not keyword_tokens:
        return 0

    padded_text = f" {normalized_text} "
    if len(keyword_tokens) > 1:
        if f" {normalized_keyword} " in padded_text:
            return 10 + (2 * len(keyword_tokens))
        return 0

    token = next(iter(keyword_tokens))
    if token in token_set:
        return 8 if len(token) > 3 else 6
    return 0


def _confidence_from_score(score: int, extension_match: bool = False) -> float:
    if extension_match:
        return 0.99
    if score >= 20:
        return 0.97
    if score >= 14:
        return 0.93
    if score >= 10:
        return 0.89
    if score >= 8:
        return 0.84
    if score >= 6:
        return 0.78
    return 0.0


def rank_file_categories(
    filename: str,
    extension: str | None = None,
    metadata: dict | None = None,
    config: dict | None = None,
    limit: int | None = None,
) -> list[dict]:
    suffix = (extension or Path(filename).suffix).casefold()
    search_text = " ".join([filename, metadata_to_search_text(metadata)])
    normalized = normalize_text(search_text)
    token_set = _tokens(normalized)
    ranked = []

    for order, rule in enumerate(get_taxonomy(config)):
        category = rule["category"]
        extensions = {item.casefold() for item in rule.get("extensions", [])}
        extension_match = bool(suffix and suffix in extensions)
        score = 100 if extension_match else 0
        matches = []

        for keyword in rule.get("keywords", []):
            keyword_points = _keyword_score(keyword, normalized, token_set)
            if keyword_points:
                score += keyword_points
                matches.append(keyword)

        if score <= 0:
            continue

        reason_parts = []
        if extension_match:
            reason_parts.append(f"extension {suffix}")
        if matches:
            reason_parts.append("keywords: " + ", ".join(matches[:5]))

        ranked.append({
            "category": category,
            "score": score,
            "confidence": _confidence_from_score(score, extension_match),
            "reason": "; ".join(reason_parts),
            "order": order,
        })

    ranked.sort(key=lambda item: (item["score"], item["confidence"], -item["order"]), reverse=True)
    if limit is not None:
        return ranked[:limit]
    return ranked


def _decision_from_ranked(ranked: list[dict], min_confidence: float, context_label: str) -> ClassificationDecision | None:
    if not ranked:
        return None
    best = ranked[0]
    if best["confidence"] < min_confidence:
        return None
    return ClassificationDecision(
        action="move",
        category=best["category"],
        reason=f"{context_label}: {best['reason']}.",
        confidence=best["confidence"],
    )


# ── Pattern-based classification ──────────────────────────────────────────────

def classify_by_patterns(filename: str, extension: str | None = None) -> ClassificationDecision | None:
    """
    High-confidence classification using regex patterns on the filename.
    Checked before the keyword engine — returns immediately on first match.
    """
    ext = (extension or Path(filename).suffix).casefold().lstrip(".")
    for ext_set, pattern, category, confidence, reason in _PATTERN_RULES:
        if ext_set is not None and ext not in ext_set:
            continue
        if pattern.search(filename):
            return ClassificationDecision(action="move", category=category, reason=reason, confidence=confidence)
    return None


# ── Document metadata priority check ──────────────────────────────────────────

def _classify_from_document_metadata(metadata: dict | None, config: dict | None) -> ClassificationDecision | None:
    """
    Run keyword classification on the most reliable document metadata fields
    (title, subject, keywords, category from PDF/Office) before the full
    metadata search. These fields are highly specific and rarely noisy.
    """
    if not metadata:
        return None
    doc_meta = metadata.get("document_metadata", {})
    if not doc_meta:
        return None

    priority_fields = []
    for field in ("title", "subject", "keywords", "category", "description"):
        value = doc_meta.get(field)
        if value and str(value).strip():
            priority_fields.append(str(value))

    if not priority_fields:
        return None

    priority_text = " ".join(priority_fields)
    ranked = rank_file_categories(priority_text, extension=None, metadata=None, config=config)
    if not ranked:
        return None
    best = ranked[0]
    if best["confidence"] >= 0.84:
        return ClassificationDecision(
            action="move",
            category=best["category"],
            reason=f"Metadatos del documento (titulo/subject): {best['reason']}.",
            confidence=best["confidence"],
        )
    return None


# ── Archive content voting ─────────────────────────────────────────────────────

def _vote_from_archive_names(archive_names: list[str], config: dict | None) -> ClassificationDecision | None:
    """
    Vote on top-level category by classifying the filenames inside a ZIP archive.
    Mirrors the folder-classification voting logic.
    """
    votes: dict[str, int] = {}
    for name in archive_names:
        ext = Path(name).suffix
        ranked = rank_file_categories(name, ext, None, config, limit=1)
        if ranked and ranked[0]["confidence"] >= 0.84:
            top_cat = ranked[0]["category"].split("/")[0]
            votes[top_cat] = votes.get(top_cat, 0) + 1

    if not votes:
        return None

    total = len(archive_names)
    best_cat, best_count = max(votes.items(), key=lambda x: x[1])
    if best_count >= 2 and best_count / total >= 0.5:
        confidence = min(0.88 + (best_count / total) * 0.08, 0.95)
        return ClassificationDecision(
            action="move",
            category=best_cat,
            reason=f"Votacion por contenido del archivo comprimido: {best_count}/{total} entradas apuntan a {best_cat}.",
            confidence=confidence,
        )
    # Strong unanimous signal even with few entries
    if len(votes) == 1 and best_count >= 3:
        return ClassificationDecision(
            action="move",
            category=best_cat,
            reason=f"Contenido del archivo comprimido unanime: {best_count} entradas en {best_cat}.",
            confidence=0.90,
        )
    return None


# ── Public classification API ──────────────────────────────────────────────────

def classify_file(
    filename: str,
    extension: str | None = None,
    config: dict | None = None,
) -> ClassificationDecision | None:
    """
    Phase-1 classification: patterns → extension/keyword rules.
    No file I/O — uses only the filename and extension.
    """
    suffix = (extension or Path(filename).suffix).casefold()

    if filename.casefold() in {"desktop.ini", ".keep"}:
        return ClassificationDecision(
            action="ignore",
            category=None,
            reason="Archivo de sistema o marcador ignorado.",
            confidence=1.0,
        )

    # Patterns first — very high confidence, no false positives in practice
    pattern_decision = classify_by_patterns(filename, suffix)
    if pattern_decision:
        return pattern_decision

    ranked = rank_file_categories(filename, suffix, None, config)
    decision = _decision_from_ranked(ranked, min_confidence=0.78, context_label="Regla por nombre/extension")
    if decision:
        return decision

    if suffix in DOCUMENT_EXTENSIONS and (config or {}).get("rules", {}).get("fallback_documents_to_varios", False):
        return ClassificationDecision(
            action="move",
            category="Varios",
            reason=f"Documento generico sin regla especifica ({suffix}).",
            confidence=0.7,
        )

    return None


def classify_file_context(
    filename: str,
    extension: str | None = None,
    metadata: dict | None = None,
    config: dict | None = None,
    min_confidence: float = 0.84,
) -> ClassificationDecision | None:
    """
    Phase-2 classification using extracted metadata.
    Checks document metadata fields and archive contents before the full search.
    """
    suffix = (extension or Path(filename).suffix).casefold()

    # Priority: document metadata fields (title, subject, keywords from PDF/Office)
    meta_decision = _classify_from_document_metadata(metadata, config)
    if meta_decision and meta_decision.confidence >= min_confidence:
        return meta_decision

    # Archive content voting (ZIP sample_names)
    if suffix in {".zip", ".rar", ".7z", ".tar", ".gz", ".cab"}:
        archive_meta = (metadata or {}).get("archive_metadata", {})
        sample_names = archive_meta.get("sample_names", [])
        if sample_names:
            archive_decision = _vote_from_archive_names(sample_names, config)
            if archive_decision and archive_decision.confidence >= min_confidence:
                return archive_decision

    ranked = rank_file_categories(filename, suffix, metadata, config)
    return _decision_from_ranked(ranked, min_confidence=min_confidence, context_label="Regla por metadatos/contenido")


# ── Folder classification ──────────────────────────────────────────────────────

def sample_folder_files(folder_path: Path, max_samples: int = 8) -> list[str]:
    """Returns up to max_samples filenames from folder root (non-recursive)."""
    try:
        names = []
        for item in folder_path.iterdir():
            if item.is_file() and not item.name.startswith("."):
                names.append(item.name)
                if len(names) >= max_samples:
                    break
        return names
    except (PermissionError, OSError):
        return []


def _vote_top_category(sampled_names: list[str], config: dict | None) -> str | None:
    """Vote on top-level category from sampled filenames. Returns category if majority found."""
    votes: dict[str, int] = {}
    for name in sampled_names:
        ext = Path(name).suffix
        ranked = rank_file_categories(name, ext, None, config, limit=1)
        if ranked and ranked[0]["confidence"] >= 0.78:
            top_cat = ranked[0]["category"].split("/")[0]
            votes[top_cat] = votes.get(top_cat, 0) + 1

    if not votes:
        return None

    best_cat, best_count = max(votes.items(), key=lambda x: x[1])
    total = len(sampled_names)
    if (best_count >= 2 and best_count / total >= 0.5) or (len(votes) == 1 and best_count >= 1):
        return best_cat
    return None


def classify_folder(
    folder_name: str,
    folder_path: str | Path | None = None,
    config: dict | None = None,
    max_samples: int = 8,
) -> ClassificationDecision | None:
    """
    Classify a folder using a 3-tier local strategy:
      1. Folder name alone (>= 0.84 confidence)
      2. Vote from sampled root files
      3. Combined name + sample names (>= 0.78 confidence)
    Returns None if the folder needs LLM classification.
    """
    # Tier 1: classify by folder name alone
    ranked = rank_file_categories(folder_name, extension=None, config=config)
    decision = _decision_from_ranked(ranked, min_confidence=0.84, context_label="Nombre de carpeta")
    if decision:
        return decision

    # Tier 2 & 3 require reading the folder
    sample_names: list[str] = []
    if folder_path:
        path = Path(folder_path)
        if path.is_dir():
            sample_names = sample_folder_files(path, max_samples)

    if sample_names:
        # Tier 2: vote on top-level category from sample file names
        voted_top = _vote_top_category(sample_names, config)
        if voted_top:
            return ClassificationDecision(
                action="move",
                category=voted_top,
                reason=f"Votacion por {len(sample_names)} archivo(s) de muestra en la carpeta.",
                confidence=0.82,
            )

        # Tier 3: combined context (folder name + sample file names together)
        combined_text = folder_name + " " + " ".join(sample_names)
        ranked_combined = rank_file_categories(combined_text, extension=None, config=config)
        combined_decision = _decision_from_ranked(
            ranked_combined,
            min_confidence=0.78,
            context_label="Carpeta + archivos de muestra",
        )
        if combined_decision:
            return combined_decision

    return None
