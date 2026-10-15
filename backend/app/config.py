from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://bapc:bapc@localhost:5432/bapc"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"

    # Clinic operating rules. All appointment times are interpreted in the clinic's timezone.
    clinic_timezone: str = "America/Chicago"
    clinic_open_hour: int = 8
    clinic_close_hour: int = 17
    # Monday=0 ... Sunday=6
    clinic_open_weekdays: list[int] = [0, 1, 2, 3, 4]

    # Auth
    session_cookie_name: str = "bapc_session"
    session_ttl_hours: int = 12
    session_cookie_secure: bool = False

    # Seed accounts (development only)
    seed_admin_email: str = "admin@bapc.test"
    seed_admin_password: str = "admin-dev-password"
    seed_patient_password: str = "patient-dev-password"

    model_config = {"env_prefix": "BAPC_", "env_file": ".env", "extra": "ignore"}


settings = Settings()
