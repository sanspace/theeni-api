# app/settings.py

import os
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

# This loads the .env file
load_dotenv()

class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.
    """
    DATABASE_URL: str

    class Config:
        # This tells Pydantic to look for a .env file
        env_file = ".env"
        env_file_encoding = "utf-8"

# Create a single instance of the settings to use throughout the app
settings = Settings()
