"""Generate the Excalidraw diagrams in docs/diagrams/.

Every label is bound to its shape (``containerId``) so Excalidraw centres it.
Arrow styles carry one meaning each; the legend in each diagram says which.
Run: uv run python scripts/diagrams/generate_excalidraw.py
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

OUT_DIR = Path(__file__).resolve().parents[2] / "docs" / "diagrams"

HELVETICA = 2
LINE_HEIGHT = 1.25
INK = "#1e1e1e"
MUTED = "#495057"

# Soft fills, one per kind of thing.
BLUE = "#d0ebff"  # actors / external callers
GREEN = "#d3f9d8"  # application core
YELLOW = "#fff3bf"  # ports
VIOLET = "#e5dbff"  # adapters
GRAY = "#f1f3f5"  # infrastructure / containers
ORANGE = "#ffe8cc"  # outcomes
RED = "#ffe3e3"  # alternative paths
WHITE = "#ffffff"


@dataclass
class Diagram:
    elements: list[dict[str, Any]] = field(default_factory=list)
    _counter: int = 0

    def _id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}-{self._counter}"

    def _base(self, kind: str, x: float, y: float, w: float, h: float) -> dict[str, Any]:
        return {
            "id": self._id(kind),
            "type": kind,
            "x": x,
            "y": y,
            "width": w,
            "height": h,
            "angle": 0,
            "strokeColor": INK,
            "backgroundColor": "transparent",
            "fillStyle": "solid",
            "strokeWidth": 1,
            "strokeStyle": "solid",
            "roughness": 0,
            "opacity": 100,
            "groupIds": [],
            "frameId": None,
            "roundness": None,
            "seed": self._counter,
            "version": 1,
            "versionNonce": self._counter,
            "isDeleted": False,
            "boundElements": [],
            "updated": 1,
            "link": None,
            "locked": False,
        }

    def _label(
        self,
        container: dict[str, Any],
        text: str,
        *,
        size: int,
        valign: str = "middle",
        color: str = INK,
        center: tuple[float, float] | None = None,
    ) -> None:
        lines = text.split("\n")
        height = len(lines) * size * LINE_HEIGHT
        # Rough Helvetica width; Excalidraw re-measures, the centre is what matters.
        width = max(len(line) for line in lines) * size * 0.55
        if center is None:
            cx = container["x"] + container["width"] / 2
            cy = container["y"] + container["height"] / 2
        else:
            cx, cy = center
        y = container["y"] + 12 if valign == "top" else cy - height / 2
        label = self._base("text", cx - width / 2, y, width, height)
        label.update(
            {
                "strokeColor": color,
                "text": text,
                "originalText": text,
                "fontSize": size,
                "fontFamily": HELVETICA,
                "textAlign": "center",
                "verticalAlign": valign,
                "containerId": container["id"],
                "lineHeight": LINE_HEIGHT,
                "autoResize": True,
            }
        )
        container["boundElements"].append({"type": "text", "id": label["id"]})
        self.elements.append(label)

    def box(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        text: str,
        *,
        fill: str = WHITE,
        size: int = 16,
        valign: str = "middle",
        dashed: bool = False,
    ) -> dict[str, Any]:
        rect = self._base("rectangle", x, y, w, h)
        rect.update(
            {
                "backgroundColor": fill,
                "roundness": {"type": 3},
                "strokeStyle": "dashed" if dashed else "solid",
            }
        )
        self.elements.append(rect)
        self._label(rect, text, size=size, valign=valign)
        return rect

    def arrow(
        self,
        start: dict[str, Any],
        end: dict[str, Any],
        *,
        label: str | None = None,
        dashed: bool = False,
        side: str = "right",
    ) -> dict[str, Any]:
        """Straight arrow leaving ``start`` on ``side`` (right, left, top, bottom)."""
        gap = 4
        if side in ("right", "left"):
            y1 = y2 = _overlap_mid(start, end, "y")
            if side == "right":
                x1, x2 = start["x"] + start["width"] + gap, end["x"] - gap
            else:
                x1, x2 = start["x"] - gap, end["x"] + end["width"] + gap
        else:
            x1 = x2 = _overlap_mid(start, end, "x")
            if side == "bottom":
                y1, y2 = start["y"] + start["height"] + gap, end["y"] - gap
            else:
                y1, y2 = start["y"] - gap, end["y"] + end["height"] + gap
        line = self._base("arrow", x1, y1, x2 - x1, y2 - y1)
        line.update(
            {
                "points": [[0, 0], [x2 - x1, y2 - y1]],
                "strokeStyle": "dashed" if dashed else "solid",
                "strokeColor": MUTED,
                "strokeWidth": 2,
                "roundness": {"type": 2},
                "startBinding": {"elementId": start["id"], "focus": 0, "gap": gap},
                "endBinding": {"elementId": end["id"], "focus": 0, "gap": gap},
                "startArrowhead": None,
                "endArrowhead": "arrow",
                "lastCommittedPoint": None,
                "elbowed": False,
            }
        )
        start["boundElements"].append({"type": "arrow", "id": line["id"]})
        end["boundElements"].append({"type": "arrow", "id": line["id"]})
        self.elements.append(line)
        if label:
            self._label(line, label, size=13, color=MUTED, center=((x1 + x2) / 2, (y1 + y2) / 2))
        return line

    def legend(self, x: float, y: float, entries: list[tuple[str, bool]]) -> None:
        """A small key: one sample arrow per meaning."""
        for index, (meaning, dashed) in enumerate(entries):
            row_y = y + index * 28
            start = self._invisible(x, row_y)
            end = self._invisible(x + 60, row_y)
            self.arrow(start, end, dashed=dashed)
            note = self._base("text", x + 72, row_y - 9, len(meaning) * 7.5, 18)
            note.update(
                {
                    "strokeColor": MUTED,
                    "text": meaning,
                    "originalText": meaning,
                    "fontSize": 14,
                    "fontFamily": HELVETICA,
                    "textAlign": "left",
                    "verticalAlign": "top",
                    "containerId": None,
                    "lineHeight": LINE_HEIGHT,
                    "autoResize": True,
                }
            )
            self.elements.append(note)

    def _invisible(self, x: float, y: float) -> dict[str, Any]:
        anchor = self._base("rectangle", x, y - 1, 0.1, 2)
        anchor.update({"strokeColor": "transparent", "opacity": 0})
        self.elements.append(anchor)
        return anchor

    def save(self, name: str) -> Path:
        document = {
            "type": "excalidraw",
            "version": 2,
            "source": "esign-workflow-api/scripts/diagrams/generate_excalidraw.py",
            "elements": self.elements,
            "appState": {"viewBackgroundColor": "#ffffff", "gridSize": None},
            "files": {},
        }
        path = OUT_DIR / f"{name}.excalidraw"
        path.write_text(json.dumps(document, indent=2) + "\n")
        return path


def _overlap_mid(a: dict[str, Any], b: dict[str, Any], axis: str) -> float:
    """Midpoint of the range two shapes share on ``axis``, so arrows run straight."""
    size = "height" if axis == "y" else "width"
    low = max(a[axis], b[axis])
    high = min(a[axis] + a[size], b[axis] + b[size])
    return float((low + high) / 2)


def architecture() -> Diagram:
    d = Diagram()
    row_h, row_gap, top = 64, 26, 110
    rows = [top + i * (row_h + row_gap) for i in range(4)]

    # Callers
    sender = d.box(0, rows[0], 170, row_h, "Sender\nX-API-Key", fill=BLUE)
    signer = d.box(0, rows[2], 170, row_h, "Signer\none-time link", fill=BLUE)

    # Driving adapter
    http = d.box(
        240,
        rows[0],
        200,
        rows[3] + row_h - rows[0],
        "\n".join(
            [
                "HTTP adapter",
                "",
                "FastAPI routes",
                "Pydantic schemas",
                "RFC 9457 errors",
                "API key",
                "request-id logs",
            ]
        ),
        fill=VIOLET,
    )

    # Application core (container with a top label)
    d.box(500, 40, 640, 470, "Application core", fill=GRAY, size=20, valign="top")
    use_cases = d.box(
        525,
        rows[0],
        300,
        row_h * 2 + row_gap,
        "Use cases\n\nDocumentService\nEnvelopeService\nSigningService\nWebhookDispatcher",
        fill=GREEN,
    )
    domain = d.box(
        525,
        rows[2],
        300,
        row_h * 2 + row_gap,
        "\n".join(
            [
                "Domain (pure Python)",
                "",
                "Envelope state machine",
                "Audit hash chain",
                "Signing tokens · Webhook HMAC",
            ]
        ),
        fill=GREEN,
    )
    ports_box = d.box(870, 85, 245, 400, "Ports (Protocols)", fill=WHITE, size=16, valign="top")
    port_names = ["Database\n(unit of work)", "BlobStorage", "SignatureService", "WebhookSender"]
    ports = [
        d.box(895, y + 25, 195, row_h - 10, name, fill=YELLOW, size=15)
        for y, name in zip(rows, port_names, strict=True)
    ]

    # Driven adapters and what they talk to
    adapter_names = [
        "MongoDB repositories\ntransactions",
        "Local blob storage\ncontent-addressed",
        "pyHanko signer\nPAdES + TEST CA",
        "httpx sender\nHMAC, retries",
    ]
    external_names = [
        "MongoDB\nreplica set",
        "Volume\n/data/documents",
        "Volume\n/data/ca",
        "Webhook\nreceiver",
    ]
    for port, row_y, adapter_name, external_name in zip(
        ports, rows, adapter_names, external_names, strict=True
    ):
        adapter = d.box(1200, row_y + 20, 200, row_h, adapter_name, fill=VIOLET, size=15)
        external = d.box(1470, row_y + 20, 160, row_h, external_name, fill=GRAY, size=15)
        d.arrow(adapter, port, dashed=True, side="left")
        d.arrow(adapter, external)

    d.arrow(sender, http)
    d.arrow(signer, http)
    d.arrow(http, use_cases)
    d.arrow(use_cases, domain, side="bottom")
    d.arrow(use_cases, ports_box)

    d.legend(500, 560, [("calls / uses", False), ("implements (adapter → port)", True)])
    return d


def signing_flow() -> Diagram:
    d = Diagram()
    w, h, gap = 230, 104, 70
    col = [i * (w + gap) for i in range(4)]
    top_row, bottom_row = 0, h + 90

    upload = d.box(
        col[0], top_row, w, h, "1. Upload PDF\n\n%PDF- check, 10 MB max\nSHA-256 stored", fill=BLUE
    )
    create = d.box(
        col[1], top_row, w, h, "2. Create envelope\n\nsigners in order\nDRAFT", fill=BLUE
    )
    send = d.box(col[2], top_row, w, h, "3. Send\n\nlink to signer 1 only\nSENT", fill=BLUE)
    first = d.box(
        col[3],
        top_row,
        w,
        h,
        "4. Signer 1 signs\n\nPAdES + visible stamp\nPARTIALLY_SIGNED",
        fill=GREEN,
    )
    second = d.box(
        col[3],
        bottom_row,
        w,
        h,
        "5. Signer 2 signs\n\nPAdES + visible stamp\nCOMPLETED",
        fill=GREEN,
    )
    webhook = d.box(
        col[2],
        bottom_row,
        w,
        h,
        "6. Webhook\n\nenvelope.completed\nHMAC-signed, 3 retries",
        fill=ORANGE,
    )
    verify = d.box(
        col[1],
        bottom_row,
        w,
        h,
        "7. Verify\n\nboth signatures valid\naudit hash chain valid",
        fill=ORANGE,
    )

    d.arrow(upload, create)
    d.arrow(create, send)
    d.arrow(send, first)
    d.arrow(first, second, side="bottom", label="link 2")
    d.arrow(second, webhook, side="left")
    d.arrow(webhook, verify, side="left")

    endings = d.box(
        col[2],
        -150,
        col[3] + w - col[2],
        76,
        "Signer declines → DECLINED (webhook)\nLink used after 72 h → EXPIRED",
        fill=RED,
        dashed=True,
    )
    d.arrow(send, endings, side="top", dashed=True)
    d.arrow(first, endings, side="top", dashed=True)

    d.legend(col[0], bottom_row + 30, [("next step", False), ("alternative ending", True)])
    return d


if __name__ == "__main__":
    for name, build in (("architecture", architecture), ("signing-flow", signing_flow)):
        print(f"wrote {build().save(name)}")
