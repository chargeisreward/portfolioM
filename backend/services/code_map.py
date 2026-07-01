"""API 代码映射服务：标准代码 → 各 API 调用时的代码。

设计：
- 数据库表 api_code_map(code_in, api_strategy, code_out, ...)
- 服务启动时初始化默认映射（含腾讯 K 线美股加 .OQ、akshare 基金去 .OF 等）
- transform_code(code_in, api_strategy) → code_out
  - 命中 DB：返回
  - miss：用内置的 _default_transform 算 + 惰性持久化
  - 这样新增 API 时只需在 DEFAULT_MAPS 加规则 + 加 transform 函数
- 内存缓存避免每次查 DB
"""
import logging
from sqlalchemy.orm import Session
from models import ApiCodeMap

logger = logging.getLogger(__name__)

# ============================================================
# 启动时初始化的默认映射
# ============================================================
# 格式: (code_in, api_strategy, code_out, market, note)
# 规则来源：data_get.md §2.1 + 实际诊断
# ============================================================
DEFAULT_MAPS = [
    # ----- 腾讯 K 线 (tencent_kline) — 必须加交易所后缀 -----
    # 美股 NASDAQ (.OQ)
    ("NVDA", "tencent_kline", "usNVDA.OQ", "US", "NASDAQ"),
    ("GOOGL", "tencent_kline", "usGOOGL.OQ", "US", "NASDAQ"),
    ("AAPL", "tencent_kline", "usAAPL.OQ", "US", "NASDAQ"),
    ("MSFT", "tencent_kline", "usMSFT.OQ", "US", "NASDAQ"),
    ("AMZN", "tencent_kline", "usAMZN.OQ", "US", "NASDAQ"),
    ("TSLA", "tencent_kline", "usTSLA.OQ", "US", "NASDAQ"),
    ("AMD", "tencent_kline", "usAMD.OQ", "US", "NASDAQ"),
    ("INTC", "tencent_kline", "usINTC.OQ", "US", "NASDAQ"),
    ("SNDK", "tencent_kline", "usSNDK.OQ", "US", "NASDAQ"),
    ("QQQ", "tencent_kline", "usQQQ.OQ", "US", "NASDAQ ETF"),
    # A 股 + ETF — 持仓写法 "159326.SZ" → 腾讯 "sz159326"
    ("159326.SZ", "tencent_kline", "sz159326", "CN", "深交所 ETF"),
    ("159870.SZ", "tencent_kline", "sz159870", "CN", "深交所 ETF"),
    # ----- 腾讯实时行情 (tencent_quote) — 不加后缀！qt.gtimg.cn 不接受 usNVDA.OQ -----
    ("NVDA", "tencent_quote", "usNVDA", "US", "K线用 .OQ；实时行情用裸 ticker"),
    ("GOOGL", "tencent_quote", "usGOOGL", "US", None),
    ("AAPL", "tencent_quote", "usAAPL", "US", None),
    ("MSFT", "tencent_quote", "usMSFT", "US", None),
    ("AMZN", "tencent_quote", "usAMZN", "US", None),
    ("TSLA", "tencent_quote", "usTSLA", "US", None),
    ("AMD", "tencent_quote", "usAMD", "US", None),
    ("INTC", "tencent_quote", "usINTC", "US", None),
    ("SNDK", "tencent_quote", "usSNDK", "US", None),
    ("QQQ", "tencent_quote", "usQQQ", "US", None),
    # ----- akshare OF 基金 (akshare_fund_nav) — 去掉 .OF 后缀 -----
    # 拉所有 .OF 持仓的对应映射
    # (具体 fund code 在 populate_default_maps 里动态生成)
]


# ============================================================
# 内部 _default_transform：当 DB miss 时按规则现场算
# ============================================================
import re

_TENCENT_KLINE_US_SUFFIX = {
    # NASDAQ
    "NVDA": ".OQ", "GOOGL": ".OQ", "AAPL": ".OQ", "MSFT": ".OQ",
    "AMZN": ".OQ", "TSLA": ".OQ", "AMD": ".OQ", "INTC": ".OQ",
    "SNDK": ".OQ",
}


