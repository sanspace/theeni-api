# app/settings.py
import os
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

# Render automatically sets a 'RENDER' environment variable.
# We can check for its existence to determine the environment.
if os.getenv("RENDER"):
    print("Production environment detected. Loading secrets from Render's path.")
    # Render's default path for secret files is /etc/secrets/.env
    load_dotenv(dotenv_path="/etc/secrets/.env")
else:
    print("Development environment detected. Loading secrets from local .env file.")
    # This loads the local .env file for development
    load_dotenv()

class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.
    """
    DATABASE_URL: str
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24
    # It expects a comma-separated string, e.g., "http://url1.com,http://url2.com"
    ALLOWED_ORIGINS: str = ""

# Create a single instance of the settings to use throughout the app
settings = Settings()
