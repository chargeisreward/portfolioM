"""迁移脚本：为 security_master 添加新列 + 创建 data_pull_task 表。

用法：python migrate_admin_columns.py
"""
import sys
sys.path.insert(0, ".")

from database import engine, Base
from sqlalchemy import text, inspect


def migrate():
    """执行迁移：添加 security_master 新列 + 创建 data_pull_task 表。"""
    inspector = inspect(engine)
    existing_cols = {c["name"] for c in inspector.get_columns("security_master")}
    new_cols = {
        "security_type": "VARCHAR(20)",
        "fund_type": "VARCHAR(20)",
        "market": "VARCHAR(8)",
        "is_drillable": "BOOLEAN DEFAULT 0",
        "index_code": "VARCHAR(20)",
        "index_name": "VARCHAR(80)",
        "benchmark_formula": "VARCHAR(500)",
        "premium_discount": "FLOAT",
        "note": "VARCHAR(200)",
        "updated_by": "INTEGER",
    }

    with engine.connect() as conn:
        for col, col_type in new_cols.items():
            if col not in existing_cols:
                sql = f"ALTER TABLE security_master ADD COLUMN {col} {col_type}"
                print(f"  执行: {sql}")
                conn.execute(text(sql))
        conn.commit()

    # 创建 data_pull_task 表（如果不存在）
    from models import DataPullTask  # noqa: F401
    Base.metadata.create_all(bind=engine, tables=[DataPullTask.__table__])
    print("  data_pull_task 表已创建（如不存在）")
    print("迁移完成")


if __name__ == "__main__":
    migrate()
