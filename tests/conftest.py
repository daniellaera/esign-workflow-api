import time
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from pymongo import MongoClient
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

from esign.adapters.mongo.database import MongoDatabase, create_client, ensure_indexes

SAMPLE_PDF = Path(__file__).resolve().parent.parent / "samples" / "contract.pdf"
# Same version as docker-compose.yml. MongoDB 8.x refuses to start on Linux
# kernels >= 6.19 (SERVER-121912), which GitHub's runners use; see ADR 0002.
MONGO_IMAGE = "mongo:7.0"


@pytest.fixture
def sample_pdf() -> bytes:
    return SAMPLE_PDF.read_bytes()


@pytest.fixture(scope="session")
def mongo_uri() -> Iterator[str]:
    """One MongoDB container per test session, as a single-node replica set
    (transactions need a replica set). Started only if a test asks for it."""
    container = (
        DockerContainer(MONGO_IMAGE)
        .with_command("--replSet rs0 --bind_ip_all")
        .with_exposed_ports(27017)
        .waiting_for(LogMessageWaitStrategy("Waiting for connections"))
    )
    try:
        container.start()
    except Exception as exc:
        # testcontainers' error does not include mongod's own output; attach it.
        raise RuntimeError(f"MongoDB failed to start:\n{_log_tail(container)}") from exc
    try:
        container.exec(["mongosh", "--quiet", "--eval", "rs.initiate()"])
        uri = (
            f"mongodb://{container.get_container_host_ip()}:"
            f"{container.get_exposed_port(27017)}/?directConnection=true"
        )
        _wait_for_primary(uri)
        yield uri
    finally:
        container.stop()


def _log_tail(container: DockerContainer, max_chars: int = 4000) -> str:
    try:
        stdout, stderr = container.get_logs()
    except Exception as exc:
        return f"(no logs: {exc})"
    finally:
        container.stop()
    return (stdout + stderr).decode(errors="replace")[-max_chars:]


def _wait_for_primary(uri: str, timeout_seconds: float = 30) -> None:
    deadline = time.monotonic() + timeout_seconds
    client: MongoClient[dict[str, Any]] = MongoClient(uri)
    with client:
        while not client.admin.command("hello").get("isWritablePrimary"):
            if time.monotonic() > deadline:
                raise TimeoutError("MongoDB replica set did not elect a primary")
            time.sleep(0.2)


@pytest.fixture
def mongo_database_name() -> str:
    # A fresh database per test keeps tests independent without cleanup logic.
    return f"test_{uuid.uuid4().hex}"


@pytest.fixture
async def database(mongo_uri: str, mongo_database_name: str) -> AsyncIterator[MongoDatabase]:
    client = create_client(mongo_uri)
    db = client[mongo_database_name]
    await ensure_indexes(db)
    try:
        yield MongoDatabase(client, db)
    finally:
        await client.drop_database(mongo_database_name)
        await client.close()
