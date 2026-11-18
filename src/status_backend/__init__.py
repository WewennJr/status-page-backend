def main() -> None:
    import uvicorn

    from status_backend.core.config import get_settings

    settings = get_settings()
    uvicorn.run(
        "status_backend.app:create_app",
        factory=True,
        host="127.0.0.1",
        port=8002,
        reload=settings.environment == "development",
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
    )
