"""Application settings via pydantic-settings."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite+aiosqlite:///./file_hub.db"
    storage_backend: str = "local"  # "local" or "s3"
    local_storage_path: str = "./uploads"
    s3_endpoint: str = ""
    s3_bucket: str = "file-hub"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_region: str = "us-east-1"
    max_file_size: int = 100 * 1024 * 1024  # 100 MB

    model_config = {"env_prefix": "FILE_HUB_"}
