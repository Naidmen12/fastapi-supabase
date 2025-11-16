# db.py (version optimizada con circuit-breaker simple)
import os
import time
import random
import logging
import threading
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

from sqlalchemy import create_engine, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import OperationalError

from fastapi import HTTPException

logger = logging.getLogger("uvicorn.error")

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

# --- CONFIG desde ENV (valores prudentes por defecto) ---
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL no esta definida. Configure la variable DATABASE_URL en Render.")

DATABASE_URL = ensure_sslmode(DATABASE_URL)

POOL_SIZE = int(os.environ.get("POOL_SIZE", "1"))        # valor conservador por defecto
MAX_OVERFLOW = int(os.environ.get("MAX_OVERFLOW", "0"))
POOL_TIMEOUT = int(os.environ.get("POOL_TIMEOUT", "15"))
POOL_RECYCLE = int(os.environ.get("POOL_RECYCLE", "1800"))
CONNECT_TIMEOUT = int(os.environ.get("CONNECT_TIMEOUT", "10"))

# circuit-breaker params
FAILURE_THRESHOLD = int(os.environ.get("CB_FAILURE_THRESHOLD", "3"))   # fallos consecutivos para abrir circuito
COOLDOWN_SECONDS = int(os.environ.get("CB_COOLDOWN", "60"))            # cuanto dura abierto el circuito
RETRIES = int(os.environ.get("DB_RETRIES", "2"))                       # reintentos rapidos
INITIAL_DELAY = float(os.environ.get("DB_INITIAL_DELAY", "0.2"))       # delay inicial entre reintentos

logger.info("DB config: POOL_SIZE=%s MAX_OVERFLOW=%s POOL_TIMEOUT=%s", POOL_SIZE, MAX_OVERFLOW, POOL_TIMEOUT)
logger.info("CB config: FAILURE_THRESHOLD=%s COOLDOWN_SECONDS=%s RETRIES=%s", FAILURE_THRESHOLD, COOLDOWN_SECONDS, RETRIES)

# --- engine unico ---
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

# --- estado del circuit-breaker (compartido en proceso) ---
_cb_lock = threading.Lock()
_cb_fail_count = 0
_cb_open_until = 0.0  # timestamp hasta cuando el circuito permanece abierto

def _circuit_is_open() -> bool:
    return time.time() < _cb_open_until

def _record_failure():
    global _cb_fail_count, _cb_open_until
    with _cb_lock:
        _cb_fail_count += 1
        logger.warning("DB failure count = %d", _cb_fail_count)
        if _cb_fail_count >= FAILURE_THRESHOLD:
            _cb_open_until = time.time() + COOLDOWN_SECONDS
            logger.error("Circuit breaker abierto hasta %s (por %d fallos)",
                         time.ctime(_cb_open_until), _cb_fail_count)

def _record_success():
    global _cb_fail_count, _cb_open_until
    with _cb_lock:
        if _cb_fail_count != 0 or _cb_open_until != 0.0:
            logger.info("DB conexion exitosa: reseteando contador de fallos")
        _cb_fail_count = 0
        _cb_open_until = 0.0

def obtener_bd():
    """
    Dependencia FastAPI: yield una session SQLAlchemy.
    - Si el circuit-breaker esta abierto devuelve HTTP 503 inmediatamente.
    - Intenta RETRIES veces con backoff/jitter corto antes de devolver 503.
    - En exito, resetea el circuit-breaker.
    """
    # si circuito abierto -> 503 sin intentar
    if _circuit_is_open():
        until = time.ctime(_cb_open_until)
        logger.warning("Solicitud rechazada por circuit-breaker abierto hasta %s", until)
        raise HTTPException(status_code=503, detail="Base de datos temporalmente inaccesible")

    delay = INITIAL_DELAY
    last_exc = None

    for intento in range(1, RETRIES + 1):
        db = SessionLocal()
        try:
            # ping rapido
            db.execute(text("SELECT 1")).fetchone()
            # exito: resetear circuit-breaker y yield session
            _record_success()
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

            logger.warning("Intento %d/%d - OperationalError al conectar a DB: %s", intento, RETRIES, str(oe))

            # registrar fallo en circuit-breaker
            _record_failure()

            if intento < RETRIES:
                # backoff exponencial con jitter corto
                jitter = random.uniform(0, 0.25 * delay)
                sleep_time = delay + jitter
                logger.info("Durmiendo %.3fs antes del proximo intento", sleep_time)
                time.sleep(sleep_time)
                delay = min(delay * 2, 10.0)  # limitar crecimiento
                continue
            else:
                logger.error("Reintentos DB agotados: %s", str(last_exc), exc_info=True)
                # abrir circuito si no se abrio ya (record_failure lo hace)
                raise HTTPException(status_code=503, detail="Base de datos temporalmente inaccesible")
        except Exception as e:
            try:
                db.close()
            except Exception:
                pass
            logger.exception("Error inesperado al obtener session DB: %s", e)
            # no tocar circuit-breaker en excepciones inesperadas; devolver 500
            raise HTTPException(status_code=500, detail="Error interno al obtener DB")