def _default_transform(code_in: str, api_strategy: str) -> str | None:
    """内置规则：DB miss 时按 api_strategy 现场算。
    返回 None 表示该 API 不支持该 code（让 caller 走 fallback）。"""
    c = (code_in or "").upper().strip()
    suf = code_in.strip()  # 保留原大小写（A 股 .SH/.SZ 是大写）
    if not c:
        return None

    if api_strategy == "tencent_quote":
        # 实时行情：usNVDA（无后缀）
        if c in _TENCENT_KLINE_US_SUFFIX or c == "QQQ":
            return f"us{c}"
        # A 股 ETF：159326.SZ → sz159326
        m = re.match(r"^(\d{6})\.(SH|SZ)$", suf)
        if m:
            return m.group(2).lower() + m.group(1)
        # 港股：00700.HK → hk00700
        m = re.match(r"^(\d{5})\.HK$", suf)
        if m:
            return "hk" + m.group(1)
        if c.isdigit() and len(c) == 6:
            prefix = "sh" if c.startswith(("5", "6")) else "sz"
            return prefix + c
        return None

    if api_strategy == "tencent_kline":
        # K 线：usNVDA.OQ（必须加交易所后缀，否则只返 1 天）
        if c in _TENCENT_KLINE_US_SUFFIX:
            return f"us{c}{_TENCENT_KLINE_US_SUFFIX[c]}"
        if c == "QQQ":
            return "usQQQ.OQ"
        m = re.match(r"^(\d{6})\.(SH|SZ)$", suf)
        if m:
            return m.group(2).lower() + m.group(1)
        m = re.match(r"^(\d{5})\.HK$", suf)
        if m:
            return "hk" + m.group(1)
        if c.isdigit() and len(c) == 6:
            prefix = "sh" if c.startswith(("5", "6")) else "sz"
            return prefix + c
        if c.isdigit() and len(c) == 5:
            return "hk" + c
        return None

    if api_strategy == "akshare_fund_nav":
        # OF 基金：去 .OF
        if c.endswith(".OF"):
            return c[:-3]
        return c  # 已是无后缀形式

    if api_strategy == "akshare_etf_index":
        # akshare fund_etf_fund_info_em 接受 6 位数字
        if c.endswith(".SH") or c.endswith(".SZ"):
            return c.split(".")[0]
        return c

    if api_strategy == "akshare_currency":
        return c  # 货币代码不需要转换

    if api_strategy == "yfinance":
        return c  # yfinance 用标准 ticker

    return None


# ============================================================
# 内存缓存
# ============================================================
_CACHE: dict[tuple[str, str], str] = {}


def _ensure_cache_loaded(db: Session) -> None:
    """首次访问时一次性加载到内存（启动后修改需 invalidate_cache）"""
    global _CACHE
    if _CACHE:
        return
    for row in db.query(ApiCodeMap).all():
        _CACHE[(row.code_in, row.api_strategy)] = row.code_out


def invalidate_cache() -> None:
    global _CACHE
    _CACHE = {}


def transform_code(code_in: str, api_strategy: str, db: Session) -> str | None:
    """标准 code → API 调用 code。None 表示该 API 不支持此 code。
    优先用 DB 命中；miss 时用内置规则现场算 + 惰性持久化。"""
    if not code_in or not api_strategy:
        return code_in
    _ensure_cache_loaded(db)
    key = (code_in, api_strategy)
    if key in _CACHE:
        return _CACHE[key]
    out = _default_transform(code_in, api_strategy)
    if out is not None and out != code_in:
        # 持久化到 DB（失败不阻塞）
        try:
            db.add(ApiCodeMap(
                code_in=code_in, api_strategy=api_strategy, code_out=out,
                market=_market_of_code(code_in),
            ))
            db.commit()
        except Exception:
            db.rollback()
    _CACHE[key] = out
    return out


