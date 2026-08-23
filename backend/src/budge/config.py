from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from the environment.

    `database_url` deliberately has no default. An unset
    `PODVINSYA_DATABASE_URL` must fail loudly at startup rather than
    quietly pointing a production process at somebody's scratch database.
    """

    model_config = SettingsConfigDict(env_prefix="PODVINSYA_")

    database_url: str
