"""Development-only view of the simulated mailbox. Enabled by ESIGN_EXPOSE_OUTBOX.

In production, links would go to a mail provider and never be readable via the API.
"""

from fastapi import APIRouter, Depends

from esign.adapters.http.dependencies import ContainerDep
from esign.adapters.http.schemas import OutboxMessageOut
from esign.adapters.http.security import require_api_key

router = APIRouter(prefix="/dev", tags=["development"], dependencies=[Depends(require_api_key)])


@router.get("/outbox/{envelope_id}")
async def outbox(envelope_id: str, container: ContainerDep) -> list[OutboxMessageOut]:
    messages = await container.envelopes.outbox(envelope_id)
    return [OutboxMessageOut.from_message(m) for m in messages]
