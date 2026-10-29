"""Write a small, valid one-page PDF to samples/contract.pdf.

The file is hand-assembled so the repository has no PDF-generation dependency;
the only subtle part is the cross-reference table, which must list each
object's byte offset.
"""

from pathlib import Path

LINES = [
    "SAMPLE AGREEMENT - esign-workflow-api demo document",
    "",
    "This document exists only to demonstrate sequential electronic signing.",
    "It has no legal meaning. Signatures are issued by a TEST CA.",
]


def build_pdf(lines: list[str]) -> bytes:
    text_ops = ["BT", "/F1 12 Tf", "72 760 Td", "16 TL"]
    for line in lines:
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        text_ops.append(f"({escaped}) Tj T*")
    text_ops.append("ET")
    content = "\n".join(text_ops).encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_offset = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode()
    out += f"startxref\n{xref_offset}\n%%EOF\n".encode()
    return bytes(out)


if __name__ == "__main__":
    target = Path(__file__).resolve().parent.parent / "samples" / "contract.pdf"
    target.write_bytes(build_pdf(LINES))
    print(f"wrote {target}")
