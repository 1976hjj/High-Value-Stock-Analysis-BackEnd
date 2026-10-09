from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import data_sync

router = APIRouter(prefix='/api/data-sync', tags=['data-sync'])


class SyncRequest(BaseModel):
    industry_ids: list[str] = Field(min_length=1)
    dataset_ids: list[str] = Field(min_length=1)
    target_date: date | None = None
    resume: bool = False


@router.get('/status')
def status():
    return data_sync.inventory()


@router.post('/start', status_code=202)
def start(payload: SyncRequest):
    try:
        return data_sync.start_sync(payload.industry_ids, payload.dataset_ids, payload.target_date, payload.resume)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post('/pause')
def pause():
    return {'job': data_sync.pause_sync()}
