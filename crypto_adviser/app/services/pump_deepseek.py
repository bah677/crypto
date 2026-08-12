"""DeepSeek: мнение LLM по pump-алерту + structured watch_plan."""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from app.config import get_settings
from app.market.lunarcrush import SocialSnapshot, fetch_symbol_social
from app.pump_scan.detect import ScanHit
from app.pump_scan.entry_watch_plan import (
    ALLOWED_METRICS,
    ParsedDeepseekWatch,
    default_watch_plan,
    normalize_watch_plan,
    parse_deepseek_watch_block,
)

log = logging.getLogger(__name__)

_DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"

_METRICS_LIST = ", ".join(sorted(ALLOWED_METRICS))

_STRATEGY_SYSTEM = f"""Ты опытный крипто-аналитик стратегии «Pump-in-Downtrend fade» на Bybit linear.

Суть стратегии:
- Детектируем резкий pump на альте (RVOL, % хода).
- Целевой сетап — fade (шорт) только если монета в даунтренде на 1D: медвежий EMA stack, цена ниже EMA, просадка от хая.
- Доп. фильтры: OI (squeeze vs new money), Funding+OI Trajectory, volume climax, изоляция от BTC, дистанция до EMA.
- Вход — limit/market шорт у EMA 1D (50/100/200) или 1W (7/14/28).
- Композитный Funding+OI: peak_reversing + OI falling/flat = целевое окно; extending = рано.

Задача: по данным алерта и соц/новостному фону дать краткое мнение на русском:
1) Насколько сетап похож на fade vs риск продолжения ралли.
2) Ключевые уровни / что смотреть.
3) Что говорит социальный фон (если есть данные).
5–12 предложений, без воды, без дисклеймеров.

ОБЯЗАТЕЛЬНО в конце ответа отдельным блоком ```json ... ``` с объектом:
{{
  "entry_timing": "now" | "early" | "late" | "skip",
  "reason_short": "1 короткая фраза",
  "watch_if_early": true/false,
  "watch_plan": {{
    "ttl_hours": 12-48,
    "all_of": [{{"metric": "...", "op": "eq|ne|in|not_in|gte|lte|gt|lt", "value": ...}}],
    "any_of": [],
    "invalidate_if": [{{"metric": "...", "op": "...", "value": ...}}]
  }}
}}

Правила watch_plan:
- Метрики ТОЛЬКО из списка: {_METRICS_LIST}
- Если entry_timing=early — watch_if_early=true и план на переход к окну входа
  (обычно funding_trajectory_state → peak_reversing и oi_trend in falling/flat).
- invalidate_if: обычно price_vs_impulse_high_pct gte 15..30 (уйдёт дальше без нас).
- Не выдумывай метрики вне списка.
"""

_REEVAL_SYSTEM = f"""Ты оцениваешь, созрело ли окно входа для fade-шорта после pump.

Даны: исходный алерт, ТВОЁ ПЕРВОЕ заключение (на момент алерта), история повторных оценок (если есть),
план слежения и текущие метрики.

Важно: опирайся на первое заключение как на исходный контекст — не противоречь ему без причины,
а развивай вывод с учётом того, что изменилось в метриках с того момента.
В ответе явно сравни с первым заключением (что изменилось).

Ответь кратко на русском (3–8 предложений): можно ли входить сейчас или ещё ждать / уже поздно.

ОБЯЗАТЕЛЬНО в конце ```json ... ```:
{{
  "entry_ok": true/false,
  "timing": "now" | "early" | "late" | "skip",
  "reason_short": "...",
  "adjust_plan": null или объект watch_plan (только если ещё early и нужно подправить пороги; максимум одна корректировка)
}}

Метрики в adjust_plan только из: {_METRICS_LIST}
"""

_NUM_PREFIX = re.compile(r"^(\d+)([A-Z0-9]+)$")


@dataclass(frozen=True)
class PumpDeepseekAnalysis:
    text: str
    entry_timing: str
    watch_if_early: bool
    watch_plan: dict
    reason_short: str | None = None
    parsed: ParsedDeepseekWatch | None = None


@dataclass(frozen=True)
class PumpDeepseekReeval:
    text: str
    entry_ok: bool
    timing: str
    reason_short: str | None = None
    adjust_plan: dict | None = None


def _symbol_base(symbol: str) -> str:
    base = symbol.upper().removesuffix("USDT").removesuffix("PERP")
    m = _NUM_PREFIX.match(base)
    if m:
        return m.group(2)
    return base


