from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_dsn: str
    clickhouse_host: str = "localhost"
    clickhouse_port: int = 8123
    clickhouse_user: str = "default"
    clickhouse_password: str
    clickhouse_database: str = "default"

    frankfurter_url: str = "https://api.frankfurter.dev"
    backfill_days: int = 365
    poll_interval_seconds: int = 3600

    grpc_port: int = 50051
