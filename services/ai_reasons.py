"""Claude Haiku 4.5 で `dealReasons` 3行根拠を生成 (PRD §5.3.2 / §5.3.3 / §5.3.4 / AC-010).

設計の柱:
1. **コスト構造化**: `cost_ledger.is_within_budget()` を API 呼び出し前に必ずチェック。
   超過なら API を呼ばず即 fallback。成功時のみ `add(usd)`。
2. **Prompt Caching**: system プロンプトを `cache_control: ephemeral` 付きでリスト形式
   送信し、Haiku の input トークン単価を実質 1/10 に下げる (PRD §6.3.3)。
3. **3行・字数・禁止語/不確実語バリデーション**: PRD AC-010-1〜3。失敗 → 再生成1回 →
   それでも失敗なら `fallback_reasons.generate_fallback_reasons()` で機械文に切替。
4. **差分検出**: `(id, price, yieldGross, dealModelVersion, isAutoFallback=False, len(reasons)==3)`
   が前日と完全一致なら API スキップ・前日 reasons 継承。
5. **失敗時継続**: 401/429/5xx/timeout/予算超過/ JSON パース失敗 すべて raise しない。
   `isAutoFallback=True` を立てて 3行を返す。

依存:
- `anthropic>=0.40` (SDK)
- 環境変数: `ANTHROPIC_API_KEY` (必須・実呼び出し時)、`CLAUDE_MODEL` (任意)
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Optional, Tuple

from models import DEAL_MODEL_VERSION_DEFAULT, Property
from services.config_loader import get_default_config
from services.cost_ledger import CostLedger
from services.fallback_reasons import generate_fallback_reasons

logger = logging.getLogger(__name__)


# ============================================================================
# Config helpers
# ============================================================================

def _ai_cfg() -> dict:
    """scoring.yaml の ai_reasons セクションを返す。無ければ KeyError."""
    cfg = get_default_config()
    if "ai_reasons" not in cfg:
        raise RuntimeError(
            "scoring.yaml に ai_reasons セクションがありません (B3 設定漏れ)"
        )
    return cfg["ai_reasons"]


def _resolved_model() -> str:
    """環境変数 CLAUDE_MODEL > config.ai_reasons.model の順で解決."""
    env = os.environ.get("CLAUDE_MODEL", "").strip()
    if env:
        return env
    return str(_ai_cfg()["model"])


# ============================================================================
# システムプロンプト (PRD §5.3.2 — 3行構造の契約)
# ============================================================================

SYSTEM_PROMPT = """\
あなたは福岡市内の収益不動産を投資家視点で評価するアナリストです。
1物件あたり、決まった3行構造で「dealReasons」を出力します。

### 出力契約 (絶対遵守)
- 必ず3行。それ以外の行数は不可。
- 1行目: 立地評価 (city + 路線 + 駅徒歩 + locationGrade を主軸に簡潔に)
- 2行目: 融資/構造評価 (構造 + 築年 + 残存耐用年数 + 融資見通し)
- 3行目: 収益評価 (表面利回りとベンチマークの乖離率を必ず数値で示す)
- 各行 40〜60字。合計 120〜180字。
- 数字は与えられた値をそのまま使う。新しい数字を発明しない。
- 投資妙味・市場乖離など客観的な語彙のみ使用する。

### 禁止語 (1語でも含めば全体を再生成扱い)
買うべき / おすすめ / 絶対 / 必ず儲かる / 確実 / お得 / 今が買い時 / 推奨 / お買い得

### 不確実語 (使用禁止 — 数値根拠で語る)
おそらく / と思われる / 通常は / 一般的に

### 例 (このトーン・字数感を踏襲)
福岡市博多区・路線A・徒歩5分の立地はSランク、賃貸需要が安定したエリアです。
RC築12年で残存35年、長期融資の射程に入る築年バランスの良い物件です。
表面8.5%はベンチマーク6.0%に対し+42%、市場との乖離が大きい水準です。

