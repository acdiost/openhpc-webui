"""Command-line entrypoint for the development server."""

import uvicorn


def main() -> None:
    uvicorn.run("openhpc_webui.application:app", host="0.0.0.0", port=6827)
