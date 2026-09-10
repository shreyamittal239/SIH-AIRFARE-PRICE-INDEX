import os
from pathlib import Path
from typing import Generator
# pyrefly: ignore [missing-import]
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

# Search for .env in project root or current working directory
current_dir = Path(__file__).resolve().parent
for parent in [current_dir, *current_dir.parents]:
    env_file = parent / ".env"
    if env_file.is_file():
        load_dotenv(env_file)
        break
else:
    load_dotenv()

postgres_user = os.getenv("POSTGRES_USER", "airfare_user")
postgres_password = os.getenv("POSTGRES_PASSWORD", "airfare_password")
postgres_host = os.getenv("POSTGRES_HOST", "localhost")
postgres_port = os.getenv("POSTGRES_PORT", "5432")
postgres_db = os.getenv("POSTGRES_DB", "airfare_index")

default_url = (
    f"postgresql+psycopg://{postgres_user}:{postgres_password}@"
    f"{postgres_host}:{postgres_port}/{postgres_db}"
)

DATABASE_URL = os.getenv("DATABASE_URL", default_url)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """Provide a transactional database session scope."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
