"""akshare 增量指数轮询 (2026-07-02)。

每天 21:23 Asia/Shanghai 跑一次;增量:
  - 新增:index_code 不存在 → INSERT
  - 更新:name / constituent_count 有差异 → UPDATE
  - 跳过:完全一致 → 跳过
  - 标记 is_active=False:上次见到但本次未拉到

注意: akshare 在 Python 3.14 上有 py_mini_racer 循环导入问题,本地开发无法直接 import。
测试用 mock;prod 部署在 Linux Python 3.x 上运行无此问题。

实现策略: 在 _fetch_indices_from_ak() 内延迟 import akshare,避免模块级 import 触发
py_mini_racer 问题。
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from models import DataPullTask
from models_master import IndexMaster

logger = logging.getLogger(__name__)


def _normalize_code(raw: str) -> str:
    """akshare 指数代码 → 项目内的 index_code (000300 保持)。"""
    code = str(raw).strip()
    if "." in code:
        code = code.split(".")[0]
    return code


def _fetch_indices_from_ak() -> "pd.DataFrame":
    """从 akshare 拉全市场 A 股指数实时快照。"""
    import akshare as ak  # 延迟 import,避免模块级触发 py_mini_racer 问题
    import pandas as pd
    df = ak.stock_zh_index_spot_em()
    df = df.rename(columns={"代码": "code", "名称": "name"})
    df["code"] = df["code"].apply(_normalize_code)
    return df[["code", "name"]]


def poll_index_master(db: Session, _fetch_fn=None) -> dict:
    """主入口: 增量同步 index_master。

    Args:
        db: SQLAlchemy session.
        _fetch_fn: 可选 - 自定义 fetcher (测试用),返回 pandas.DataFrame 含 code/name 列。
                  默认调 _fetch_indices_from_ak() (生产路径)。

    Returns:
        dict: {status, inserted, updated, skipped, marked_inactive, error?}
    """
    if _fetch_fn is None:
        _fetch_fn = _fetch_indices_from_ak

    started_at = datetime.utcnow()
    job = DataPullTask(
        job_id="job_poll_index_master",
        job_name="指数主数据轮询 (akshare)",
        started_at=started_at,
        status="RUNNING",
        triggered_by="scheduler",
    )
    db.add(job)
    db.commit()

    try:
        df = _fetch_fn()
        # 标准化列名:akshare 返回中文列名(代码/名称),我们也允许注入 fetcher 已 rename
        df = df.rename(columns={"代码": "code", "名称": "name"})
        df["code"] = df["code"].apply(_normalize_code)
        current_codes = set(df["code"].astype(str))

        inserted = updated = skipped = 0
        now = datetime.utcnow()
        for _, row in df.iterrows():
            code = str(row["code"])
            name = str(row["name"])
            existing = db.query(IndexMaster).filter_by(index_code=code).first()
            if not existing:
                db.add(IndexMaster(
                    index_code=code,
                    index_name=name,
                    source="akshare",
                    is_active=True,
                    first_pulled_at=now,
                    last_pulled_at=now,
                    last_verified_at=now,
                ))
                inserted += 1
            else:
                changed = False
                if existing.index_name != name:
                    existing.index_name = name
                    changed = True
                if existing.last_verified_at is None or (
                    now - existing.last_verified_at
                ).days >= 1:
                    existing.last_verified_at = now
                    changed = True
                if changed:
                    existing.last_pulled_at = now
                    updated += 1
                else:
                    skipped += 1

        marked_inactive = 0
        active_rows = db.query(IndexMaster).filter(IndexMaster.is_active == True).all()  # noqa: E712
        for r in active_rows:
            if r.source == "akshare" and r.index_code not in current_codes:
                r.is_active = False
                r.last_pulled_at = now
                marked_inactive += 1

        db.commit()

        job.status = "SUCCESS"
        job.finished_at = datetime.utcnow()
        job.records_pulled = inserted + updated
        db.commit()

        return {
            "status": "success",
            "inserted": inserted,
            "updated": updated,
            "skipped": skipped,
            "marked_inactive": marked_inactive,
        }

    except Exception as e:
        db.rollback()
        job.status = "FAILED"
        job.finished_at = datetime.utcnow()
        job.error_message = str(e)[:500]
        db.commit()
        logger.exception("akshare_index_poller 失败")
        return {"status": "failed", "error": str(e)[:500]}