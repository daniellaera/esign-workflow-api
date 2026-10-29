# 0001. Lightweight ports and adapters

**Status:** accepted

## Context
The service mixes business rules (signing order, state machine, audit chain) with heavy infrastructure (MongoDB, pyHanko, HTTP). The rules should be testable without that infrastructure, but a POC should not carry a full hexagonal framework.

## Decision
Three layers inside one package:

- `esign.domain`: dataclasses and pure functions. Must not import FastAPI, PyMongo or pyHanko.
- `esign.application`: use-case services plus `ports.py`, a handful of `typing.Protocol` interfaces.
- `esign.adapters`: HTTP (FastAPI), MongoDB, local storage, pyHanko signer, httpx webhook sender.

`container.py` is the only module that wires concrete classes together. No DI framework; the container is a dataclass built in the FastAPI lifespan.

Protocols instead of abstract base classes: adapters satisfy them structurally, and test fakes need no inheritance.

## Consequences
- Domain tests run in milliseconds without Docker.
- Some mapping code is duplicated (domain dataclass ↔ BSON ↔ Pydantic schema). That is accepted: each boundary is explicit and internal fields (token hashes, versions) can't leak into the API by accident.
- Signing and verification share one port (`SignatureService`) because they share the CA and the PDF library; splitting them would add an interface without a second implementation.