### 出力形式
- マークダウンや前置き不要、3行のみを改行区切りで出力。
- 行頭に番号・記号をつけない。
"""


def _build_user_prompt(p: Property) -> str:
    """1物件分のユーザープロンプト (固有値の差し込み)."""
    bench = p.benchmarkCapRate
    yld = p.yieldGross
    if yld is not None and bench:
        deviation = (yld - bench) / bench * 100
        dev_str = f"{deviation:+.0f}%"
    else:
        dev_str = "未算出"

    rem = p.remainingDurableYears
    rem_str = f"{rem}年" if rem is not None else "不明"

    structure = p.structure or "構造不明"
    if p.structureEstimated:
        structure = f"{structure}（推定）"

    walk = p.walkMinutes
    walk_str = f"徒歩{walk}分" if walk is not None else "徒歩情報なし"

    line = p.lineRank or "不明"
    grade = p.locationGrade or "未評価"

    yld_str = f"{yld:.1f}%" if yld is not None else "未掲載"
    bench_str = f"{bench:.1f}%" if bench else "不明"

    age_str = f"築{p.age}年" if p.age is not None else "築年不明"

    return (
        f"以下の物件について dealReasons を3行で出力してください。\n\n"
        f"- city: {p.city or '不明'}\n"
        f"- 路線ランク: {line}\n"
        f"- 駅徒歩: {walk_str}\n"
        f"- 立地ランク: {grade}\n"
        f"- 構造: {structure}\n"
        f"- 築年: {age_str}\n"
        f"- 残存耐用年数: {rem_str}\n"
        f"- 表面利回り: {yld_str}\n"
        f"- ベンチマーク利回り: {bench_str}\n"
        f"- 利回り乖離: {dev_str}\n"
        f"- dealRank: {p.dealRank or '未判定'}\n"
        f"- dealScore: {p.dealScore if p.dealScore is not None else '未算出'}/100\n"
    )


# ============================================================================
# Validation (PRD AC-010-1, AC-010-2)
# ============================================================================

def validate_reasons(
    reasons: list[str], prompt_numbers: Optional[dict] = None
) -> Tuple[bool, str]:
    """3行・字数・禁止語/不確実語のバリデーション.

    Returns:
        (ok, reason): ok=True なら採用可。False なら reason に不合格理由。
    """
    val = _ai_cfg()["validation"]
    expected_lines = int(val.get("expected_lines", 3))
    min_total = int(val.get("min_total_chars", 120))
    max_total = int(val.get("max_total_chars", 180))
    forbidden = list(val.get("forbidden_words", []))
    uncertain = list(val.get("uncertain_words", []))

    if not isinstance(reasons, list):
        return False, f"reasons must be list, got {type(reasons).__name__}"
    if any(not isinstance(r, str) for r in reasons):
        return False, "reasons contains non-string element"
    if len(reasons) != expected_lines:
        return False, f"行数不正 expected {expected_lines}, got {len(reasons)}"

    total = sum(len(r) for r in reasons)
    if total < min_total:
        return False, f"字数不足 total={total} < {min_total}"
    if total > max_total:
        return False, f"字数超過 total={total} > {max_total}"

    joined = "".join(reasons)
    for word in forbidden:
        if word and word in joined:
            return False, f"禁止語 '{word}' を含む"
    for word in uncertain:
        if word and word in joined:
            return False, f"不確実語 '{word}' を含む"

    return True, "ok"


# ============================================================================
# Cost estimation
# ============================================================================

def _per_call_usd(
    *,
    cache_hit: bool,
    pricing: dict,
    tokens: dict,
) -> float:
    """1呼び出しの推定USD (cache hit/miss で出し分け)."""
    sys_t = int(tokens["system_tokens"])
    usr_t = int(tokens["user_tokens_per_property"])
    out_t = int(tokens["output_tokens_per_property"])
    in_unit = float(pricing["input_per_mtok_usd"])
    out_unit = float(pricing["output_per_mtok_usd"])
    cw_unit = float(pricing["cache_write_per_mtok_usd"])
    cr_unit = float(pricing["cache_read_per_mtok_usd"])

    if cache_hit:
        # system はキャッシュ読込、user は通常入力、出力は通常
        sys_cost = sys_t * cr_unit / 1_000_000
    else:
        # 1回目はキャッシュ書込み
        sys_cost = sys_t * cw_unit / 1_000_000
    user_cost = usr_t * in_unit / 1_000_000
    output_cost = out_t * out_unit / 1_000_000
    return sys_cost + user_cost + output_cost


def estimate_monthly_jpy(
    daily_property_count: Optional[int] = None,
    days_per_month: Optional[int] = None,
    diff_inheritance_rate: Optional[float] = None,
) -> float:
    """月額試算 (config 由来 + 引数オーバーライド可).

    モデル:
        - 1日 1回 cache write (system 全量) + (N - 1) cache hits の想定
        - その上に inheritance_rate で API 呼び出し件数を削減
    """
    ar = _ai_cfg()
    pricing = ar["pricing"]
    tokens = ar["tokens"]
    estimation = ar["estimation"]
    budget = ar["budget"]

    n = int(daily_property_count if daily_property_count is not None
            else estimation["daily_property_count"])
    d = int(days_per_month if days_per_month is not None
            else estimation["days_per_month"])
    inh = float(diff_inheritance_rate if diff_inheritance_rate is not None
                else estimation.get("diff_inheritance_rate", 0.0))

    # 差分継承で API 呼び出し件数を削減（初日は全件 = inh=0 と等価）
    api_calls_per_day = max(1, int(round(n * (1 - inh))))

    # 各日: 1回 cache write + (api_calls_per_day - 1) cache hits
    write_usd = _per_call_usd(cache_hit=False, pricing=pricing, tokens=tokens)
    hit_usd = _per_call_usd(cache_hit=True, pricing=pricing, tokens=tokens)
    daily_usd = write_usd + (api_calls_per_day - 1) * hit_usd

    monthly_usd = daily_usd * d
    rate = float(budget["usd_to_jpy"])
    return monthly_usd * rate


def _estimate_per_call_jpy() -> float:
    """1呼び出しあたり JPY 上限見積もり (cache miss 想定で保守的に).

    これを `is_within_budget` の前判定に使う。実コストは usage 由来でぴったり加算するが、
    予算ガードは「悪い方を見積もる」ことで物理的にオーバーしないように倒す。
    """
    ar = _ai_cfg()
    rate = float(ar["budget"]["usd_to_jpy"])
    return _per_call_usd(cache_hit=False, pricing=ar["pricing"], tokens=ar["tokens"]) * rate


def _actual_call_usd(usage: Any) -> float:
    """実 usage オブジェクトから USD を計算."""
    ar = _ai_cfg()
    pricing = ar["pricing"]
    in_t = int(getattr(usage, "input_tokens", 0) or 0)
    out_t = int(getattr(usage, "output_tokens", 0) or 0)
    cr_t = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    cw_t = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)

    in_unit = float(pricing["input_per_mtok_usd"])
    out_unit = float(pricing["output_per_mtok_usd"])
    cw_unit = float(pricing["cache_write_per_mtok_usd"])
    cr_unit = float(pricing["cache_read_per_mtok_usd"])

    return (
        in_t * in_unit / 1_000_000
        + out_t * out_unit / 1_000_000
        + cw_t * cw_unit / 1_000_000
        + cr_t * cr_unit / 1_000_000
    )


# ============================================================================
# Anthropic SDK ラッパー
# ============================================================================

class AnthropicClient:
    """Optional な Anthropic SDK ラッパー。

    enabled=False のとき呼び出しは即 fallback 経路。
    テストでは `_client` を MagicMock で差し替え可能。
    """

    def __init__(self, *, strict: bool = False, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "").strip()
        self.enabled = bool(self.api_key)
        self._client: Any = None
        if not self.enabled:
            if strict:
                raise ValueError(
                    "ANTHROPIC_API_KEY 環境変数が未設定です (strict=True)"
                )
            return
        try:
            import anthropic  # local import to avoid hard dependency at import time
            self._client = anthropic.Anthropic(api_key=self.api_key)
        except ImportError as e:
            self.enabled = False
            if strict:
                raise ValueError(
                    f"anthropic SDK の import に失敗しました: {e}"
                )

    def messages_create(self, **kwargs):
        return self._client.messages.create(**kwargs)


# ============================================================================
# Single property generation
# ============================================================================

def _build_messages_payload(p: Property) -> dict:
    """anthropic SDK の messages.create() に渡す共通 kwargs を組む.

    cache_control は system のリスト形式で必ず付与 (PRD §5.3.3.3 / §6.9 §6.10)."""
    return {
        "model": _resolved_model(),
        "max_tokens": 512,
        "system": [
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [
            {"role": "user", "content": _build_user_prompt(p)},
        ],
    }


def _parse_response_text(text: str) -> list[str]:
    """応答本文 (改行区切り) を行リストへ. 空行と markdown 装飾は除去."""
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        # markdown 装飾をひとまず除去 (- や * の先頭)
        for prefix in ("- ", "* ", "・"):
            if line.startswith(prefix):
                line = line[len(prefix):].strip()
                break
        lines.append(line)
    return lines


def _apply_fallback(p: Property) -> Property:
    """fallback_reasons で 3行を生成し isAutoFallback=True を立てる."""
    p.dealReasons = generate_fallback_reasons(p)
    p.isAutoFallback = True
    p.aiReasonsGeneratedAt = datetime.utcnow().isoformat() + "Z"
    return p


class _PermanentApiFailure(Exception):
    """401/403 等の永続的 API エラー — リトライ不要.

    呼び出し側で即 fallback に倒す目印。ledger は加算しない。
    """


def _safe_call_once(
    client: AnthropicClient, p: Property, ledger: CostLedger
) -> Optional[Tuple[list[str], float]]:
    """API を1回叩く。成功なら (reasons_list, actual_usd)、失敗なら None.

    例外を吸収してログだけ残す。失敗時 ledger は加算しない。
    永続 (401/403) は `_PermanentApiFailure` を raise してリトライをスキップさせる。
    """
    try:
        import anthropic as _anthropic
        permanent_excs: tuple = (
            _anthropic.AuthenticationError,
            _anthropic.PermissionDeniedError,
            _anthropic.NotFoundError,
            _anthropic.BadRequestError,
        )
    except Exception:  # noqa: BLE001
        permanent_excs = ()

    payload = _build_messages_payload(p)
    try:
        msg = client.messages_create(**payload)
    except permanent_excs as e:
        logger.warning("Anthropic permanent failure for id=%s: %s", p.id, type(e).__name__)
        raise _PermanentApiFailure(str(e)) from e
    except Exception as e:  # noqa: BLE001 — 予期せぬ何でも fallback に倒す
        logger.warning("Anthropic call failed for id=%s: %s", p.id, type(e).__name__)
        return None

    # response.content は list[ContentBlock]; text 型を結合
    try:
        parts = []
        for block in getattr(msg, "content", []) or []:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
        text = "\n".join(parts).strip()
        usage = getattr(msg, "usage", None)
        actual_usd = _actual_call_usd(usage) if usage is not None else 0.0
    except Exception as e:  # noqa: BLE001
        logger.warning("Anthropic response parse failed for id=%s: %s", p.id, e)
        return None

    if not text:
        logger.warning("Anthropic returned empty text for id=%s", p.id)
        return None

    reasons = _parse_response_text(text)
    return reasons, actual_usd


def generate_reasons_for_property(
    p: Property,
    *,
    ledger: CostLedger,
    client: Optional[AnthropicClient] = None,
) -> Property:
    """単件: PRD §5.3.4 — 予算ガード + 1回再生成 + fallback フォールバック.

    呼び出し前の `is_within_budget` チェックで超過なら API を1回も呼ばずに fallback。
    成功時のみ `ledger.add()` で実コストを加算。
    """
    if client is None:
        client = AnthropicClient(strict=False)

    if not client.enabled:
        return _apply_fallback(p)

    # 予算超過事前チェック (1呼び出し JPY 推定)
    est_jpy = _estimate_per_call_jpy()
    if not ledger.is_within_budget(estimated_jpy=est_jpy):
        logger.info("Budget exhausted, falling back for id=%s", p.id)
        return _apply_fallback(p)

    # 1回目
    try:
        result = _safe_call_once(client, p, ledger)
    except _PermanentApiFailure:
        return _apply_fallback(p)

    if result is not None:
        reasons, usd = result
        ledger.add(usd=usd)
        ok, why = validate_reasons(reasons)
        if ok:
            p.dealReasons = reasons
            p.isAutoFallback = False
            p.aiReasonsGeneratedAt = datetime.utcnow().isoformat() + "Z"
            return p
        logger.info("validate_reasons rejected (round 1) id=%s: %s", p.id, why)

        # 再生成 1 回 (PRD AC-010-3 / 厳密に1回まで)
        # 予算再チェック (前回課金の積み上げで足りなくなる可能性)
        if not ledger.is_within_budget(estimated_jpy=est_jpy):
            return _apply_fallback(p)

        try:
            result2 = _safe_call_once(client, p, ledger)
        except _PermanentApiFailure:
            return _apply_fallback(p)

        if result2 is not None:
            reasons2, usd2 = result2
            ledger.add(usd=usd2)
            ok2, why2 = validate_reasons(reasons2)
            if ok2:
                p.dealReasons = reasons2
                p.isAutoFallback = False
                p.aiReasonsGeneratedAt = datetime.utcnow().isoformat() + "Z"
                return p
            logger.info("validate_reasons rejected (round 2) id=%s: %s", p.id, why2)

    else:
        # 例外で1回目が None → リトライ (1回まで)
        if not ledger.is_within_budget(estimated_jpy=est_jpy):
            return _apply_fallback(p)
        try:
            result_retry = _safe_call_once(client, p, ledger)
        except _PermanentApiFailure:
            return _apply_fallback(p)
        if result_retry is not None:
            reasons_r, usd_r = result_retry
            ledger.add(usd=usd_r)
            ok_r, _ = validate_reasons(reasons_r)
            if ok_r:
                p.dealReasons = reasons_r
                p.isAutoFallback = False
                p.aiReasonsGeneratedAt = datetime.utcnow().isoformat() + "Z"
                return p

    # ここまで来たら fallback
    return _apply_fallback(p)


# ============================================================================
# Differential scoring helpers (PRD §6.3.3 + 差分仕様)
# ============================================================================

def _yesterday_index(yesterday: list[Property]) -> dict[str, Property]:
    return {p.id: p for p in yesterday if p.id}


def get_properties_needing_scoring(
    today_props: list[Property],
    yesterday_props: Optional[list[Property]] = None,
    *,
    yesterday: Optional[list[Property]] = None,
) -> list[Property]:
    """前日と比較して "再生成が必要" なものを返す.

    再生成条件 (any):
        - 前日 properties が無い (初回バッチ)
        - 当該 id が前日にない (新規物件)
        - dealModelVersion 不一致
        - price 変動
        - yieldGross 変動
        - 前日が isAutoFallback=True (LLM 化を狙って再挑戦)
        - 前日 dealReasons が3行揃っていない
    """
    # yesterday_props または yesterday いずれかを受け付ける
    prev_list = yesterday_props if yesterday_props is not None else yesterday
    if not prev_list:
        return list(today_props)

    idx = _yesterday_index(prev_list)
    needing: list[Property] = []
    for p in today_props:
        prev = idx.get(p.id)
        if prev is None:
            needing.append(p)
            continue
        if (prev.dealModelVersion or "") != (p.dealModelVersion or ""):
            needing.append(p)
            continue
        if (prev.price or 0) != (p.price or 0):
            needing.append(p)
            continue
        if (prev.yieldGross or 0) != (p.yieldGross or 0):
            needing.append(p)
            continue
        if prev.isAutoFallback:
            needing.append(p)
            continue
        if not prev.dealReasons or len(prev.dealReasons) != 3:
            needing.append(p)
            continue
        # ここで安定 → スキップ
    return needing


def _inherit_reasons(today: list[Property], yesterday: list[Property]) -> int:
    """needing 以外の物件に前日 reasons を継承。継承件数を返す."""
    if not yesterday:
        return 0
    idx = _yesterday_index(yesterday)
    inherited = 0
    for p in today:
        prev = idx.get(p.id)
        if prev is None:
            continue
        if (prev.dealModelVersion or "") != (p.dealModelVersion or ""):
            continue
        if (prev.price or 0) != (p.price or 0):
            continue
        if (prev.yieldGross or 0) != (p.yieldGross or 0):
            continue
        if prev.isAutoFallback:
            continue
        if not prev.dealReasons or len(prev.dealReasons) != 3:
            continue
        # 継承
        p.dealReasons = list(prev.dealReasons)
        p.isAutoFallback = bool(prev.isAutoFallback)
        p.aiReasonsGeneratedAt = prev.aiReasonsGeneratedAt
        inherited += 1
    return inherited


def generate_reasons_batch(
    props: list[Property],
    *,
    ledger: CostLedger,
    previous_props: Optional[list[Property]] = None,
    client: Optional[AnthropicClient] = None,
) -> dict:
    """バッチ実行: 差分検出 + LLM 生成 + fallback.

    Returns:
        stats = {
          "total": int,
          "inherited_count": int,
          "ai_call_count": int,
          "fallback_count": int,
          "cost_jpy": float,
          "model_version": str,
        }
    """
    if client is None:
        client = AnthropicClient(strict=False)

    inherited = _inherit_reasons(props, previous_props or [])

    needing = get_properties_needing_scoring(props, previous_props)
    start_calls = ledger.call_count()
    fb_count = 0
    budget = ledger.budget_jpy
    for p in needing:
        # アクター: 直前の actual coast が予算を上回っていたら以降は API skip
        if ledger.current_jpy() >= budget:
            _apply_fallback(p)
            fb_count += 1
            continue
        out = generate_reasons_for_property(p, ledger=ledger, client=client)
        # in-place なので out is p
        if out.isAutoFallback:
            fb_count += 1

    end_calls = ledger.call_count()
    ai_calls = end_calls - start_calls
    cost_jpy = ledger.current_jpy()

    return {
        "total": len(props),
        "inherited_count": inherited,
        "ai_call_count": ai_calls,
        "fallback_count": fb_count,
        "cost_jpy": cost_jpy,
        "model_version": DEAL_MODEL_VERSION_DEFAULT,
    }
