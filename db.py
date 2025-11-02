# db.py
import os
import time
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
from sqlalchemy import create_engine, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import OperationalError

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL no esta definida. Configure la variable DATABASE_URL en Render.")

def ensure_sslmode(url: str) -> str:
    try:
        p = urlparse(url)
        qs = dict(parse_qsl(p.query))
        if "sslmode" not in qs:
            qs["sslmode"] = "require"
            p = p._replace(query=urlencode(qs))
        return urlunparse(p)
    except Exception:
        return url

DATABASE_URL = ensure_sslmode(DATABASE_URL)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=3,
    max_overflow=5,
    pool_timeout=15,
    connect_args={"connect_timeout": 10},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def obtener_bd(retries: int = 5, initial_delay: float = 1.0):
    delay = initial_delay
    last_exc = None
    for attempt in range(1, retries + 1):
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1")).fetchone()
            try:
                yield db
            finally:
                try:
                    db.close()
                except Exception:
                    pass
            return
        except Exception as e:
            last_exc = e
            try:
                db.close()
            except Exception:
                pass
            if attempt < retries:
                time.sleep(delay)
                delay *= 2
            else:
                raise OperationalError(f"Failed to connect to DB after {retries} attempts: {str(last_exc)}", None, None)
