"""Settings routes (mounted behind `require_owner`)."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from linkedbuddy.db.engine import get_session
from linkedbuddy.settings import service
from linkedbuddy.settings.service import SettingsIn, SettingsOut

router = APIRouter(prefix="/settings", tags=["settings"])

Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("", response_model=SettingsOut)
async def read_settings(db: Session) -> SettingsOut:
    return await service.get_settings_row(db)


@router.put("", response_model=SettingsOut)
async def put_settings(body: SettingsIn, db: Session) -> SettingsOut:
    return await service.replace_settings(db, body)
