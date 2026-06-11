import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path

try:
    from langgraph.prebuilt import create_react_agent
except ImportError:
    create_react_agent = None

from core.llm_engine import get_llm_providers
from infra.metrics import (
    M_CACHE_HITS,
    M_CACHE_MISSES,
    M_CYCLE_DURATION,
    M_FILES_ERRORS,
    M_FILES_PROCESSED,
    M_LLM_CALL,
    M_LLM_CALLS_TOTAL,
    M_LLM_FAILURES_TOTAL,
    M_PHASE1_DURATION,
    M_PHASE2_DURATION,
    metrics,
)
from modules.crud_executor import consume_thread_moves, move_file_secure, move_folder_secure
from modules.rules_engine import (
    build_taxonomy_prompt,
    classify_file,
    classify_file_context,
    classify_folder,
    is_document_extension,
    normalize_text,
    rank_file_categories,
    sample_folder_files,
    taxonomy_categories,
)
from runtime.event_bus import FileEvent, FileState, bus

logger = logging.getLogger(__name__)

# Previews cortos para bulk; el modo individual usa un contexto mas amplio.
_DEFAULT_BULK_CONTENT_MAX_CHARS = 700
_DEFAULT_INDIVIDUAL_CONTENT_MAX_CHARS = 3000


def _is_api_key_error(exc_or_msg) -> bool:
    msg = str(exc_or_msg).lower()
    return any(kw in msg for kw in (
        "api_key_invalid", "api key expired", "renew the api key",
        "api key not valid", "invalid api key",
    ))


def _is_rate_limit_error(exc_or_msg) -> bool:
    msg = str(exc_or_msg).lower()
    return any(kw in msg for kw in (
        "quota", "rate limit", "resource_exhausted",
        "too many requests", "429", "ratequotaexceeded",
    ))


def _is_daily_limit_error(exc_or_msg) -> bool:
    msg = str(exc_or_msg).lower()
    return any(kw in msg for kw in ("per day", "rpd", "daily", "requests per day"))


class MoveFailureError(RuntimeError):
    def __init__(self, message: str, error_code: str | None = None):
        super().__init__(message)
        self.error_code = error_code


def _resolve_workspace(workspace_value: str | Path) -> Path:
    path = Path(workspace_value).expanduser()
    return path.resolve()


