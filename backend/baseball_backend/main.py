from importlib.metadata import PackageNotFoundError, version

from fastapi import FastAPI

from baseball_backend.settings import get_settings

app = FastAPI(title="Baseball Intelligence API", version="0.1.0")


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