def _market_of_code(code: str) -> str:
    c = (code or "").upper().strip()
    if c.endswith(".OF"): return "OF"
    if c.endswith(".HK"): return "HK"
    if c.endswith(".SH") or c.endswith(".SZ"): return "CN"
    return "US"


# ============================================================
# 初始化
# ============================================================

def populate_default_maps(db: Session) -> int:
    """启动时初始化默认映射。幂等：已存在则跳过。
    自动拉 holdings 里所有 .OF + 腾讯 K 线美股默认条目一并初始化。"""
    # 1. 静态 DEFAULT_MAPS
    existing = {(r.code_in, r.api_strategy) for r in db.query(ApiCodeMap).all()}
    added = 0
    for code_in, api, code_out, market, note in DEFAULT_MAPS:
        if (code_in, api) in existing:
            continue
        try:
            db.add(ApiCodeMap(
                code_in=code_in, api_strategy=api, code_out=code_out,
                market=market, note=note,
            ))
            db.commit()
            added += 1
        except Exception:
            db.rollback()
    # 2. 自动为所有 .OF 持仓生成 akshare_fund_nav 映射（去 .OF）
    from models import Holding
    of_codes = {h.security_code for h in db.query(Holding).all() if h.security_code.endswith(".OF")}
    for code_in in of_codes:
        if (code_in, "akshare_fund_nav") in existing:
            continue
        try:
            db.add(ApiCodeMap(
                code_in=code_in, api_strategy="akshare_fund_nav",
                code_out=code_in[:-3], market="OF", note="auto-generated for akshare",
            ))
            db.commit()
            added += 1
        except Exception:
            db.rollback()
    logger.info("api_code_map initialized: %d new rows", added)
    invalidate_cache()
    return added


def list_maps(db: Session, api_strategy: str | None = None) -> list[dict]:
    _ensure_cache_loaded(db)
    q = db.query(ApiCodeMap)
    if api_strategy:
        q = q.filter(ApiCodeMap.api_strategy == api_strategy)
    rows = q.order_by(ApiCodeMap.api_strategy, ApiCodeMap.code_in).all()
    return [{
        "id": r.id,
        "code_in": r.code_in,
        "api_strategy": r.api_strategy,
        "code_out": r.code_out,
        "market": r.market,
        "note": r.note,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    } for r in rows]


def upsert_map(db: Session, code_in: str, api_strategy: str, code_out: str, market: str | None = None, note: str | None = None) -> dict:
    """新增/更新一条映射"""
    row = (
        db.query(ApiCodeMap)
        .filter(ApiCodeMap.code_in == code_in, ApiCodeMap.api_strategy == api_strategy)
        .first()
    )
    if row:
        row.code_out = code_out
        row.market = market or row.market
        row.note = note or row.note
    else:
        row = ApiCodeMap(
            code_in=code_in, api_strategy=api_strategy, code_out=code_out,
            market=market, note=note,
        )
        db.add(row)
    db.commit()
    db.refresh(row)
    invalidate_cache()
    return {"id": row.id, "code_in": row.code_in, "api_strategy": row.api_strategy, "code_out": row.code_out}


def delete_map(db: Session, code_in: str, api_strategy: str) -> bool:
    row = (
        db.query(ApiCodeMap)
        .filter(ApiCodeMap.code_in == code_in, ApiCodeMap.api_strategy == api_strategy)
        .first()
    )
    if not row:
        return False
    db.delete(row)
    db.commit()
    invalidate_cache()
    return True


# ============================================================
# 完整解析路径（DB → 启发式 → raw probe → LLM 兜底）
# 用于 cron 等批量场景：保证每个 user_code 都返回 confirmed tencent_quote code
# ============================================================

