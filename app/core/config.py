from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List

class Settings(BaseSettings):
    # Această linie îi spune Pydantic să citească automat fișierul .env
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_NAME: str = "toolbaz-2api"
    APP_VERSION: str = "3.1.0 (Modele Complete)"
    
    # Dacă în .env nu este configurat API_MASTER_KEY, valoarea implicită este "1"
    API_MASTER_KEY: str = "1"
    
    # 🔥 Listă completă de modele
    MODELS: List[str] = [
        "toolbaz-v4.5-fast",
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        "claude-sonnet-4",
        "gpt-5",
        "grok-4-fast"
    ]
    DEFAULT_MODEL: str = "toolbaz-v4.5-fast"

    # 🔥 Configurare concurență
    # Valoarea 1 este pentru a preveni erorile dacă lipsește configurarea în .env.
    BROWSER_POOL_SIZE: int = 1
    
    # Numărul maxim de utilizări ale unui context înainte de resetare
    CONTEXT_MAX_USES: int = 50 

settings = Settings()