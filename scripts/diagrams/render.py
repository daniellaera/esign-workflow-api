"""Export diagrams with a local Kroki (Docker), then rasterise them for review.

- docs/diagrams/*.excalidraw -> docs/diagrams/*.svg (committed)
- every ```mermaid block in README.md and docs/ARCHITECTURE.md -> PNG (review only)
- every SVG -> PNG with rsvg-convert (review only)

Review PNGs go to out/diagram-check/. The Kroki containers are stopped at the end.
Run: uv run python scripts/diagrams/render.py   (needs Docker and rsvg-convert)
"""

import re
import subprocess
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = Path(__file__).resolve().parent / "kroki-compose.yml"
DIAGRAMS = ROOT / "docs" / "diagrams"
CHECK_DIR = ROOT / "out" / "diagram-check"
MARKDOWN_SOURCES = [ROOT / "README.md", ROOT / "docs" / "ARCHITECTURE.md"]
KROKI = "http://127.0.0.1:18000"
MERMAID_BLOCK = re.compile(r"```mermaid\n(.*?)```", re.DOTALL)


def compose(*args: str) -> None:
    subprocess.run(["docker", "compose", "-f", str(COMPOSE_FILE), *args], check=True)  # noqa: S603, S607


def wait_for_kroki(client: httpx.Client, timeout_seconds: float = 120) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            if client.get("/health").status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(1)
    raise TimeoutError("Kroki did not start")


def render(client: httpx.Client, kind: str, source: str, output_format: str) -> bytes:
    response = client.post(f"/{kind}/{output_format}", content=source.encode())
    response.raise_for_status()
    return response.content


def rasterise(svg: Path) -> Path:
    png = CHECK_DIR / f"{svg.stem}.png"
    subprocess.run(  # noqa: S603
        ["rsvg-convert", "--zoom", "1.5", "--background-color", "white", "-o", str(png), str(svg)],  # noqa: S607
        check=True,
    )
    return png


def main() -> None:
    CHECK_DIR.mkdir(parents=True, exist_ok=True)
    compose("up", "--detach", "--quiet-pull")
    try:
        with httpx.Client(base_url=KROKI, timeout=60) as client:
            wait_for_kroki(client)
            for source in sorted(DIAGRAMS.glob("*.excalidraw")):
                svg = source.with_suffix(".svg")
                svg.write_bytes(render(client, "excalidraw", source.read_text(), "svg"))
                print(f"{svg.relative_to(ROOT)} -> {rasterise(svg).relative_to(ROOT)}")
            for markdown in MARKDOWN_SOURCES:
                blocks = MERMAID_BLOCK.findall(markdown.read_text())
                for number, block in enumerate(blocks, start=1):
                    png = CHECK_DIR / f"{markdown.stem.lower()}-mermaid-{number}.png"
                    png.write_bytes(render(client, "mermaid", block, "png"))
                    print(f"{markdown.relative_to(ROOT)} block {number} -> {png.relative_to(ROOT)}")
    finally:
        compose("down")


if __name__ == "__main__":
    main()