class NAPOrchestrator:
    """
    Orquestador principal del agente NAP Files-Sorter.

    Flujo de procesamiento en 3 fases:
      1. Reglas deterministicas (sin API) -- extension/keyword -> movimiento directo.
      2. Clasificacion por lote (1 API call para todos los archivos ambiguos del ciclo).
      3. Fallback ReAct por archivo -- solo si el lote falla.
    """

    def __init__(self, config: dict, db_manager, workspace_dir=None):
        self.config = config
        self.db = db_manager
        if workspace_dir is not None:
            self.workspace_root = Path(workspace_dir).expanduser().resolve()
            workspace_source = "main"
        else:
            workspace_rel_dir = config.get("monitoring", {}).get("workspace_dir", "./workspace")
            self.workspace_root = _resolve_workspace(workspace_rel_dir)
            workspace_source = "config"
        self.config.setdefault("monitoring", {})["workspace_dir"] = str(self.workspace_root)
        logger.info(
            "Workspace root: %s (source=%s, existe=%s)",
            self.workspace_root,
            workspace_source,
            self.workspace_root.exists(),
        )
        self.dry_run = config.get("monitoring", {}).get("dry_run", config.get("app", {}).get("dry_run", False))
        self.destination_aliases = config.get("monitoring", {}).get("destination_aliases", {})
        self.max_files_per_cycle = max(1, int(config.get("processing", {}).get("max_files_per_cycle", 25)))
        self.llm_batch_size = max(1, int(config.get("processing", {}).get("llm_batch_size", 15)))
        self.llm_timeout_seconds = int(config.get("processing", {}).get("llm_timeout_seconds", 60))
        processing_cfg = config.get("processing", {})
        parsing_cfg = config.get("parsing", {})
        rules_cfg = config.get("rules", {})
        self.llm_individual_threshold = max(1, int(processing_cfg.get("llm_individual_threshold", 6)))
        self.llm_bulk_content_max_chars = max(
            150,
            int(processing_cfg.get("llm_bulk_content_max_chars", _DEFAULT_BULK_CONTENT_MAX_CHARS)),
        )
        self.llm_individual_content_max_chars = max(
            self.llm_bulk_content_max_chars,
            int(processing_cfg.get(
                "llm_individual_content_max_chars",
                parsing_cfg.get("max_chars", _DEFAULT_INDIVIDUAL_CONTENT_MAX_CHARS),
            )),
        )
        self.local_context_min_confidence = float(processing_cfg.get("local_context_min_confidence", 0.84))
        self.varios_min_confidence = float(processing_cfg.get("varios_min_confidence", 0.82))
        self.ambiguous_document_fallback_category = rules_cfg.get(
            "ambiguous_document_fallback_category",
            "Varios/Documentos por Revisar",
        )
        self._taxonomy_categories = taxonomy_categories(config)
        self._active_paths: set[str] = set()
        self._active_paths_lock = threading.Lock()
        self._tray = None
        self._consecutive_api_failures = 0
        # Lazy LLM init: do NOT call get_llm() at construction time
        self._llm_obj = None
        self._groq_llm = None
        self._gemini_llm = None
        self._llm_initialized = False
        self._llm_init_lock = threading.Lock()
        self._llm_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="NAPLLMInvoke")
        self.agent = None  # set lazily when LLM first initializes
        # Dual circuit breakers — one per provider
        from runtime.circuit_breaker import CircuitBreaker
        proc = config.get("processing", {})
        cb_threshold = int(proc.get("circuit_breaker_threshold", 3))
        self._groq_base_recovery = float(proc.get("groq_circuit_recovery_seconds", 65.0))
        self._gemini_base_recovery = float(proc.get("gemini_circuit_recovery_seconds", 65.0))
        self._groq_circuit = CircuitBreaker("groq", cb_threshold, self._groq_base_recovery)
        self._gemini_circuit = CircuitBreaker("gemini", cb_threshold, self._gemini_base_recovery)
        self._groq_daily_recovery = float(proc.get("groq_daily_circuit_recovery_seconds", 3600.0))
        self._gemini_daily_recovery = float(proc.get("gemini_daily_circuit_recovery_seconds", 3600.0))
        self._circuit = self._groq_circuit  # backward compat alias
        self._circuit_error_type: str | None = None  # "rate_limit" | "auth_error" | None
        # Decision cache: avoids redundant LLM calls for files with same extension+stem pattern
        from classifiers.decision_cache import DecisionCache
        cache_size = int(config.get("processing", {}).get("decision_cache_size", 200))
        cache_ttl = float(config.get("processing", {}).get("decision_cache_ttl_seconds", 3600.0))
        self._cache = DecisionCache(max_size=cache_size, ttl_seconds=cache_ttl)
        self._warm_decision_cache(cache_size)

    def _warm_decision_cache(self, limit: int):
        if not hasattr(self.db, "get_recent_classification_decisions"):
            return
        loaded = 0
        for row in self.db.get_recent_classification_decisions(limit=limit):
            category = row.get("category")
            if not category or self._is_varios_category(category):
                continue
            self._cache.put(
                row.get("filename") or "",
                row.get("extension") or "",
                category,
                row.get("decision_source") or "db",
            )
            loaded += 1
        if loaded:
            logger.info("Decision cache precargado desde SQLite: %s decision(es).", loaded)

    def _init_llm_providers(self):
        """Inicializa ambos providers. Debe llamarse con _llm_init_lock adquirido."""
        providers = get_llm_providers(self.config)
        self._groq_llm = providers["groq"]
        self._gemini_llm = providers["gemini"]
        self._llm_obj = self._groq_llm or self._gemini_llm
        self._llm_initialized = True
        if self._llm_obj:
            self.agent = self._initialize_agent()
        else:
            self.agent = None
            logger.warning("Sin LLM disponible (faltan API keys).")

    @property
    def llm(self):
        if self._llm_initialized:
            return self._llm_obj
        with self._llm_init_lock:
            if not self._llm_initialized:
                self._init_llm_providers()
        return self._llm_obj

    def set_tray(self, tray):
        self._tray = tray

    def _emit(self, state: FileState, filepath: str, filename: str, **kwargs):
        bus.publish(FileEvent(state=state, filepath=filepath, filename=filename, **kwargs))

    def _notify_error(self, message: str, notify: bool = True):
        logger.error(message)
        if self._tray and hasattr(self._tray, "set_error"):
            self._tray.set_error(message, notify=notify)

    def _record_api_failure(self, message: str, provider: str = "groq"):
        import json as _json
        metrics.inc(M_LLM_FAILURES_TOTAL)
        self._consecutive_api_failures += 1
        circuit = self._groq_circuit if provider == "groq" else self._gemini_circuit
        etype = None
        if _is_rate_limit_error(message):
            etype = "rate_limit"
            if _is_daily_limit_error(message):
                circuit.recovery_seconds = self._groq_daily_recovery if provider == "groq" else self._gemini_daily_recovery
            else:
                circuit.recovery_seconds = self._groq_base_recovery if provider == "groq" else self._gemini_base_recovery
        elif _is_api_key_error(message):
            etype = "auth_error"
        self._circuit_error_type = etype
        circuit.record_failure(message)
        from runtime.circuit_breaker import CircuitState
        if circuit.state == CircuitState.OPEN:
            recovery = circuit.recovery_seconds
            payload = _json.dumps({"provider": provider, "type": etype or "unknown", "recovery_seconds": recovery, "msg": message[:300]})
            self.db.log_system_event("circuit_open", payload)
            has_gemini = bool(self._gemini_llm)
            is_daily = etype == "rate_limit" and _is_daily_limit_error(message)
            if provider == "groq":
                if etype == "rate_limit":
                    if is_daily:
                        msg = ("Limite diario de Groq alcanzado. Usando Gemini como respaldo." if has_gemini
                               else "Limite diario de Groq alcanzado. Configura API Gemini desde Monitor, o espera hasta manana.")
                    else:
                        msg = ("Cuota por minuto de Groq excedida. Usando Gemini como respaldo." if has_gemini
                               else f"Cuota de Groq excedida. Configura API Gemini desde Monitor, o espera ~{int(recovery)}s.")
                else:
                    msg = "API key de Groq invalida. Ve a Monitor -> API Groq para cambiarla."
            else:
                if etype == "rate_limit":
                    msg = f"Cuota de Gemini excedida. Reintentando en ~{int(recovery)}s."
                else:
                    msg = "API key de Gemini invalida. Ve a Monitor -> API Gemini para cambiarla."
            self._notify_error(msg, notify=True)

    def _record_api_success(self, provider: str = "groq"):
        self._consecutive_api_failures = 0
        circuit = self._groq_circuit if provider == "groq" else self._gemini_circuit
        circuit.recovery_seconds = self._groq_base_recovery if provider == "groq" else self._gemini_base_recovery
        prev_type = self._circuit_error_type
        circuit.record_success()
        if prev_type is not None:
            self.db.log_system_event("circuit_recovered", f'{{"provider": "{provider}", "type": "recovered"}}')
            self._circuit_error_type = None

    def _invoke_llm_with_timeout(self, prompt, timeout_seconds: int | None = None):
        from runtime.circuit_breaker import CircuitOpenError
        if not self._llm_initialized:
            with self._llm_init_lock:
                if not self._llm_initialized:
                    self._init_llm_providers()
        timeout = timeout_seconds or self.llm_timeout_seconds
        groq_skipped = False

        # Intentar Groq primero
        if self._groq_llm:
            try:
                self._groq_circuit.before_call()
            except CircuitOpenError:
                groq_skipped = True
            else:
                metrics.inc(M_LLM_CALLS_TOTAL)
                future = self._llm_executor.submit(self._groq_llm.invoke, prompt)
                try:
                    with metrics.span(M_LLM_CALL):
                        result = future.result(timeout=timeout)
                    self._record_api_success("groq")
                    return result
                except TimeoutError:
                    future.cancel()
                    self._record_api_failure(f"Timeout de Groq despues de {timeout}s.", provider="groq")
                except Exception as exc:
                    self._record_api_failure(str(exc), provider="groq")

        # Fallback a Gemini
        if self._gemini_llm:
            try:
                self._gemini_circuit.before_call()
            except CircuitOpenError:
                pass
            else:
                metrics.inc(M_LLM_CALLS_TOTAL)
                future = self._llm_executor.submit(self._gemini_llm.invoke, prompt)
                try:
                    with metrics.span(M_LLM_CALL):
                        result = future.result(timeout=timeout)
                    self._record_api_success("gemini")
                    return result
                except TimeoutError:
                    future.cancel()
                    msg = f"Timeout de Gemini despues de {timeout}s."
                    self._record_api_failure(msg, provider="gemini")
                    raise TimeoutError(msg)
                except Exception as exc:
                    self._record_api_failure(str(exc), provider="gemini")
                    raise

        raise CircuitOpenError("Ambos proveedores LLM estan en espera o sin configurar.")

    def _handle_move_failure(self, filepath: str, move_result: dict):
        message = move_result.get("message", "Error desconocido al mover archivo.")
        if move_result.get("error_code") == "workspace_mismatch":
            self._notify_error(f"Workspace mismatch al mover '{Path(filepath).name}': {message}", notify=True)
        raise MoveFailureError(message, move_result.get("error_code"))

    def _initialize_agent(self):
        if not create_react_agent:
            logger.warning("LangGraph no esta instalado. NAP Files-Sorter correra solo con reglas y fallback local.")
            return None
        if not self.llm:
            logger.error("Orquestador no pudo arrancar el Agente: Motor LLM no disponible.")
            return None

        from modules.crud_executor import get_crud_tools
        from modules.multimodal_parser import get_parser_tools

        self.tools = get_crud_tools(self.workspace_root, self.dry_run, self.destination_aliases) + get_parser_tools()

        taxonomy_prompt = build_taxonomy_prompt(self.config)
        system_prompt = f"""Eres NAP Files-Sorter, un agente de IA autonomo experto en la gestion inteligente de archivos.
Tu mision es organizar el directorio de trabajo del usuario siguiendo ESTRICTAMENTE esta taxonomia:

{taxonomy_prompt}

REGLAS DE OPERACION:
- IGNORAR archivos "desktop.ini" (puedes usar delete_file solo para mandarlos a cuarentena, nunca a Varios).
- Usa 'analyze_document_content' si el nombre es ambiguo, pero da prioridad a las palabras clave listadas.
- Llama a 'move_file' usando EXACTAMENTE la ruta de la categoria como 'destination_folder_name' (ej: "Universidad y Estudio/Actividades y Tareas").
- Usa Varios solo si nombre, tipo, metadatos y contenido no dan evidencia razonable para una categoria especifica.
- Si el archivo es PDF/Office/texto y hay una pista academica, laboral, personal o financiera, elige la categoria especifica correspondiente en vez de Varios.
- NUNCA pidas confirmacion. Usa la herramienta 'move_file' para categorizar el archivo inmediatamente.
"""
        return create_react_agent(
            model=self.llm,
            tools=self.tools,
            prompt=system_prompt,
        )

    # ------------------------------------------------------------------ #
    # Fase 1: reglas deterministicas                                       #
    # ------------------------------------------------------------------ #

    def _process_with_rule(self, filepath: str, filename: str, extension: str | None) -> bool:
        """Intenta clasificar con reglas deterministicas. Retorna True si fue manejado."""
        decision = classify_file(filename, extension, self.config)
        if not decision:
            return False
        logger.info("Regla para '%s': accion=%s categoria=%s dry_run=%s", filename, decision.action, decision.category, self.dry_run)

        if decision.action == "ignore":
            self.db.log_classification_event(
                filepath=filepath,
                decision_source="rule",
                action="ignore",
                old_path=filepath,
                reason=decision.reason,
                confidence=decision.confidence,
                dry_run=self.dry_run,
            )
            self.db.update_file_status(filepath, "processed")
            self._emit(FileState.IGNORED, filepath, filename, decision_source="rule", reason=decision.reason)
            return True

        move_result = move_file_secure(
            source_path=filepath,
            destination_folder_name=decision.category or "Varios",
            workspace_root=self.workspace_root,
            dry_run=self.dry_run,
            destination_aliases=self.destination_aliases,
        )
        if not move_result["ok"]:
            self._handle_move_failure(filepath, move_result)

        self.db.log_classification_event(
            filepath=filepath,
            decision_source="rule",
            action="move",
            old_path=move_result.get("old_path", filepath),
            new_path=move_result.get("new_path"),
            category=decision.category,
            reason=decision.reason,
            confidence=decision.confidence,
            dry_run=move_result.get("dry_run", False),
        )
        self.db.log_action(filepath, "rule_move", move_result["message"])
        if move_result.get("dry_run"):
            logger.info("Dry-run activo: %s permanece pendiente para ejecucion real futura.", filename)
        else:
            self.db.update_file_path(filepath, move_result["new_path"], "processed")
        logger.info(move_result["message"])
        self._emit(FileState.MOVED, filepath, filename, category=decision.category, decision_source="rule")
        return True

    # ------------------------------------------------------------------ #
    # Helpers de movimiento                                                #
    # ------------------------------------------------------------------ #

    def _apply_move(
        self,
        filepath: str,
        category: str,
        decision_source: str,
        reason: str,
        confidence: float | None = None,
    ):
        """Mueve un archivo a la categoria indicada y registra el evento en DB."""
        filename = Path(filepath).name
        move_result = move_file_secure(
            source_path=filepath,
            destination_folder_name=category,
            workspace_root=self.workspace_root,
            dry_run=self.dry_run,
            destination_aliases=self.destination_aliases,
        )
        if not move_result["ok"]:
            self._handle_move_failure(filepath, move_result)

        self.db.log_classification_event(
            filepath=filepath,
            decision_source=decision_source,
            action="move",
            old_path=move_result.get("old_path", filepath),
            new_path=move_result.get("new_path"),
            category=category,
            reason=reason,
            confidence=confidence,
            dry_run=move_result.get("dry_run", False),
        )
        self.db.log_action(filepath, f"{decision_source}_move", move_result["message"])
        if move_result.get("dry_run"):
            logger.info("Dry-run: %s permanece pendiente.", filename)
        else:
            self.db.update_file_path(filepath, move_result["new_path"], "processed")
        logger.info(move_result["message"])
        self._emit(FileState.MOVED, filepath, filename, category=category, decision_source=decision_source)

    def _move_to_fallback_category(self, filepath: str, category: str, reason: str):
        self._apply_move(filepath, category, "system", reason)

    # ------------------------------------------------------------------ #
    # Helpers de contexto y validacion                                    #
    # ------------------------------------------------------------------ #

    def _is_varios_category(self, category: str | None) -> bool:
        return normalize_text(category or "").startswith("varios")

    def _fallback_category_for(self, file_info: dict | None = None, filepath: str | None = None) -> str:
        extension = ""
        if file_info:
            extension = file_info.get("extension") or file_info.get("metadata", {}).get("extension") or ""
        if not extension and filepath:
            extension = Path(filepath).suffix
        if is_document_extension(extension):
            return self.ambiguous_document_fallback_category
        return "Varios"

    def _normalize_category(self, category: str | None) -> str | None:
        if not category:
            return None
        raw = str(category).strip().strip('"').strip("'").replace("\\", "/")
        raw = re.sub(r"\s*/\s*", "/", raw)
        raw = re.sub(r"^\d+\.\s*", "", raw)
        if not raw:
            return None
        if self._is_varios_category(raw):
            return raw if raw == "Varios" or raw.startswith("Varios/") else "Varios"

        by_normalized = {normalize_text(item): item for item in self._taxonomy_categories}
        direct = by_normalized.get(normalize_text(raw))
        if direct:
            return direct

        # Some models return only a numbered top-level alias. Strip the number
        # and retry against full taxonomy paths before treating it as invalid.
        normalized_raw = normalize_text(raw)
        for logical, physical in self.destination_aliases.items():
            physical_norm = normalize_text(re.sub(r"^\d+\.\s*", "", physical))
            if normalized_raw.startswith(physical_norm):
                suffix = raw.split("/", 1)[1] if "/" in raw else ""
                candidate = f"{logical}/{suffix}" if suffix else logical
                direct = by_normalized.get(normalize_text(candidate))
                if direct:
                    return direct
        return None

    def _response_text(self, response) -> str:
        content = getattr(response, "content", response)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    parts.append(str(item.get("text") or item.get("content") or ""))
                else:
                    parts.append(str(item))
            return "\n".join(part for part in parts if part)
        return str(content)

    def _extract_json_payload(self, text: str):
        text = text.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        match = re.search(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                return None
        return None

    def _parse_llm_decisions(self, response) -> list[dict] | None:
        payload = self._extract_json_payload(self._response_text(response))
        if payload is None:
            return None
        if isinstance(payload, dict):
            if isinstance(payload.get("decisions"), list):
                return payload["decisions"]
            if "filepath" in payload or "category" in payload:
                return [payload]
        if isinstance(payload, list):
            return payload
        return None

    def _compact_prompt_payload(self, info: dict) -> dict:
        metadata = info.get("metadata", {})
        payload = {
            "filepath": info.get("filepath"),
            "filename": info.get("filename"),
            "extension": info.get("extension"),
            "type_group": metadata.get("type_group"),
            "mime_type": metadata.get("mime_type"),
            "size": metadata.get("size_label") or metadata.get("size_bytes"),
            "modified_time": metadata.get("modified_time"),
            "document_metadata": metadata.get("document_metadata"),
            "media_metadata": metadata.get("media_metadata"),
            "archive_metadata": metadata.get("archive_metadata"),
            "content_preview": metadata.get("content_preview"),
            "local_candidates": info.get("local_candidates"),
        }
        return {key: value for key, value in payload.items() if value not in (None, "", [], {})}

    def _build_file_infos(self, files: list[dict], max_chars: int) -> list[dict]:
        from modules.multimodal_parser import collect_file_metadata

        def _build(f):
            filepath = f["filepath"]
            metadata = collect_file_metadata(filepath, max_chars=max_chars, include_content=True)
            extension = f.get("extension") or metadata.get("extension") or Path(filepath).suffix
            info = {
                "filepath": filepath,
                "filename": f["filename"],
                "extension": extension,
                "metadata": metadata,
            }
            info["local_candidates"] = rank_file_categories(
                f["filename"],
                extension,
                metadata,
                self.config,
                limit=3,
            )
            return info

        workers = min(8, max(1, len(files)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(_build, files))

    def _best_local_candidate(self, info: dict) -> dict | None:
        candidates = info.get("local_candidates") or []
        return candidates[0] if candidates else None

    def _should_cache_decision(self, category: str, confidence: float | None) -> bool:
        if self._is_varios_category(category):
            return False
        return confidence is None or confidence >= 0.72

    def _coerce_confidence(self, value) -> float | None:
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            return None
        return max(0.0, min(1.0, confidence))

    def _final_category_from_llm(self, info: dict, decision: dict | None) -> tuple[str, str, float | None]:
        decision = decision or {}
        confidence = self._coerce_confidence(decision.get("confidence"))
        category = self._normalize_category(decision.get("category"))
        reason = str(decision.get("reason") or "Clasificacion por LLM.").strip()
        local = self._best_local_candidate(info)
        local_category = local.get("category") if local else None
        local_confidence = float(local.get("confidence", 0.0)) if local else 0.0
        is_doc = is_document_extension(info.get("extension"))

        if not category:
            if local_category and local_confidence >= 0.72:
                return local_category, f"LLM no devolvio categoria valida; se uso candidato local: {local.get('reason')}.", local_confidence
            fallback = self._fallback_category_for(info)
            return fallback, "LLM no devolvio categoria valida; enviado a revision.", confidence

        if self._is_varios_category(category) and is_doc:
            llm_confidence = confidence if confidence is not None else 0.0
            if local_category and local_confidence >= 0.72 and llm_confidence < self.varios_min_confidence:
                return (
                    local_category,
                    f"LLM propuso Varios con baja evidencia; candidato local usado: {local.get('reason')}.",
                    max(local_confidence, llm_confidence),
                )
            if llm_confidence < self.varios_min_confidence:
                fallback = self._fallback_category_for(info)
                return (
                    fallback,
                    "LLM no tuvo confianza suficiente para mandar documento a Varios; enviado a revision.",
                    confidence,
                )

        return category, reason, confidence

    # ------------------------------------------------------------------ #
    # Fase 2: clasificacion por lote                                       #
    # ------------------------------------------------------------------ #

    def _classify_batch_with_llm(self, files: list[dict]) -> list[dict] | None:
        """
        Clasifica un lote de archivos ambiguos en una sola llamada al LLM.
        Los archivos llegan enriquecidos con metadatos/previews compactos.
        Retorna list[{filepath, category, confidence, reason}] o None si falla.
        """
        taxonomy = build_taxonomy_prompt(self.config)
        files_json = json.dumps([self._compact_prompt_payload(f) for f in files], ensure_ascii=False, indent=2)

        prompt = (
            "Eres un clasificador de archivos preciso y conservador. Clasifica cada archivo segun la taxonomia.\n"
            "Responde EXCLUSIVAMENTE con un JSON array valido, sin texto adicional ni bloques markdown.\n\n"
            f"Taxonomia:\n{taxonomy}\n\n"
            "Politica para Varios:\n"
            "- Usa Varios solo si nombre, extension, metadatos y preview no dan evidencia razonable para una categoria especifica.\n"
            "- No mandes PDF, DOCX, XLSX, PPTX o TXT a Varios si hay senales academicas, laborales, personales, financieras o de salud.\n"
            "- Si hay varias opciones, elige la categoria mas especifica y reporta confianza moderada en vez de usar Varios.\n\n"
            "Formato de respuesta (un objeto por archivo):\n"
            '[{"filepath": "<ruta exacta>", "category": "<categoria exacta de la taxonomia o Varios>", '
            '"confidence": 0.0, "reason": "<evidencia breve>"}]\n\n'
            f"Archivos a clasificar:\n{files_json}"
        )

        try:
            response = self._invoke_llm_with_timeout(prompt, timeout_seconds=60)
            results = self._parse_llm_decisions(response)
            if not isinstance(results, list):
                logger.warning("Batch LLM no retorno JSON valido. Respuesta: %.200s", self._response_text(response))
                return None
            logger.info("Batch LLM clasifico %d/%d archivos en una sola llamada.", len(results), len(files))
            return results
        except TimeoutError as e:
            logger.error("Timeout en clasificacion por lote: %s", e)
            return None
        except Exception as e:
            from runtime.circuit_breaker import CircuitOpenError
            if isinstance(e, CircuitOpenError):
                logger.warning("Batch LLM saltado (circuit ABIERTO): %s", e)
            else:
                logger.error("Error en clasificacion por lote: %s", e)
            return None

    def _classify_single_with_llm(self, file_info: dict) -> dict | None:
        """Clasificacion LLM individual con contexto amplio para pocos archivos."""
        taxonomy = build_taxonomy_prompt(self.config)
        file_json = json.dumps(self._compact_prompt_payload(file_info), ensure_ascii=False, indent=2)
        prompt = (
            "Eres un clasificador de archivos muy preciso. Analiza un solo archivo usando nombre, tipo, "
            "metadatos y preview de contenido.\n"
            "Responde EXCLUSIVAMENTE con un objeto JSON valido, sin markdown.\n\n"
            f"Taxonomia:\n{taxonomy}\n\n"
            "Reglas:\n"
            "- Elige una categoria exacta de la taxonomia cuando exista evidencia razonable.\n"
            "- Varios es ultima opcion: usalo solo si no hay evidencia util en nombre, tipo, metadatos ni contenido.\n"
            "- Para PDF/Office/texto, si hay pistas academicas, laborales, personales, financieras o de salud, "
            "elige la categoria especifica correspondiente aunque la confianza no sea perfecta.\n"
            "- Usa confidence entre 0 y 1 y explica la evidencia en reason.\n\n"
            'Formato: {"filepath": "<ruta exacta>", "category": "<categoria exacta o Varios>", '
            '"confidence": 0.0, "reason": "<evidencia breve>"}\n\n'
            f"Archivo:\n{file_json}"
        )

        try:
            response = self._invoke_llm_with_timeout(prompt, timeout_seconds=self.llm_timeout_seconds)
            results = self._parse_llm_decisions(response)
            if not results:
                logger.warning("LLM individual no retorno JSON valido para %s: %.200s", file_info["filename"], self._response_text(response))
                return None
            return results[0]
        except TimeoutError as e:
            logger.error("Timeout en clasificacion individual para %s: %s", file_info["filename"], e)
            return None
        except Exception as e:
            from runtime.circuit_breaker import CircuitOpenError
            if isinstance(e, CircuitOpenError):
                logger.warning("LLM individual saltado (circuit ABIERTO): %s", e)
            else:
                logger.error("Error en clasificacion individual para %s: %s", file_info["filename"], e)
            return None

    # ------------------------------------------------------------------ #
    # Fase 3: fallback ReAct individual                                    #
    # ------------------------------------------------------------------ #

    def _process_file_with_agent(self, filepath: str, filename: str) -> str:
        """Procesa un archivo con el agente ReAct. El path debe estar ya claimed."""
        logger.info("Fallback ReAct para archivo ambiguo: %s", filename)
        prompt_input = (
            "Nuevo archivo detectado:\n"
            f"Ruta absoluta: '{filepath}'\n"
            f"Nombre: '{filename}'\n"
            "Por favor, analiza el archivo (si es posible) y ejecuta la accion "
            "(tool) mas apropiada para organizarlo o procesarlo inmediatamente."
        )
        try:
            from runtime.circuit_breaker import CircuitOpenError
            self._circuit.before_call()  # raises CircuitOpenError if OPEN
            consume_thread_moves()
            response = self.agent.invoke({"messages": [("user", prompt_input)]})
            resultado = response["messages"][-1].content
            logger.info("[Respuesta NAP para %s]:\n%s", filename, resultado)
            tool_moves = consume_thread_moves()
            successful_moves = [move for move in tool_moves if move.get("ok")]
            if successful_moves:
                last_move = successful_moves[-1]
                self.db.log_classification_event(
                    filepath=filepath,
                    decision_source="llm",
                    action="move",
                    old_path=last_move.get("old_path", filepath),
                    new_path=last_move.get("new_path"),
                    category=None,
                    reason="Movimiento ejecutado por agente LLM (fallback individual).",
                    confidence=None,
                    dry_run=last_move.get("dry_run", False),
                )
                self.db.log_action(filepath, "llm_move", last_move["message"])
                if not last_move.get("dry_run"):
                    self.db.update_file_path(filepath, last_move["new_path"], "processed")
                self._record_api_success()
            elif tool_moves:
                self._handle_move_failure(filepath, tool_moves[-1])
            else:
                self.db.update_file_status(filepath, "processed")
                self.db.log_action(filepath, "llm_analysis", "Analisis preliminar completado.")
                self._record_api_success()
            return "processed"
        except MoveFailureError as e:
            logger.error("Movimiento fallido en agente ReAct para %s: %s", filename, e)
            self.db.update_file_status(filepath, "error")
            self._emit(FileState.ERROR, filepath, filename, reason=str(e))
            return "error"
        except Exception as e:
            from runtime.circuit_breaker import CircuitOpenError
            if isinstance(e, CircuitOpenError):
                if self._circuit_error_type == "rate_limit":
                    logger.info("ReAct saltado (rate limit): %s -- se reintentara automaticamente.", e)
                    return "skipped"  # archivo queda pending, catch-up lo reintenta
                else:
                    logger.warning("ReAct saltado (circuit ABIERTO auth): %s -- usando fallback controlado.", e)
                    try:
                        self._apply_move(
                            filepath,
                            self._fallback_category_for(filepath=filepath),
                            "system",
                            f"Circuit abierto: {e}",
                        )
                        return "processed"
                    except Exception as e2:
                        logger.error("Fallback controlado fallo para %s: %s", filename, e2)
            else:
                logger.error("Error en agente ReAct para %s: %s", filename, e)
                self._record_api_failure(str(e))
            self.db.update_file_status(filepath, "error")
            self._emit(FileState.ERROR, filepath, filename, reason=str(e))
            return "error"

    # ------------------------------------------------------------------ #
    # Procesamiento de archivos ambiguos (fases 2 + 3)                    #
    # ------------------------------------------------------------------ #

    def _update_tray_progress(
        self,
        tray,
        status: str,
        result: dict,
        base_processed_total: int,
        base_errors_total: int,
        pending: int = 0,
    ):
        if tray and hasattr(tray, "update_stats"):
            tray.update_stats(
                status=status,
                pending=pending,
                processed_total=base_processed_total + result.get("processed", 0),
                errors_total=base_errors_total + result.get("errors", 0),
                processing=True,
            )

    def _process_remaining_ambiguous(
        self,
        remaining: list[dict],
        result: dict,
        tray=None,
        base_processed_total: int = 0,
        base_errors_total: int = 0,
        prefer_bulk: bool = False,
    ):
        if self.llm:
            self._update_tray_progress(
                tray,
                f"LLM: {'bulk' if prefer_bulk else 'individual'} para {len(remaining)} ambiguos",
                result,
                base_processed_total,
                base_errors_total,
                pending=len(remaining),
            )

            if prefer_bulk:
                classifications = self._classify_batch_with_llm(remaining)
                if classifications is not None:
                    by_path = {
                        c.get("filepath"): c
                        for c in classifications
                        if isinstance(c, dict) and c.get("filepath")
                    }
                    successful = 0
                    for info in remaining:
                        filepath = info["filepath"]
                        try:
                            category, reason, confidence = self._final_category_from_llm(info, by_path.get(filepath))
                            self._apply_move(filepath, category, "llm_batch", reason, confidence=confidence)
                            if self._should_cache_decision(category, confidence):
                                self._cache.put(info["filename"], info.get("extension") or "", category, "llm_batch")
                            result["processed"] += 1
                            successful += 1
                        except Exception as e:
                            logger.error("Error aplicando movimiento de lote para %s: %s", info["filename"], e)
                            self.db.update_file_status(filepath, "error")
                            result["errors"] += 1
                            self._emit(FileState.ERROR, filepath, info["filename"], reason=str(e))
                        finally:
                            self._release_path(filepath)
                    self._update_tray_progress(
                        tray,
                        f"LLM bulk: {successful}/{len(remaining)} clasificados",
                        result,
                        base_processed_total,
                        base_errors_total,
                        pending=0,
                    )
                    return
            else:
                failed_infos = []
                successful = 0
                for info in remaining:
                    filepath = info["filepath"]
                    release_now = True
                    try:
                        decision = self._classify_single_with_llm(info)
                        if decision is None:
                            failed_infos.append(info)
                            release_now = False
                            continue
                        category, reason, confidence = self._final_category_from_llm(info, decision)
                        self._apply_move(filepath, category, "llm_individual", reason, confidence=confidence)
                        if self._should_cache_decision(category, confidence):
                            self._cache.put(info["filename"], info.get("extension") or "", category, "llm_individual")
                        result["processed"] += 1
                        successful += 1
                    except Exception as e:
                        logger.error("Error aplicando movimiento individual para %s: %s", info["filename"], e)
                        self.db.update_file_status(filepath, "error")
                        result["errors"] += 1
                        self._emit(FileState.ERROR, filepath, info["filename"], reason=str(e))
                    finally:
                        if release_now:
                            self._release_path(filepath)
                self._update_tray_progress(
                    tray,
                    f"LLM individual: {successful}/{len(remaining)} clasificados",
                    result,
                    base_processed_total,
                    base_errors_total,
                    pending=len(failed_infos),
                )
                if not failed_infos:
                    return
                remaining = failed_infos

            self._update_tray_progress(
                tray,
                f"LLM fallo; fallback para {len(remaining)} archivos",
                result,
                base_processed_total,
                base_errors_total,
                pending=len(remaining),
            )

        for info in remaining:
            filepath = info["filepath"]
            try:
                if self.agent:
                    status = self._process_file_with_agent(filepath, info["filename"])
                    if status == "processed":
                        result["processed"] += 1
                    elif status == "skipped":
                        pass
                    else:
                        result["errors"] += 1
                else:
                    fallback = self._fallback_category_for(info)
                    self._apply_move(filepath, fallback, "system", "Sin agente LLM disponible; fallback controlado.")
                    result["processed"] += 1
            except MoveFailureError as e:
                logger.error("Movimiento fallido en fallback individual para %s: %s", info["filename"], e)
                self.db.update_file_status(filepath, "error")
                result["errors"] += 1
                self._emit(FileState.ERROR, filepath, info["filename"], reason=str(e))
            except Exception as e:
                logger.error("Error en fallback individual para %s: %s; usando fallback controlado.", info["filename"], e)
                try:
                    self._apply_move(filepath, self._fallback_category_for(info), "system", f"Error LLM: {e}")
                    result["processed"] += 1
                except Exception as e2:
                    logger.error("Fallback controlado tambien fallo para %s: %s", info["filename"], e2)
                    self.db.update_file_status(filepath, "error")
                    result["errors"] += 1
                    self._emit(FileState.ERROR, filepath, info["filename"], reason=str(e2))
            finally:
                self._release_path(filepath)

        self._update_tray_progress(
            tray,
            f"Fallback: {len(remaining)} archivos revisados",
            result,
            base_processed_total,
            base_errors_total,
            pending=0,
        )

    def _process_ambiguous_batch(
        self,
        files: list[dict],
        result: dict,
        tray=None,
        base_processed_total: int = 0,
        base_errors_total: int = 0,
        prefer_bulk: bool = False,
    ):
        """
        Clasifica ambiguos en capas:
        cache -> reglas con metadatos/contenido -> LLM individual o bulk -> fallback controlado.
        Libera los paths reclamados al terminar cada archivo.
        """
        # --- Decision cache: apply hits immediately, send misses to LLM ---
        cache_hits = []
        cache_misses = []
        for f in files:
            cached = self._cache.get(f["filename"], f.get("extension") or "")
            if cached is not None and not (is_document_extension(f.get("extension")) and self._is_varios_category(cached)):
                cache_hits.append((f, cached))
                metrics.inc(M_CACHE_HITS)
            else:
                cache_misses.append(f)
                metrics.inc(M_CACHE_MISSES)

        for f, category in cache_hits:
            filepath = f["filepath"]
            try:
                self._apply_move(filepath, category, "cache", "Categoria del cache de decisiones.")
                result["processed"] += 1
            except Exception as e:
                logger.error("Error aplicando cache hit para %s: %s", f["filename"], e)
                self.db.update_file_status(filepath, "error")
                result["errors"] += 1
                self._emit(FileState.ERROR, filepath, f["filename"], reason=str(e))
            finally:
                self._release_path(filepath)

        if not cache_misses:
            return
        max_chars = self.llm_bulk_content_max_chars if prefer_bulk else self.llm_individual_content_max_chars
        self._update_tray_progress(
            tray,
            f"Analizando metadatos de {len(cache_misses)} ambiguos",
            result,
            base_processed_total,
            base_errors_total,
            pending=len(cache_misses),
        )
        file_infos = self._build_file_infos(cache_misses, max_chars=max_chars)

        remaining = []
        for info in file_infos:
            filepath = info["filepath"]
            handled = False
            try:
                decision = classify_file_context(
                    info["filename"],
                    info.get("extension"),
                    info.get("metadata"),
                    self.config,
                    min_confidence=self.local_context_min_confidence,
                )
                if decision and not self._is_varios_category(decision.category):
                    self._apply_move(
                        filepath,
                        decision.category,
                        "metadata_rule",
                        decision.reason,
                        confidence=decision.confidence,
                    )
                    if self._should_cache_decision(decision.category, decision.confidence):
                        self._cache.put(info["filename"], info.get("extension") or "", decision.category, "metadata_rule")
                    result["processed"] += 1
                    handled = True
                else:
                    remaining.append(info)
            except Exception as e:
                logger.error("Error aplicando regla con metadatos para %s: %s", info["filename"], e)
                self.db.update_file_status(filepath, "error")
                result["errors"] += 1
                self._emit(FileState.ERROR, filepath, info["filename"], reason=str(e))
                handled = True
            finally:
                if handled:
                    self._release_path(filepath)

        if not remaining:
            self._update_tray_progress(
                tray,
                "Metadatos: todos clasificados",
                result,
                base_processed_total,
                base_errors_total,
                pending=0,
            )
            return

        self._process_remaining_ambiguous(
            remaining,
            result,
            tray=tray,
            base_processed_total=base_processed_total,
            base_errors_total=base_errors_total,
            prefer_bulk=prefer_bulk,
        )
        return

    # ------------------------------------------------------------------ #
    # Procesamiento de carpetas                                            #
    # ------------------------------------------------------------------ #

    def _apply_folder_move(
        self,
        folderpath: str,
        foldername: str,
        category: str,
        decision_source: str,
        reason: str,
        confidence: float | None,
    ):
        """Moves a folder to the target category and records the event in DB."""
        move_result = move_folder_secure(
            source_path=folderpath,
            destination_folder_name=category,
            workspace_root=self.workspace_root,
            dry_run=self.dry_run,
            destination_aliases=self.destination_aliases,
        )
        if not move_result["ok"]:
            self._handle_move_failure(folderpath, move_result)

        self.db.log_classification_event(
            filepath=folderpath,
            decision_source=decision_source,
            action="move",
            old_path=move_result.get("old_path", folderpath),
            new_path=move_result.get("new_path"),
            category=category,
            reason=reason,
            confidence=confidence,
            dry_run=move_result.get("dry_run", False),
        )
        self.db.log_action(folderpath, f"{decision_source}_move_folder", move_result["message"])
        if not move_result.get("dry_run"):
            self.db.update_file_path(folderpath, move_result["new_path"], "processed")
        logger.info(move_result["message"])
        self._emit(FileState.MOVED, folderpath, foldername, category=category, decision_source=decision_source)

    def _classify_folder_with_llm(
        self, folder_name: str, sample_names: list[str]
    ) -> tuple[str, str, float | None] | None:
        """Classify a folder via LLM using its name and sampled file names."""
        taxonomy = build_taxonomy_prompt(self.config)
        folder_info = {"folder_name": folder_name, "sample_files": sample_names}
        folder_json = json.dumps(folder_info, ensure_ascii=False, indent=2)
        prompt = (
            "Clasifica esta carpeta en una de las categorias de la taxonomia.\n"
            "Usa el nombre de la carpeta y los archivos de muestra como contexto.\n"
            "Responde EXCLUSIVAMENTE con un objeto JSON valido, sin texto adicional ni markdown.\n\n"
            f"Taxonomia:\n{taxonomy}\n\n"
            "Reglas:\n"
            "- Elige la categoria (puede ser top-level o subcategoria) que mejor describa la carpeta.\n"
            "- Si hay mezcla de contenidos, elige la categoria predominante.\n"
            "- Usa Varios solo si no hay evidencia razonable en nombre ni archivos de muestra.\n\n"
            'Formato: {"category": "<categoria exacta o Varios>", "confidence": 0.0, "reason": "<evidencia breve>"}\n\n'
            f"Carpeta:\n{folder_json}"
        )
        try:
            response = self._invoke_llm_with_timeout(prompt, timeout_seconds=self.llm_timeout_seconds)
            results = self._parse_llm_decisions(response)
            if not results:
                logger.warning("LLM no retorno JSON valido para carpeta '%s'.", folder_name)
                return None
            raw = results[0]
            category = self._normalize_category(raw.get("category"))
            if not category:
                return None
            confidence = self._coerce_confidence(raw.get("confidence"))
            reason = str(raw.get("reason") or "Clasificacion de carpeta por LLM.").strip()
            return category, reason, confidence
        except Exception as e:
            from runtime.circuit_breaker import CircuitOpenError
            if isinstance(e, CircuitOpenError):
                logger.warning("LLM de carpeta saltado (circuit ABIERTO): %s", e)
            else:
                logger.error("Error en clasificacion LLM de carpeta '%s': %s", folder_name, e)
            return None

    def _process_folders(
        self,
        folder_records: list[dict],
        result: dict,
        tray=None,
        base_processed_total: int = 0,
        base_errors_total: int = 0,
    ):
        """Process pending folders: local rules + sampling -> LLM -> Varios fallback."""
        logger.info("Procesando %d carpeta(s) pendiente(s)...", len(folder_records))
        for folder_record in folder_records:
            folderpath = folder_record["filepath"]
            foldername = folder_record["filename"]

            if not self._claim_path(folderpath):
                result["skipped"] += 1
                continue

            try:
                self._emit(FileState.PROCESSING, folderpath, foldername)

                # Phase 1+2+3: local rules + name + sampling
                path = Path(folderpath)
                if not path.is_dir():
                    # Folder was moved or deleted externally; clean up
                    self.db.update_file_status(folderpath, "processed")
                    result["processed"] += 1
                    continue

                decision = classify_folder(foldername, folderpath, self.config)
                if decision:
                    logger.info(
                        "Regla para carpeta '%s': categoria=%s confidence=%.2f",
                        foldername, decision.category, decision.confidence,
                    )
                    self._apply_folder_move(
                        folderpath, foldername, decision.category, "rule",
                        decision.reason, decision.confidence,
                    )
                    result["processed"] += 1
                    metrics.inc(M_FILES_PROCESSED)
                    continue

                # Phase 4: LLM
                if self.llm:
                    self._update_tray_progress(
                        tray, f"LLM: clasificando carpeta '{foldername}'",
                        result, base_processed_total, base_errors_total, pending=1,
                    )
                    sample_names = sample_folder_files(path)
                    llm_result = self._classify_folder_with_llm(foldername, sample_names)
                    if llm_result:
                        category, reason, confidence = llm_result
                        self._apply_folder_move(
                            folderpath, foldername, category, "llm_individual", reason, confidence,
                        )
                        result["processed"] += 1
                        metrics.inc(M_FILES_PROCESSED)
                        continue

                # Fallback: Varios/Documentos por Revisar
                fallback_category = "Varios/Documentos por Revisar"
                self._apply_folder_move(
                    folderpath, foldername, fallback_category, "system",
                    "Sin clasificacion clara para la carpeta; enviada a revision.", None,
                )
                result["processed"] += 1
                metrics.inc(M_FILES_PROCESSED)

            except MoveFailureError as e:
                logger.error("Error al mover carpeta '%s': %s", foldername, e)
                self.db.update_file_status(folderpath, "error")
                result["errors"] += 1
                metrics.inc(M_FILES_ERRORS)
                self._emit(FileState.ERROR, folderpath, foldername, reason=str(e))
            except Exception as e:
                logger.error("Error procesando carpeta '%s': %s", foldername, e)
                self.db.update_file_status(folderpath, "error")
                result["errors"] += 1
                metrics.inc(M_FILES_ERRORS)
                self._emit(FileState.ERROR, folderpath, foldername, reason=str(e))
            finally:
                self._release_path(folderpath)

    # ------------------------------------------------------------------ #
    # Control de concurrencia                                              #
    # ------------------------------------------------------------------ #

    def _claim_path(self, filepath: str) -> bool:
        with self._active_paths_lock:
            if filepath in self._active_paths:
                return False
            self._active_paths.add(filepath)
            return True

    def _release_path(self, filepath: str):
        with self._active_paths_lock:
            self._active_paths.discard(filepath)

    # ------------------------------------------------------------------ #
    # Punto de entrada del ciclo de procesamiento                          #
    # ------------------------------------------------------------------ #

    def process_pending_files(self, tray=None, base_processed_total: int = 0, base_errors_total: int = 0):
        """Consulta la BD por archivos pendientes y los procesa en 3 fases."""
        with metrics.span(M_CYCLE_DURATION):
            return self._process_pending_files_inner(tray, base_processed_total, base_errors_total)

    def _process_pending_files_inner(self, tray=None, base_processed_total: int = 0, base_errors_total: int = 0):
        pending_items = self.db.get_pending_files(limit=self.max_files_per_cycle)
        result = {"pending": len(pending_items), "processed": 0, "errors": 0, "skipped": 0}

        logger.info("Pendientes en BD: %s (limite ciclo: %s)", len(pending_items), self.max_files_per_cycle)
        if not pending_items:
            logger.info("Sin archivos pendientes. Esperando proximo ciclo.")
            return result

        # Separate folders from files
        folder_records = [item for item in pending_items if item.get("is_directory")]
        pending_files = [item for item in pending_items if not item.get("is_directory")]

        logger.info(
            "Orquestador detecto %s elemento(s): %s archivo(s), %s carpeta(s).",
            len(pending_items), len(pending_files), len(folder_records),
        )

        # Process folders first (before file pipeline)
        if folder_records:
            self._process_folders(folder_records, result, tray, base_processed_total, base_errors_total)

        if not pending_files:
            return result

        # Fase 1: reglas deterministicas -- sin API, rapido
        ambiguous = []
        with metrics.span(M_PHASE1_DURATION):
            for file_record in pending_files:
                filepath = file_record["filepath"]
                filename = file_record["filename"]
                self._emit(FileState.QUEUED, filepath, filename)
                if not self._claim_path(filepath):
                    logger.debug("Archivo ya en procesamiento: %s", filename)
                    result["skipped"] += 1
                    continue
                self._emit(FileState.PROCESSING, filepath, filename)
                try:
                    rule_result = self._process_with_rule(filepath, filename, file_record.get("extension"))
                    if rule_result:
                        result["processed"] += 1
                        metrics.inc(M_FILES_PROCESSED)
                        self._release_path(filepath)
                    else:
                        ambiguous.append(file_record)  # path sigue claimed hasta el lote
                except Exception as e:
                    logger.error("Error en regla para %s: %s", filename, e)
                    self.db.update_file_status(filepath, "error")
                    self._release_path(filepath)
                    result["errors"] += 1
                    metrics.inc(M_FILES_ERRORS)
                    self._emit(FileState.ERROR, filepath, filename, reason=str(e))

        self._update_tray_progress(
            tray,
            f"Reglas: {result['processed']} clasificados; {len(ambiguous)} ambiguos",
            result,
            base_processed_total,
            base_errors_total,
            pending=len(ambiguous),
        )

        if not ambiguous:
            return result

        logger.info(
            "%d archivo(s) ambiguo(s) -> clasificacion por lote (chunks de %d).",
            len(ambiguous),
            self.llm_batch_size,
        )

        # Fases 2+3: lote LLM con fallback ReAct
        _phase23_start = time.perf_counter()
        prefer_bulk = len(ambiguous) > self.llm_individual_threshold
        for i in range(0, len(ambiguous), self.llm_batch_size):
            chunk = ambiguous[i : i + self.llm_batch_size]
            _chunk_processed_before = result["processed"]
            _chunk_errors_before = result["errors"]
            self._process_ambiguous_batch(
                chunk,
                result,
                tray=tray,
                base_processed_total=base_processed_total,
                base_errors_total=base_errors_total,
                prefer_bulk=prefer_bulk,
            )
            metrics.inc(M_FILES_PROCESSED, result["processed"] - _chunk_processed_before)
            metrics.inc(M_FILES_ERRORS, result["errors"] - _chunk_errors_before)
            # Pace API calls — Groq free tier: 30 req/min; Gemini free tier: 15 req/min
            if i + self.llm_batch_size < len(ambiguous):
                time.sleep(2)
        metrics.record(M_PHASE2_DURATION, time.perf_counter() - _phase23_start)

        return result
