-- db/schema.sql
-- Tabla principal para registrar el estado y metadatos de los archivos del workspace
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    filepath TEXT NOT NULL UNIQUE,
    extension TEXT,
    size_bytes INTEGER,
    is_directory INTEGER DEFAULT 0,
    status TEXT DEFAULT 'pending', -- Estados: pending, processed, error
    retry_count INTEGER NOT NULL DEFAULT 0,
    last_modified TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Tabla para mantener un registro de auditorÃ­a/logs de las acciones tomadas por la IA
CREATE TABLE IF NOT EXISTS actions_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER,
    action_type TEXT NOT NULL, -- Ejemplos: 'categorize', 'move', 'extract_data'
    description TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE
);

-- Registro detallado de decisiones de clasificacion y movimientos propuestos/ejecutados
CREATE TABLE IF NOT EXISTS classification_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER,
    decision_source TEXT NOT NULL, -- rule|llm|system
    action TEXT NOT NULL,
    old_path TEXT,
    new_path TEXT,
    category TEXT,
    reason TEXT,
    confidence REAL,
    dry_run INTEGER DEFAULT 0,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE SET NULL
);

-- Indices: get_pending_files filtra por status en cada ciclo y NAPMonitor
-- ordena classification_events por timestamp cada 3s. Con carpetas de 70k+
-- archivos, sin indices ambas consultas escanean la tabla completa.
CREATE INDEX IF NOT EXISTS idx_files_status ON files(status);
CREATE INDEX IF NOT EXISTS idx_class_events_timestamp ON classification_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_class_events_file_id ON classification_events(file_id);
CREATE INDEX IF NOT EXISTS idx_actions_log_file_id ON actions_log(file_id);
