from importlib.metadata import PackageNotFoundError, version

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from baseball_backend.routes.auth import router as auth_router
from baseball_backend.routes.games import router as games_router
from baseball_backend.routes.predictions import router as predictions_router
from baseball_backend.settings import get_settings

app = FastAPI(title="Baseball Intelligence API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router)
app.include_router(games_router)
app.include_router(predictions_router)


@app.get("/health")
def health() -> dict[str, object]:
    settings = get_settings()
    ml_version: str | None
    try:
        ml_version = version("baseball-analyze")
    except PackageNotFoundError:
        ml_version = None

    return {
        "status": "ok",
        "database_configured": bool(settings.database_url),
        "ml_package_version": ml_version,
    }


def main() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "baseball_backend.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=True,
    )
