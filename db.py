# db.py (versión optimizada)
import os
import time
import random
import logging
import threading
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

from sqlalchemy import create_engine, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import OperationalError, DBAPIError

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

POOL_SIZE = int(os.environ.get("POOL_SIZE", "2"))
MAX_OVERFLOW = int(os.environ.get("MAX_OVERFLOW", "0"))
POOL_TIMEOUT = int(os.environ.get("POOL_TIMEOUT", "15"))
POOL_RECYCLE = int(os.environ.get("POOL_RECYCLE", "1800"))
CONNECT_TIMEOUT = int(os.environ.get("CONNECT_TIMEOUT", "10"))

# circuit-breaker params
FAILURE_THRESHOLD = int(os.environ.get("CB_FAILURE_THRESHOLD", "3"))
COOLDOWN_SECONDS = int(os.environ.get("CB_COOLDOWN", "60"))
RETRIES = int(os.environ.get("DB_RETRIES", "3"))
INITIAL_DELAY = float(os.environ.get("DB_INITIAL_DELAY", "0.2"))

logger.info("DB config: POOL_SIZE=%s MAX_OVERFLOW=%s POOL_TIMEOUT=%s POOL_RECYCLE=%s CONNECT_TIMEOUT=%s",
            POOL_SIZE, MAX_OVERFLOW, POOL_TIMEOUT, POOL_RECYCLE, CONNECT_TIMEOUT)
logger.info("CB config: FAILURE_THRESHOLD=%s COOLDOWN_SECONDS=%s RETRIES=%s INITIAL_DELAY=%s",
            FAILURE_THRESHOLD, COOLDOWN_SECONDS, RETRIES, INITIAL_DELAY)

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
_cb_open_until = 0.0  # monotonic timestamp

def _now() -> float:
    return time.monotonic()

def _circuit_is_open() -> bool:
    return _now() < _cb_open_until

def _record_failure():
    global _cb_fail_count, _cb_open_until
    with _cb_lock:
        _cb_fail_count += 1
        logger.warning("DB failure count = %d", _cb_fail_count)
        if _cb_fail_count >= FAILURE_THRESHOLD:
            _cb_open_until = _now() + COOLDOWN_SECONDS
            logger.error("Circuit breaker abierto hasta %s (por %d fallos)",
                         time.ctime(time.time() + ( _cb_open_until - _now() )), _cb_fail_count)

def _record_success():
    global _cb_fail_count, _cb_open_until
    with _cb_lock:
        if _cb_fail_count != 0 or _cb_open_until != 0.0:
            logger.info("DB conexion exitosa: reseteando contador de fallos")
        _cb_fail_count = 0
        _cb_open_until = 0.0

# flag opcional para saber si init_db() ha comprobado la db en startup
_db_warm = False

def init_db(startup_retries: int = 3, startup_delay: float = 1.0):
    """
    Intentar conectar al engine varias veces durante startup para "warmup".
    No lanza error fatal (lo captura y deja que la app inicie), pero registra.
    """
    global _db_warm
    last_exc = None
    for i in range(1, startup_retries + 1):
        try:
            logger.info("Init DB: intento %d/%d", i, startup_retries)
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            _db_warm = True
            logger.info("Init DB: conexión verificada correctamente")
            return
        except Exception as e:
            last_exc = e
            logger.warning("Init DB intento %d falló: %s", i, str(e))
            time.sleep(startup_delay * i)
    logger.error("Init DB: todos los intentos fallaron: %s", str(last_exc), exc_info=True)
    # No raising here para que el proceso siga corriendo; el circuit-breaker maneja peticiones.

def obtener_bd():
    """
    Dependencia FastAPI: yield una session SQLAlchemy.
    - Si el circuit-breaker esta abierto devuelve HTTP 503 inmediatamente (con Retry-After).
    - Intenta RETRIES veces con backoff/jitter corto antes de devolver 503.
    - En exito, resetea el circuit-breaker.
    """
    # si circuito abierto -> 503 sin intentar
    if _circuit_is_open():
        until = time.time() + (_cb_open_until - _now())
        retry_after = int(round(_cb_open_until - _now()))
        logger.warning("Solicitud rechazada por circuit-breaker abierto durante %d s", retry_after)
        raise HTTPException(status_code=503, detail="Base de datos temporalmente inaccesible", headers={"Retry-After": str(retry_after)})

    delay = INITIAL_DELAY
    last_exc = None

    for intento in range(1, RETRIES + 1):
        db = SessionLocal()
        try:
            # ping rápido y fiable
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            # éxito: resetear circuit-breaker y yield session
            _record_success()
            try:
                yield db
            finally:
                try:
                    db.close()
                except Exception:
                    pass
            return
        except (OperationalError, DBAPIError) as oe:
            last_exc = oe
            try:
                db.close()
            except Exception:
                pass

            logger.warning("Intento %d/%d - OperationalError/DBAPIError al conectar a DB: %s", intento, RETRIES, repr(oe))

            # registrar fallo en circuit-breaker
            _record_failure()

            if intento < RETRIES:
                # backoff exponencial con jitter corto (blocking)
                jitter = random.uniform(0, 0.25 * delay)
                sleep_time = delay + jitter
                logger.info("Durmiendo %.3fs antes del proximo intento", sleep_time)
                time.sleep(sleep_time)
                delay = min(delay * 2, 10.0)  # limitar crecimiento
                continue
            else:
                logger.error("Reintentos DB agotados: %s", repr(last_exc), exc_info=True)
                raise HTTPException(status_code=503, detail="Base de datos temporalmente inaccesible", headers={"Retry-After": str(COOLDOWN_SECONDS)})
        except Exception as e:
            try:
                db.close()
            except Exception:
                pass
            logger.exception("Error inesperado al obtener session DB: %s", e)
            # devolver 500: excepción inesperada
            raise HTTPException(status_code=500, detail="Error interno al obtener DB")

# helper util: para scripts/tests
def ping_db() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
