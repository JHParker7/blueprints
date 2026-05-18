from typing import Annotated
from contextlib import asynccontextmanager
import json
import logging
import os
from queue import Queue

import asyncpg
import requests
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordBearer, HTTPBasic, HTTPBasicCredentials
import logging_loki
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_fastapi_instrumentator import Instrumentator
import uvicorn

GATEKEEPER_URL = os.getenv("GATEKEEPER_URL", "http://localhost:8080")
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://postgres:test@127.0.0.1:5432/blueprints"
)
TEMPO_ENDPOINT = os.getenv("TEMPO_ENDPOINT", "http://localhost:4318")
LOKI_URL = os.getenv("LOKI_URL", "http://localhost:3100")
TELEMETRY_ENABLED = os.getenv("TELEMETRY_ENABLED", "true").lower() == "true"


# ── Tracing ──────────────────────────────────────────────────────────────────


def _setup_tracing() -> None:
    resource = Resource.create({"service.name": "blueprints"})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=f"{TEMPO_ENDPOINT}/v1/traces")
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    RequestsInstrumentor().instrument()


if TELEMETRY_ENABLED:
    _setup_tracing()


# ── Logging ───────────────────────────────────────────────────────────────────


class _TraceContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        ctx = trace.get_current_span().get_span_context()
        record.trace_id = format(ctx.trace_id, "032x") if ctx.is_valid else ""
        record.span_id = format(ctx.span_id, "016x") if ctx.is_valid else ""
        return True


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        super().format(record)
        return json.dumps({
            "time": self.formatTime(record),
            "level": record.levelname,
            "msg": record.getMessage(),
            "trace_id": getattr(record, "trace_id", ""),
            "span_id": getattr(record, "span_id", ""),
        })


def _setup_logging() -> logging.Logger:
    fmt = _JsonFormatter()

    stream_handler = logging.StreamHandler()
    stream_handler.addFilter(_TraceContextFilter())
    stream_handler.setFormatter(fmt)

    logger = logging.getLogger("blueprints")
    logger.setLevel(logging.INFO)
    logger.addHandler(stream_handler)

    if TELEMETRY_ENABLED:
        loki_handler = logging_loki.LokiQueueHandler(
            Queue(-1),
            url=f"{LOKI_URL}/loki/api/v1/push",
            tags={"service": "blueprints"},
            version="1",
        )
        loki_handler.addFilter(_TraceContextFilter())
        loki_handler.setFormatter(fmt)
        logger.addHandler(loki_handler)

        uvicorn_access_logger = logging.getLogger("uvicorn.access")
        uvicorn_access_logger.addHandler(loki_handler)

    return logger


logger = _setup_logging()


# ── Database ──────────────────────────────────────────────────────────────────

