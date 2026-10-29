"""Run the full signing flow against a running stack (``make up``).

Uploads samples/contract.pdf, has two signers sign it in order, checks the
signatures and the audit chain, and writes the results to ./out/.
"""

import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
BASE_URL = os.environ.get("ESIGN_DEMO_URL", "http://localhost:8000")
API_KEY = os.environ.get("ESIGN_API_KEY", "dev-api-key-change-me")
OUT = ROOT / "out"
SIGNERS = [
    {"name": "Alice Example", "email": "alice@example.com"},
    {"name": "Bob Example", "email": "bob@example.com"},
]


def step(message: str) -> None:
    print(f"==> {message}")


def check(response: httpx.Response) -> Any:
    if response.is_error:
        sys.exit(
            f"{response.request.method} {response.request.url} -> {response.status_code}\n"
            f"{response.text}"
        )
    return response.json() if "json" in response.headers.get("content-type", "") else None


def current_signing_link(client: httpx.Client, envelope_id: str) -> str:
    """Public demo: the demo-only endpoint. Local stack: the simulated outbox."""
    response = client.get(f"/envelopes/{envelope_id}/signing-link")
    if response.status_code != 404:
        link: str = check(response)["signing_link"]
        return link
    outbox = check(client.get(f"/dev/outbox/{envelope_id}"))
    latest: str = outbox[-1]["signing_link"]
    return latest


def wait_until_ready(client: httpx.Client, timeout_seconds: float = 60) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            if client.get("/health/ready").status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(1)
    sys.exit(f"API at {BASE_URL} is not ready; did you run `make up`?")


def main() -> None:
    OUT.mkdir(exist_ok=True)
    with httpx.Client(base_url=BASE_URL, headers={"X-API-Key": API_KEY}, timeout=30) as client:
        wait_until_ready(client)

        step("Uploading samples/contract.pdf")
        pdf = (ROOT / "samples" / "contract.pdf").read_bytes()
        document = check(client.post("/documents", files={"file": ("contract.pdf", pdf)}))
        print(f"    document {document['id']} sha256={document['sha256'][:16]}...")

        step("Creating envelope with two sequential signers")
        envelope = check(
            client.post(
                "/envelopes",
                json={"title": "Demo agreement", "document_id": document["id"], "signers": SIGNERS},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        )
        envelope_id = envelope["id"]
        print(f"    envelope {envelope_id} status={envelope['status']}")

        step("Sending envelope")
        print(f"    status={check(client.post(f'/envelopes/{envelope_id}/send'))['status']}")

        for signer in SIGNERS:
            link = current_signing_link(client, envelope_id)
            step(f"{signer['name']} opens their signing link and signs")
            # Signers authenticate with the link alone, so no API key here.
            view = check(httpx.get(link, timeout=30))
            print(f"    signer #{view['you']['order']} of {len(view['signers'])}")
            result = check(httpx.post(link, timeout=30))
            print(f"    envelope status={result['envelope_status']}")

        step("Verifying signatures")
        verification = check(client.get(f"/envelopes/{envelope_id}/verify"))
        for signature in verification["signatures"]:
            print(
                f"    {signature['field_name']}: {signature['signer_name']} "
                f"intact={signature['intact']} valid={signature['valid']} "
                f"chain_valid={signature['chain_valid']}"
            )

        step("Checking the audit hash chain")
        audit = check(client.get(f"/envelopes/{envelope_id}/audit"))
        print(f"    {len(audit['events'])} events, chain_valid={audit['chain_valid']}")

        step(f"Writing {OUT.relative_to(ROOT)}/signed.pdf and test-ca.pem")
        (OUT / "signed.pdf").write_bytes(
            client.get(f"/envelopes/{envelope_id}/document").raise_for_status().content
        )
        (OUT / "test-ca.pem").write_bytes(
            client.get("/trust/test-ca.pem").raise_for_status().content
        )

    if not (verification["all_signatures_valid"] and audit["chain_valid"]):
        sys.exit("verification FAILED")
    step("Done: both signatures valid, audit chain intact")


if __name__ == "__main__":
    main()
