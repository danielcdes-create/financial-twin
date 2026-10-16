from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    environment: str = "development"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_base_url: str = ""
    mongodb_uri: str = ""
    mongodb_database: str = "financas"
    mongodb_direct_hosts: str = ""
    mongodb_replica_set: str = ""
    xai_api_key: str = ""
    xai_base_url: str = "https://api.x.ai/v1"
    xai_text_model: str = ""
    xai_vision_model: str = ""
    xai_image_model: str = ""
    xai_transcription_model: str = "grok-voice-transcribe-2.0"
    jwt_secret: str = ""
    api_secret: str = ""
    app_password: str = ""
    account_setup_token: str = ""
    open_finance_provider: str = ""
    open_finance_client_id: str = ""
    open_finance_client_secret: str = ""
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

@lru_cache
def get_settings() -> Settings:
    return Settings()
# Keep xAI model defaults in the adapter; credentials remain server-only.
