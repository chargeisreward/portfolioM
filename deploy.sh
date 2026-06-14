#!/usr/bin/env bash
# PortfolioM — 一键部署到 Zeabur
# 使用方法:
#   1. 首次:  ./deploy.sh setup  (初始化 zeabur 项目, 拿到 SVC/ENV ID)
#   2. 后续:  export SVC=...; export ENV=...; ./deploy.sh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

# Zeabur CLI 路径(可选 — 也可以装到 PATH)
ZEABUR="${ZEABUR:-zeabur}"

# ⚠️ 在此填入你 Zeabur 项目的 service / environment ID
# 首次运行 ./deploy.sh setup 后, zeabur CLI 会打印这些 ID
SVC="${SVC:-}"      # 后端 service id
ENV="${ENV:-}"      # environment id
VITE_API_URL="${VITE_API_URL:-https://portfoliom-backend.zeabur.app}"

case "${1:-push}" in
  setup)
    echo "=== 检查 Zeabur 登录状态 ==="
    "$ZEABUR" auth status
    echo "=== 列出项目 ==="
    "$ZEABUR" project list
    echo
    echo "请把 SERVICE_ID / ENVIRONMENT_ID 写进 shell:"
    echo "  export SVC=<service-id>"
    echo "  export ENV=<environment-id>"
    ;;
  push)
    echo "=== [1/3] Git 检查与推送 ==="
    git status
    git push origin main
    echo "=== [2/3] 部署 ==="
    if [[ -z "$SVC" || -z "$ENV" ]]; then
      echo "ERROR: 请先 export SVC 和 ENV (运行 ./deploy.sh setup)"
      exit 1
    fi
    "$ZEABUR" deploy --service-id "$SVC" --environment-id "$ENV"
    echo "=== [3/3] 同步环境变量 ==="
    # 把 .env 推上去 (如果有)
    if [[ -f .env ]]; then
      "$ZEABUR" variable env --service-id "$SVC" --environment-id "$ENV" --file .env
    fi
    # 前端 VITE_API_URL
    "$ZEABUR" variable update --service-id "$SVC" --environment-id "$ENV" --key VITE_API_URL --value "$VITE_API_URL" || true
    echo "=== 完成 ==="
    ;;
  log)
    "$ZEABUR" deployment log --service-id "$SVC" --environment-id "$ENV" --tail 100
    ;;
  restart)
    "$ZEABUR" service restart --id "$SVC" --environment-id "$ENV"
    ;;
  *)
    echo "用法: $0 {setup|push|log|restart}"
    exit 1
    ;;
esac
