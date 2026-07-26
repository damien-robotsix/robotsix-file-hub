# Changelog

## 0.0.0 (unreleased)

- Add file upload endpoints: `POST /files` (single) and `POST /files/batch`
  with storage backend abstraction (S3/MinIO via boto3 and local filesystem fallback)
- Set up Python package scaffold: `pyproject.toml`, `src/robotsix_file_hub/` with minimal FastAPI app stub, and dev tooling config (ruff, mypy, pytest)
