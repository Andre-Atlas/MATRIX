from fastapi import FastAPI

from matrix_server.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="MATRIX")
    app.state.settings = settings or get_settings()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