def _fetch_web_snippets_sync(symbol: str, *, limit: int = 5) -> list[str]:
    """Бесплатный быстрый срез упоминаний (DuckDuckGo HTML)."""
    base = _symbol_base(symbol)
    q = urllib.parse.quote_plus(f"{base} crypto token news twitter")
    url = f"https://html.duckduckgo.com/html/?q={q}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "traiding-bot-ema/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            body = resp.read().decode(errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError):
        return []

    snippets: list[str] = []
    for m in re.finditer(
        r'class="result__snippet"[^>]*>(.*?)</(?:a|td|div)>',
        body,
        flags=re.I | re.S,
    ):
        raw = re.sub(r"<[^>]+>", " ", m.group(1))
        raw = html.unescape(re.sub(r"\s+", " ", raw)).strip()
        if len(raw) < 20:
            continue
        snippets.append(raw[:280])
        if len(snippets) >= limit:
            break
    return snippets


def _social_block(social: SocialSnapshot | None) -> str:
    if social is None:
        return "LunarCrush: нет данных"
    parts = [f"topic={social.topic}"]
    if social.galaxy_score is not None:
        parts.append(f"galaxy={social.galaxy_score:.0f}")
    if social.sentiment is not None:
        parts.append(f"sentiment={social.sentiment:.0f}")
    if social.interactions is not None:
        parts.append(f"interactions={social.interactions:.0f}")
    if social.spike_ratio is not None:
        parts.append(f"social_spike×{social.spike_ratio:.2f}")
    return "LunarCrush: " + ", ".join(parts)


def _build_user_prompt(
    hit: ScanHit,
    alert_text: str,
    *,
    social: SocialSnapshot | None,
    web_snippets: list[str],
) -> str:
    lines = [
        f"Символ: {hit.symbol}",
        f"TF: {hit.interval} · ход {hit.price_change_pct:+.1f}% · RVOL ×{hit.rvol:.1f}",
        f"Цена импульса: {hit.close}",
        "",
        "Текст алерта:",
        alert_text,
        "",
        _social_block(social),
    ]
    if hit.funding_oi:
        fot = hit.funding_oi
        lines.append(
            f"Funding+OI Trajectory: state={fot.funding_trajectory_state}, "
            f"oi={fot.oi_trend}, now={fot.funding_now}, min={fot.funding_min}"
        )
    if web_snippets:
        lines.append("")
        lines.append("Веб-сниппеты (поиск упоминаний токена):")
        for i, s in enumerate(web_snippets, 1):
            lines.append(f"{i}. {s}")
    else:
        lines.append("")
        lines.append("Веб-сниппеты: не найдены (опирайся на алерт и LunarCrush).")
    lines.append("")
    lines.append("Не забудь JSON-блок в конце.")
    return "\n".join(lines)


