# ruff: noqa: F821, B018
# vulture whitelist — symbols that are used but not detected by static analysis.
# Route handlers are discovered by FastAPI at runtime.
# Pydantic model_config is a class variable consumed by the metaclass.
cls  # classmethod receiver in pydantic @field_validator methods
_.model_config  # pydantic settings / base model config
_.converter  # logging.Formatter.converter — set to time.gmtime for UTC logs
_.rate_limit_exceeded_handler  # slowapi exception handler registered on the app
_.serve_ui  # FastAPI route — SPA fallback
_.deploy_spec  # FastAPI route
_.health  # FastAPI route
_.health_live  # FastAPI route
_.upload_file  # FastAPI route
_.upload_files_batch  # FastAPI route
_.download_file  # FastAPI route
_.view_file  # FastAPI route
_.get_file_metadata  # FastAPI route
_.update_file_metadata  # FastAPI route
_.reindex_files  # FastAPI route
_.reindex_progress  # FastAPI route
_.search  # FastAPI route
_.list_files  # FastAPI route
_.list_categories  # FastAPI route
_.delete_file  # FastAPI route
_.get_task_status  # FastAPI route
_.read_config  # FastAPI route — config
_.write_config  # FastAPI route — config
_.config_versions  # FastAPI route — config
_.config_rollback  # FastAPI route — config
_.prune_config  # FastAPI route — config
_.detail  # Pydantic schema field
_.relevance  # Pydantic schema field
_.deduplicated  # Pydantic schema field — FileUploadResponse
_.dimensions  # EmbeddingSettings documentation constraint — assert match with pgvector
_.get_settings  # FastAPI Depends callable — not traced by vulture
