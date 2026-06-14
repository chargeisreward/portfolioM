#!/bin/bash
# PortfolioM — 启动脚本
# 用法: bash start.sh

set -e
DIR="$(cd "$(dirname "$0")" && pwd)"

echo "╔════════════════════════════════════╗"
echo "║     PortfolioM 启动                ║"
echo "╚════════════════════════════════════╝"

# Configuration
export DB_PATH="${DB_PATH:-$DIR/portfolio.db}"
PORT=${PORT:-8008}

cd "$DIR/backend"

# Step 1: 安装依赖
echo ""
echo "📦 安装 Python 依赖..."
pip install -q -r requirements.txt 2>/dev/null || true

# Step 2: 初始化数据库
echo ""
echo "🗄️  初始化数据库..."
python -c "from database import init_db; init_db(); print('DB ready')"

# Step 3: 导入持仓
echo ""
echo "📥 导入持仓数据..."
python -c "
from services.importer import import_excel
from database import SessionLocal
count = import_excel('$DIR/_2026-06-04.xlsx', SessionLocal())
print(f'Imported {count} holdings')
"

# Step 4: ETF映射
echo ""
echo "🏷️  映射ETF跟踪指数..."
python -c "
from crawlers.etf_index import crawl_fund_index_map
from database import SessionLocal
n = crawl_fund_index_map(SessionLocal())
print(f'Mapped {n} funds')
"

# Step 5: 种子数据
echo ""
echo "🌱  加载种子成分股数据..."
python seed_csv.py
python seed_fin.py

# Step 6: 穿透计算
echo ""
echo "🔍 执行穿透计算..."
python -c "
from services.penetration import PenetrationEngine
from database import SessionLocal
db = SessionLocal()
engine = PenetrationEngine(db)
r = engine.calculate()
print(f'穿透完成: {len(r)} 只底层股票')
from services.csi300 import Csi300Analyzer
Csi300Analyzer(db).recalc_baselines()
print('沪深300基准计算完成')
db.close()
"

# Step 7: 启动服务
echo ""
echo "🚀 启动后端 (port $PORT)..."
nohup python -m uvicorn main:app --host 0.0.0.0 --port $PORT > /tmp/portm-backend.log 2>&1 &
BACKEND_PID=$!
echo "   PID: $BACKEND_PID"

echo ""
echo "🚀 启动前端 (port 5173)..."
cd "$DIR/frontend"
nohup npx vite --port 5173 > /tmp/portm-frontend.log 2>&1 &
FRONTEND_PID=$!
echo "   PID: $FRONTEND_PID"

echo ""
echo "╔════════════════════════════════════╗"
echo "║  ✅ 启动完成!                      ║"
echo "║  后端: http://localhost:$PORT/docs  ║"
echo "║  前端: http://localhost:5173       ║"
echo "╚════════════════════════════════════╝"
echo ""
echo "按 Ctrl+C 停止所有服务"

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; echo '服务已停止'" SIGINT SIGTERM
wait