_CREATE_TABLES = """
    CREATE TABLE IF NOT EXISTS states (
        workspace  TEXT PRIMARY KEY,
        data       BYTEA        NOT NULL,
        updated_at TIMESTAMPTZ  DEFAULT now()
    );
    CREATE TABLE IF NOT EXISTS locks (
        workspace  TEXT PRIMARY KEY,
        lock_data  TEXT         NOT NULL,
        created_at TIMESTAMPTZ  DEFAULT now(),
        updated_at TIMESTAMPTZ  DEFAULT now()
    );
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = await asyncpg.create_pool(DATABASE_URL)
    async with pool.acquire() as conn:
        await conn.execute(_CREATE_TABLES)
    app.state.db = pool
    logger.info("database pool initialized")
    yield
    await pool.close()
    logger.info("database pool closed")


async def get_db(request: Request) -> asyncpg.Pool:
    return request.app.state.db


# ── App ───────────────────────────────────────────────────────────────────────

basic_scheme = HTTPBasic(auto_error=False)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)

app = FastAPI(lifespan=lifespan)

FastAPIInstrumentor.instrument_app(app)
Instrumentator().instrument(app).expose(app)  # /metrics


# ── Auth ──────────────────────────────────────────────────────────────────────


def authenticate_user(email: str, password: str) -> str:
    res = requests.post(
        f"{GATEKEEPER_URL}/login", json={"email": email, "password": password}
    )
    return res.json()["token"]


async def get_current_token(
    bearer: Annotated[str | None, Depends(oauth2_scheme)],
    basic: Annotated[HTTPBasicCredentials | None, Depends(basic_scheme)],
) -> str:
    if bearer:
        return bearer
    if basic:
        return authenticate_user(basic.username, basic.password)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Basic"},
    )


async def check_permissions(token: str, resource: str, action: str) -> bool:
    res = requests.get(
        f"{GATEKEEPER_URL}/check_permissions",
        json={"service": "blueprints", "resource": resource, "action": action},
        headers={"Authorization": f"Bearer {token}"},
    )
    if res.status_code != 200:
        logger.error(
            "gatekeeper error status=%s reason=%s", res.status_code, res.reason
        )
        return False
    authorized = res.json()["authorized"]
    if not authorized:
        logger.warning("permission denied resource=%s action=%s", resource, action)
    return authorized


# ── Endpoints ─────────────────────────────────────────────────────────────────


@app.get("/state/{workspace}")
async def tf_get_state(
    workspace: str,
    token: Annotated[str, Depends(get_current_token)],
    db: Annotated[asyncpg.Pool, Depends(get_db)],
):
    if not await check_permissions(token, f"blueprints/states/{workspace}", "getState"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    row = await db.fetchrow("SELECT data FROM states WHERE workspace = $1", workspace)
    if row is None:
        logger.info("state not found workspace=%s", workspace)
        return Response(status_code=204)
    logger.info("state retrieved workspace=%s", workspace)
    return Response(content=bytes(row["data"]), media_type="application/json")


@app.post("/state/{workspace}")
async def tf_update_state(
    workspace: str,
    request: Request,
    token: Annotated[str, Depends(get_current_token)],
    db: Annotated[asyncpg.Pool, Depends(get_db)],
    ID: str | None = None,
):
    if not await check_permissions(
        token, f"blueprints/states/{workspace}", "updateState"
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    body = await request.body()
    async with db.acquire() as conn:
        async with conn.transaction():
            lock_row = await conn.fetchrow(
                "SELECT lock_data FROM locks WHERE workspace = $1 FOR UPDATE", workspace
            )
            if ID and lock_row:
                lock_data = json.loads(lock_row["lock_data"])
                if lock_data.get("ID") != ID:
                    logger.warning(
                        "state update rejected: lock id mismatch workspace=%s",
                        workspace,
                    )
                    return Response(
                        content=lock_row["lock_data"],
                        status_code=409,
                        media_type="application/json",
                    )
            await conn.execute(
                """INSERT INTO states (workspace, data) VALUES ($1, $2)
                   ON CONFLICT (workspace) DO UPDATE SET data = $2, updated_at = now()""",
                workspace,
                body,
            )
    logger.info("state updated workspace=%s", workspace)
    return Response(status_code=200)


@app.delete("/state/{workspace}")
async def tf_delete_state(
    workspace: str,
    token: Annotated[str, Depends(get_current_token)],
    db: Annotated[asyncpg.Pool, Depends(get_db)],
):
    if not await check_permissions(
        token, f"blueprints/states/{workspace}", "deleteState"
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    await db.execute("DELETE FROM states WHERE workspace = $1", workspace)
    logger.info("state deleted workspace=%s", workspace)
    return Response(status_code=200)


@app.api_route("/state/{workspace}", methods=["LOCK"])
async def tf_lock_state(
    workspace: str,
    request: Request,
    token: Annotated[str, Depends(get_current_token)],
    db: Annotated[asyncpg.Pool, Depends(get_db)],
):
    if not await check_permissions(
        token, f"blueprints/states/{workspace}", "lockState"
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    body = await request.body()
    async with db.acquire() as conn:
        async with conn.transaction():
            existing = await conn.fetchrow(
                "SELECT lock_data FROM locks WHERE workspace = $1 FOR UPDATE", workspace
            )
            if existing:
                logger.warning("lock conflict workspace=%s", workspace)
                return Response(
                    content=existing["lock_data"],
                    status_code=423,
                    media_type="application/json",
                )
            await conn.execute(
                "INSERT INTO locks (workspace, lock_data) VALUES ($1, $2)",
                workspace,
                body.decode(),
            )
    logger.info("state locked workspace=%s", workspace)
    return Response(status_code=200)


@app.api_route("/state/{workspace}", methods=["UNLOCK"])
async def tf_unlock_state(
    workspace: str,
    request: Request,
    token: Annotated[str, Depends(get_current_token)],
    db: Annotated[asyncpg.Pool, Depends(get_db)],
):
    if not await check_permissions(
        token, f"blueprints/states/{workspace}", "unlockState"
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    body = await request.body()
    async with db.acquire() as conn:
        async with conn.transaction():
            existing = await conn.fetchrow(
                "SELECT lock_data FROM locks WHERE workspace = $1 FOR UPDATE", workspace
            )
            if existing:
                if body:
                    request_data = json.loads(body)
                    lock_data = json.loads(existing["lock_data"])
                    if request_data.get("ID") and lock_data.get(
                        "ID"
                    ) != request_data.get("ID"):
                        logger.warning(
                            "unlock rejected: lock id mismatch workspace=%s", workspace
                        )
                        raise HTTPException(status_code=409, detail="Lock ID mismatch")
                await conn.execute("DELETE FROM locks WHERE workspace = $1", workspace)
    logger.info("state unlocked workspace=%s", workspace)
    return Response(status_code=200)


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8081,
        ssl_certfile=os.getenv("TLS_CERT_FILE"),
        ssl_keyfile=os.getenv("TLS_KEY_FILE"),
        ssl_ca_certs=os.getenv("CA_CERT_FILE"),
    )
