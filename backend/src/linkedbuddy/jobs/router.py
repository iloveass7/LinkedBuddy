"""Job status for the UI to poll (mounted behind `require_owner`)."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from linkedbuddy.db.engine import get_session
from linkedbuddy.db.models import Job

router = APIRouter(prefix="/jobs", tags=["jobs"])


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    status: str
    attempts: int
    error: str | None
    result: dict[str, Any] | None


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: uuid.UUID, db: Annotated[AsyncSession, Depends(get_session)]) -> JobOut:
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="job not found")
    return JobOut.model_validate(job)
