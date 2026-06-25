# Bug Fixed Log

记录已修复的 Bug、根因分析与教训。按时间倒序排列。

---

## 2026-06-26: main.py NameError: name 'User' is not defined

**影响范围**：严重 — 后端启动失败，所有 API 端点不可用

**现象**：
```
NameError: name 'User' is not defined
  File "/app/main.py", line 393, in <module>
    @app.post("/api/admin/...")
    async def admin_endpoint(user: User = Depends(require_admin)):
```
后端容器启动后立即崩溃，docker logs 显示 NameError 堆栈。

**根因**：
`backend/main.py` line 18 的 import 语句漏了 `User`：
```python
# 错误（漏 User）
from models import FundIndexMap, Holding, AssetType, OverseasShareFinancialSnapshot
```
但 main.py 中 line 393/406/439/463/1522/4474/4555 等多处端点使用 `user: User = Depends(require_admin/require_advisor)` 类型注解。

Python 在函数定义时（非调用时）求值类型注解。由于文件顶部没有 `from __future__ import annotations`（PEP 563），类型注解在 `@app.post(...)` 装饰器执行时立即求值，此时 `User` 未 import → NameError。

**为什么本地未暴露**：
本地开发时用 `python -m uvicorn main:app`（无 --reload）启动后端，启动时这些端点尚未添加。之后修改代码添加端点但未重启进程，旧进程仍在运行，所以本地从未触发此错误。Docker 容器重新构建时，新代码从头启动，立即触发 NameError。

**修复**：
line 18 加 `User`：
```python
from models import FundIndexMap, Holding, AssetType, OverseasShareFinancialSnapshot, User
```
- commit: `12de386` (feature/auth-upgrade)
- merge: `0ba1cad` (main)

**教训**：
1. 类型注解的 import 必须完整 — Python 在函数定义时求值注解（除非加 `from __future__ import annotations`）
2. 建议加 `from __future__ import annotations`（PEP 563），使所有类型注解延迟求值为字符串，避免此类问题
3. 本地开发时修改代码后应重启服务验证，避免「旧进程掩盖新 bug」
4. Docker 构建是天然的新启动验证 — 本地能跑不代表 Docker 能跑

**验证**：
- `docker logs portfoliom2-backend` 显示 `Application startup complete` + `Uvicorn running on http://0.0.0.0:8000`
- 所有 API 端点返回 200（登录/持仓/LLM 解析/价格刷新均通过）