def resolve_tencent_quote_code(code_in: str, db: Session) -> str | None:
    """四段解析路径（带 LLM 兜底），返回唯一确认可用的 tencent_quote code。

    路径：
      1. DB hit（transform_code 已含 DEFAULT_MAPS + 历史 persist）
      2. 启发式（_default_transform：覆盖 .SH/.SZ/.HK/纯数字）
      3. raw probe（单次 tencent_get 直接试 user_code 原值）
      4. LLM 候选 + 单次验证（仅 1-3 都失败时启用）

    Returns: confirmed tencent code（如 'usNVDA'）；失败返回 None。
    """
    if not code_in:
        return None

    # ---- 路径 1：DB hit（已含 DEFAULT_MAPS）----
    mapped = transform_code(code_in, "tencent_quote", db)
    if mapped and mapped != code_in:
        # transform_code 已 persist，无需再验证
        return mapped

    # ---- 路径 2：启发式（_default_transform）----
    heur = _default_transform(code_in, "tencent_quote")
    if heur and heur != code_in:
        # 惰性持久化 + 直接返回（_default_transform 推断的格式与已有 _to_tencent_ticker 一致）
        try:
            db.add(ApiCodeMap(
                code_in=code_in, api_strategy="tencent_quote",
                code_out=heur, market=_market_of_code(code_in),
                note="heuristic _default_transform",
            ))
            db.commit()
        except Exception:
            db.rollback()
        _CACHE[(code_in, "tencent_quote")] = heur
        return heur

    # ---- 路径 3：raw probe（直接试原值）----
    from crawlers._http import tencent_get
    try:
        resp = tencent_get(f"https://qt.gtimg.cn/q={code_in}", timeout=5.0)
        if resp and resp.status_code == 200 and "=" in resp.text and '""' not in resp.text.split("\n")[0]:
            # raw code 直接可用 → persist
            try:
                db.add(ApiCodeMap(
                    code_in=code_in, api_strategy="tencent_quote",
                    code_out=code_in, market=_market_of_code(code_in),
                    note="raw probe",
                ))
                db.commit()
            except Exception:
                db.rollback()
            _CACHE[(code_in, "tencent_quote")] = code_in
            return code_in
    except Exception:
        pass

    # ---- 路径 4：LLM 兜底 ----
    candidates = _llm_suggest_tencent_candidates(code_in)
    if not candidates:
        return None

    for cand in candidates:
        if cand == code_in:
            continue  # 已在路径 3 试过
        try:
            resp = tencent_get(f"https://qt.gtimg.cn/q={cand}", timeout=5.0)
            if resp and resp.status_code == 200 and "=" in resp.text \
                    and '""' not in resp.text.split("\n")[0]:
                # LLM 候选验证通过
                try:
                    db.add(ApiCodeMap(
                        code_in=code_in, api_strategy="tencent_quote",
                        code_out=cand, market=_market_of_code(code_in),
                        note="LLM resolved",
                    ))
                    db.commit()
                except Exception:
                    db.rollback()
                _CACHE[(code_in, "tencent_quote")] = cand
                logger.info("LLM 兜底解析: %s -> %s", code_in, cand)
                return cand
        except Exception:
            continue

    logger.warning("LLM 候选全部失败: code=%s candidates=%s", code_in, candidates)
    return None


def _llm_suggest_tencent_candidates(code_in: str) -> list[str]:
    """调 LLM 推测 code 的 tencent_quote 候选。失败返 []。"""
    try:
        from services.llm_service import _call_llm
    except ImportError:
        return []
    sys_prompt = (
        "你是证券代码格式专家。给定一个未知的证券代码（持仓写法），"
        "你需要猜测它在腾讯 qt.gtimg.cn 行情接口的格式。"
        "已知腾讯格式：A 股带 sh/sz 前缀（sh600519），港股带 hk 前缀（hk00700），"
        "美股带 us 前缀（usNVDA）。"
        "请只返回 JSON：{\"candidates\": [\"usXXX\", \"XXX\", ...], \"reason\": \"简短原因\"}"
    )
    user_prompt = f"证券代码: {code_in}\n请列出最可能的 tencent qt.gtimg.cn 候选（按概率排序，最多 5 个）"
    raw = _call_llm(sys_prompt, user_prompt, temperature=0.0, timeout=20.0)
    if not raw:
        return []
    try:
        import json
        data = json.loads(raw)
        cands = data.get("candidates", [])
        return [c for c in cands if isinstance(c, str) and c][:5]
    except Exception:
        return []
