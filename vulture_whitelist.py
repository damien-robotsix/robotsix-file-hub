# ruff: noqa: F821, B018
# vulture whitelist — symbols that are used but not detected by static analysis.
# Route handlers are discovered by FastAPI at runtime.
# Pydantic model_config is a class variable consumed by the metaclass.
_.model_config  # pydantic settings / base model config
_.deploy_spec  # FastAPI route
_.health  # FastAPI route
_.upload_file  # FastAPI route
_.upload_files_batch  # FastAPI route
_.download_file  # FastAPI route
_.get_file_metadata  # FastAPI route
_.reindex_files  # FastAPI route
_.reindex_progress  # FastAPI route
_.search  # FastAPI route
_.list_files  # FastAPI route
_.list_categories  # FastAPI route
_.delete_file  # FastAPI route
_.get_task_status  # FastAPI route
_.detail  # Pydantic schema field
_.relevance  # Pydantic schema field
_.get_current_user  # auth dependency — consumed by external consumers and tests
_.get_settings  # FastAPI Depends callable — not traced by vulture
