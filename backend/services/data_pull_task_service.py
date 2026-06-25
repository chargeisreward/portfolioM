"""数据拉取任务记录 service — 记录/查询任务执行历史。

依赖：DataPullTask
"""
from __future__ import annotations

import logging
from datetime import datetime
from sqlalchemy.orm import Session

from models import DataPullTask

logger = logging.getLogger(__name__)


def record_task_start(
    db: Session, job_id: str, job_name: str, triggered_by: str
) -> dict:
    """记录任务开始（创建 RUNNING 状态记录）。"""
    task = DataPullTask(
        job_id=job_id,
        job_name=job_name,
        started_at=datetime.utcnow(),
        status="RUNNING",
        triggered_by=triggered_by,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return _to_dict(task)


def record_task_finish(
    db: Session,
    task_id: int,
    status: str,
    records_pulled: int = 0,
    error_message: str | None = None,
) -> dict | None:
    """记录任务结束（更新状态）。"""
    task = db.query(DataPullTask).filter(DataPullTask.id == task_id).first()
    if not task:
        return None
    task.status = status
    task.finished_at = datetime.utcnow()
    task.records_pulled = records_pulled
    task.error_message = error_message
    db.commit()
    db.refresh(task)
    return _to_dict(task)


def list_tasks(
    db: Session,
    status: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    """查询任务历史（分页+筛选）。返回 {items, total, page, page_size}。"""
    q = db.query(DataPullTask)
    if status:
        q = q.filter(DataPullTask.status == status)
    if date_from:
        q = q.filter(DataPullTask.started_at >= date_from)
    if date_to:
        q = q.filter(DataPullTask.started_at <= date_to)
    total = q.count()
    rows = (
        q.order_by(DataPullTask.started_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "items": [_to_dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def _to_dict(task: DataPullTask) -> dict:
    """将 ORM 对象转为 dict。"""
    return {
        "id": task.id,
        "job_id": task.job_id,
        "job_name": task.job_name,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
        "status": task.status,
        "records_pulled": task.records_pulled,
        "error_message": task.error_message,
        "triggered_by": task.triggered_by,
    }
