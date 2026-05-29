import logging
import os

try:
    from langchain_groq import ChatGroq
except ImportError:
    ChatGroq = None

try:
    from langchain_google_genai import ChatGoogleGenerativeAI
except ImportError:
    ChatGoogleGenerativeAI = None

logger = logging.getLogger(__name__)


def get_llm(config: dict):
    """Inicializa el LLM: Groq (primario) → Gemini (fallback) → None."""
    llm_config = config.get("llm", {})
    temperature = llm_config.get("temperature", 0.2)

    # Prioridad 1: Groq (30 req/min, 14.400 req/dia gratis)
    groq_key = os.environ.get("GROQ_API_KEY")
    if groq_key and ChatGroq:
        groq_model = llm_config.get("groq_model", "llama-3.3-70b-versatile")
        try:
            llm = ChatGroq(model=groq_model, temperature=temperature, api_key=groq_key)
            logger.info("Motor LLM inicializado: Groq (%s)", groq_model)
            return llm
        except Exception as e:
            logger.warning("Error al inicializar Groq, intentando Gemini: %s", e)

    # Prioridad 2: Gemini (fallback opcional — si la key esta configurada)
    google_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if google_key and ChatGoogleGenerativeAI:
        if not os.environ.get("GOOGLE_API_KEY"):
            os.environ["GOOGLE_API_KEY"] = google_key
        gemini_model = llm_config.get("gemini_model", "gemini-2.5-flash")
        try:
            llm = ChatGoogleGenerativeAI(
                model=gemini_model,
                temperature=temperature,
                max_output_tokens=8192,
            )
            logger.info("Motor LLM inicializado: Gemini (%s)", gemini_model)
            return llm
        except Exception as e:
            logger.error("Error al inicializar Gemini: %s", e)
            return None

    logger.warning("Falta GROQ_API_KEY y GOOGLE_API_KEY/GEMINI_API_KEY. Motor LLM deshabilitado.")
    return None


def get_llm_providers(config: dict) -> dict:
    """Retorna {'groq': llm_or_None, 'gemini': llm_or_None} para uso dual en runtime."""
    llm_config = config.get("llm", {})
    temperature = llm_config.get("temperature", 0.2)
    result = {"groq": None, "gemini": None}

    groq_key = os.environ.get("GROQ_API_KEY")
    if groq_key and ChatGroq:
        groq_model = llm_config.get("groq_model", "llama-3.3-70b-versatile")
        try:
            result["groq"] = ChatGroq(model=groq_model, temperature=temperature, api_key=groq_key)
            logger.info("Proveedor Groq inicializado (%s)", groq_model)
        except Exception as e:
            logger.warning("Error inicializando Groq: %s", e)

    google_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if google_key and ChatGoogleGenerativeAI:
        if not os.environ.get("GOOGLE_API_KEY"):
            os.environ["GOOGLE_API_KEY"] = google_key
        gemini_model = llm_config.get("gemini_model", "gemini-2.5-flash")
        try:
            result["gemini"] = ChatGoogleGenerativeAI(
                model=gemini_model, temperature=temperature, max_output_tokens=8192,
            )
            logger.info("Proveedor Gemini inicializado (%s)", gemini_model)
        except Exception as e:
            logger.warning("Error inicializando Gemini: %s", e)

    return result
