"""Consultas SQLAlchemy para batches e items mensuales."""
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models import BHEBatch, BHEBatchItem


def create_batch(db: Session, values: dict) -> BHEBatch:
    batch = BHEBatch(**values)
    db.add(batch)
    db.flush()
    db.refresh(batch)
    return batch


def get_batch(db: Session, batch_id: int) -> BHEBatch | None:
    return db.scalar(
        select(BHEBatch)
        .options(selectinload(BHEBatch.items))
        .where(BHEBatch.id == batch_id)
    )


def list_batches(db: Session) -> list[BHEBatch]:
    statement = (
        select(BHEBatch)
        .options(selectinload(BHEBatch.items))
        .order_by(BHEBatch.created_at.desc(), BHEBatch.id.desc())
    )
    return list(db.scalars(statement).all())


def get_item(db: Session, item_id: int) -> BHEBatchItem | None:
    return db.get(BHEBatchItem, item_id)


def get_item_by_client(db: Session, batch_id: int, client_id: int) -> BHEBatchItem | None:
    return db.scalar(
        select(BHEBatchItem).where(
            BHEBatchItem.batch_id == batch_id,
            BHEBatchItem.client_id == client_id,
        )
    )


def create_item(db: Session, values: dict) -> BHEBatchItem:
    item = BHEBatchItem(**values)
    db.add(item)
    db.flush()
    db.refresh(item)
    return item


def list_draft_items(db: Session, batch_id: int) -> list[BHEBatchItem]:
    statement = select(BHEBatchItem).where(
        BHEBatchItem.batch_id == batch_id,
        BHEBatchItem.status == "draft",
    )
    return list(db.scalars(statement).all())