def _deepseek_chat_sync(system: str, user: str, *, max_tokens: int = 1100) -> str | None:
    s = get_settings()
    key = (s.deepseek_api_key or "").strip()
    if not key:
        return None
    payload = {
        "model": s.deepseek_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.4,
        "max_tokens": max_tokens,
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        _DEEPSEEK_URL,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
            "User-Agent": "traiding-bot-ema/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        err = e.read().decode(errors="replace")[:300]
        log.warning("DeepSeek HTTP %s: %s", e.code, err)
        return None
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
        log.warning("DeepSeek request failed: %s", e)
        return None

    try:
        return str(body["choices"][0]["message"]["content"]).strip()
    except (KeyError, IndexError, TypeError):
        log.warning("DeepSeek unexpected response: %s", str(body)[:200])
        return None


def analyze_pump_hit_structured_sync(hit: ScanHit, alert_text: str) -> PumpDeepseekAnalysis | None:
    if not get_settings().deepseek_ready:
        return None
    social = None
    try:
        social = fetch_symbol_social(hit.symbol)
    except Exception:
        log.debug("DeepSeek: LunarCrush skip %s", hit.symbol, exc_info=True)
    web = _fetch_web_snippets_sync(hit.symbol)
    user_prompt = _build_user_prompt(hit, alert_text, social=social, web_snippets=web)
    raw = _deepseek_chat_sync(_STRATEGY_SYSTEM, user_prompt)
    if not raw:
        return None
    user_text, parsed = parse_deepseek_watch_block(raw)
    if parsed is None:
        return PumpDeepseekAnalysis(
            text=user_text or raw,
            entry_timing="unknown",
            watch_if_early=False,
            watch_plan=default_watch_plan(),
            reason_short=None,
            parsed=None,
        )
    return PumpDeepseekAnalysis(
        text=user_text or raw,
        entry_timing=parsed.entry_timing,
        watch_if_early=parsed.watch_if_early,
        watch_plan=parsed.watch_plan,
        reason_short=parsed.reason_short,
        parsed=parsed,
    )


def analyze_pump_hit_sync(hit: ScanHit, alert_text: str) -> str | None:
    """Обратная совместимость: только текст."""
    res = analyze_pump_hit_structured_sync(hit, alert_text)
    return res.text if res else None


def format_deepseek_reply(text: str, *, analysis: PumpDeepseekAnalysis | None = None) -> str:
    safe = html.escape(text.strip())
    parts = [f"🤖 <b>DeepSeek</b>\n{safe}"]
    if analysis is not None:
        timing = analysis.entry_timing
        timing_ru = {
            "now": "можно смотреть вход",
            "early": "рано — лучше следить",
            "late": "поздно",
            "skip": "пропуск",
            "unknown": "неясно",
        }.get(timing, timing)
        parts.append(f"\n\n⏱ Timing: <b>{html.escape(timing_ru)}</b>")
        if analysis.watch_if_early or timing == "early":
            parts.append(
                "\n👀 Можно поставить на слежение кнопкой под алертом "
                "(«Следить до входа»)."
            )
    return "".join(parts)


async def analyze_pump_hit_async(hit: ScanHit, alert_text: str) -> str | None:
    return await asyncio.to_thread(analyze_pump_hit_sync, hit, alert_text)


async def analyze_pump_hit_structured_async(
    hit: ScanHit, alert_text: str
) -> PumpDeepseekAnalysis | None:
    return await asyncio.to_thread(analyze_pump_hit_structured_sync, hit, alert_text)


def reeval_entry_watch_sync(
    *,
    symbol: str,
    alert_text: str,
    watch_plan: dict,
    metrics: dict,
    baseline: dict | None = None,
    initial_analysis: str | None = None,
    initial_entry_timing: str | None = None,
    analysis_history: list | None = None,
) -> PumpDeepseekReeval | None:
    if not get_settings().deepseek_ready:
        return None
    hist = analysis_history or []
    if hist:
        lines = ["История повторных оценок (от старых к новым):"]
        for i, item in enumerate(hist[-5:], 1):
            if not isinstance(item, dict):
                continue
            lines.append(
                f"{i}) [{item.get('at', '?')}] timing={item.get('timing')} "
                f"entry_ok={item.get('entry_ok')}\n"
                f"{(item.get('text') or item.get('reason_short') or '')[:1200]}"
            )
        hist_block = "\n".join(lines)
    else:
        hist_block = "История повторных оценок: пока пусто (это первая переоценка)."

    first = (initial_analysis or "").strip()
    if not first:
        first = "(первое заключение не сохранено — опирайся на алерт и метрики)"

    user = "\n".join(
        [
            f"Символ: {symbol}",
            f"Первоначальный timing от тебя: {initial_entry_timing or 'unknown'}",
            "",
            "Исходный алерт:",
            alert_text[:3000],
            "",
            "Твоё ПЕРВОЕ заключение (на момент алерта) — обязательный контекст:",
            first[:4000],
            "",
            hist_block,
            "",
            "План слежения (JSON):",
            json.dumps(watch_plan, ensure_ascii=False),
            "",
            "Метрики на момент постановки на слежение:",
            json.dumps(baseline or {}, ensure_ascii=False, default=str),
            "",
            "Текущие метрики:",
            json.dumps(metrics, ensure_ascii=False, default=str),
            "",
            "Созрело ли окно входа для fade-шорта сейчас? Учти преемственность с первым заключением.",
        ]
    )
    raw = _deepseek_chat_sync(_REEVAL_SYSTEM, user, max_tokens=900)
    if not raw:
        return None
    user_text, _ = parse_deepseek_watch_block(raw)
    entry_ok = False
    timing = "early"
    reason = None
    adjust = None
    blob = None
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.S | re.I)
    if m:
        blob = m.group(1)
        user_text = (raw[: m.start()] + raw[m.end() :]).strip()
    else:
        for m2 in re.finditer(r"\{[^{}]*\"entry_ok\"[^{}]*\}", raw, flags=re.S):
            blob = m2.group(0)
            user_text = (raw[: m2.start()] + raw[m2.end() :]).strip()
    if blob:
        try:
            data = json.loads(blob)
            entry_ok = bool(data.get("entry_ok"))
            timing = str(data.get("timing") or ("now" if entry_ok else "early")).lower()
            reason = data.get("reason_short")
            ap = data.get("adjust_plan")
            if isinstance(ap, dict):
                adjust = normalize_watch_plan(ap)
        except json.JSONDecodeError:
            pass
    return PumpDeepseekReeval(
        text=user_text or raw,
        entry_ok=entry_ok,
        timing=timing,
        reason_short=str(reason).strip() if reason else None,
        adjust_plan=adjust,
    )


async def reeval_entry_watch_async(**kwargs) -> PumpDeepseekReeval | None:
    return await asyncio.to_thread(reeval_entry_watch_sync, **kwargs)
