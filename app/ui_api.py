from __future__ import annotations

from fastapi.responses import FileResponse

from app.api import ROOT, app


@app.get("/ui/dashboard.css", include_in_schema=False)
def dashboard_styles() -> FileResponse:
    return FileResponse(
        ROOT / "dashboard" / "dashboard.css",
        media_type="text/css",
        headers={"Cache-Control": "public, max-age=300"},
    )


@app.get("/ui/dashboard.js", include_in_schema=False)
def dashboard_script() -> FileResponse:
    return FileResponse(
        ROOT / "dashboard" / "dashboard.js",
        media_type="application/javascript",
        headers={"Cache-Control": "public, max-age=300"},
    )
