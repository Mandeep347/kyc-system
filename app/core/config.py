from datetime import date
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    seed: int = 20261022
    as_of_date: date = date(2026, 10, 30)
    ground_truth_dir: str = "output/ground_truth"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()