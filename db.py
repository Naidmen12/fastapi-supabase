# db.py
import os
import time
import random
import logging
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

from sqlalchemy import create_engine, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import OperationalError

from fastapi import HTTPException

logger = logging.getLogger("uvicorn.error")

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

# Parametrizables desde ENV para facilitar tuning sin tocar código
POOL_SIZE = int(os.environ.get("POOL_SIZE", "2"))        # valor prudente para free hosting
MAX_OVERFLOW = int(os.environ.get("MAX_OVERFLOW", "0"))  # evitar picos de overflow
POOL_TIMEOUT = int(os.environ.get("POOL_TIMEOUT", "15"))
POOL_RECYCLE = int(os.environ.get("POOL_RECYCLE", "1800"))  # reciclar cada 30 min
CONNECT_TIMEOUT = int(os.environ.get("CONNECT_TIMEOUT", "10"))

logger.info("DB config: POOL_SIZE=%s MAX_OVERFLOW=%s POOL_TIMEOUT=%s", POOL_SIZE, MAX_OVERFLOW, POOL_TIMEOUT)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=POOL_SIZE,
    max_overflow=MAX_OVERFLOW,
    pool_timeout=POOL_TIMEOUT,
    pool_recycle=POOL_RECYCLE,
    connect_args={"connect_timeout": CONNECT_TIMEOUT},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def obtener_bd(retries: int = 3, initial_delay: float = 0.5):
    """
    Dependency para FastAPI: yield una sesión SQLAlchemy.
    Hace retries con backoff exponencial + jitter y devuelve HTTP 503 si la BD
    no está disponible tras los reintentos.
    Uso en FastAPI: def endpoint(db = Depends(obtener_bd)):
    """
    delay = initial_delay
    last_exc = None
    for attempt in range(1, retries + 1):
        db = SessionLocal()
        try:
            # simple ping para asegurar la conexión
            db.execute(text("SELECT 1")).fetchone()
            try:
                yield db
            finally:
                try:
                    db.close()
                except Exception:
                    pass
            return
        except OperationalError as oe:
            last_exc = oe
            try:
                db.close()
            except Exception:
                pass

            logger.warning("DB connection attempt %d/%d failed: %s", attempt, retries, str(oe))
            if attempt < retries:
                # backoff exponencial con jitter
                jitter = random.uniform(0, 0.5 * delay)
                sleep_time = delay + jitter
                logger.info("Sleeping %.3fs before next DB attempt", sleep_time)
                time.sleep(sleep_time)
                delay *= 2
            else:
                # Reintentos agotados: devolver HTTP 503 para que cliente actúe con backoff
                logger.error("Failed to connect to DB after %d attempts: %s", retries, str(last_exc), exc_info=True)
                # Opcional: incluir header Retry-After para indicar cuánto esperar (en segundos)
                raise HTTPException(status_code=503,
                                    detail="Base de datos temporalmente inaccesible",
                                    headers={"Retry-After": "5"})
        except Exception as e:
            # cualquier otro error no previsto -> cerrar sesión y propagar como 500 controlado
            try:
                db.close()
            except Exception:
                pass
            logger.exception("Unexpected error when obtaining DB session: %s", e)
            raise HTTPException(status_code=500, detail="Error interno al obtener DB")
