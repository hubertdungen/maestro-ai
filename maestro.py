#!/usr/bin/env python3
"""
Maestro AI — Orquestrador Central de Agentes do pavilion-nas.
Gere Antigravity (agy), Claude Code (cca/claude), Codex (codex) e DeepSeek CLI (dsh / dsh-term.sh).

Funcionalidades:
1. Deteta em tempo real as quotas, janelas de 5h/7d, dias de reset e saldo (€/$) da API DeepSeek.
2. Calcula o Pace Budget (ritmo sustentável de quinta a quinta no Claude Max).
3. Suporte Total a Imagens (Homepage ttyd + Windows PasteBridge):
   - Deteta caminhos de imagens coladas (/srv/appdata/0_SERVER/pastes/paste-*.png, etc.)
   - O Maestro lê e interpreta as imagens diretamente e reencaminha-as automaticamente
     aos agentes "filhos" (Codex via `-i`, Claude via `--add-dir`, DeepSeek via `read_image`).
4. Failover Automático (Maestro Substituto): se o Antigravity (agy) ficar sem tokens/quota,
   transfere automaticamente o comando para o melhor Maestro Substituto disponível
   (Codex -> Claude Sonnet -> DeepSeek CLI) já com as regras e o contexto partilhado.
5. Autonomia 100% (--dangerously-skip-permissions / never ask approval) em todos os agentes.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import urllib.request
from zoneinfo import ZoneInfo
from maestro_queue import weekly_budget, observe_daily

HOME = pathlib.Path.home()
TZ = ZoneInfo("Europe/Lisbon")
UTC = dt.timezone.utc

MAESTRO_DIR = HOME / "scripts" / "maestro"
STATE_DIR = HOME / ".local" / "state" / "maestro"
RUN_DIR = HOME / ".local" / "run" / "maestro"
HISTORY_LOG = STATE_DIR / "history.jsonl"
SHARED_CONTEXT = STATE_DIR / "shared-context.md"
AGY_COOLDOWN_FILE = STATE_DIR / "agy-cooldown.json"
AGY_QUOTA_SIGNAL_FILE = STATE_DIR / "agy-quota-signal.json"
AGY_LOG_DIR = HOME / ".gemini" / "antigravity-cli" / "log"
DS_BALANCE_CACHE = STATE_DIR / "deepseek-balance.json"
PASTES_DIR = "/srv/appdata/0_SERVER/pastes"

CLAUDE_LIVE = HOME / ".claude" / "limits" / "live.json"
CLAUDE_STATE = HOME / ".claude" / "limits" / "state.json"
CODEX_HISTORY = HOME / ".codex" / "history.jsonl"
DSH_CREDS = HOME / ".dsh" / ".credentials.yaml"

NODE_BIN = HOME / ".local/ai-cli-tools/node/current/bin"
NPM_BIN = HOME / ".local/ai-cli-tools/npm-global/bin"
LOCAL_BIN = HOME / ".local/bin"

IMAGE_RE = re.compile(r"(/[\w./@%+-]+\.(?:png|jpe?g|webp|gif))", re.IGNORECASE)

# Etiquetas canónicas dos motores. Uma só fonte de verdade para o plano, para o
# cartão ao vivo e para o painel de quotas — evita dizer "Antigravity" quando
# quem executa é o DeepSeek (ou "maestro Codex" quando quem trabalha é outro).
ENGINE_DISPLAY = {
    "agy": "Antigravity (Gemini)",
    "codex": "Codex (gpt-6.1-sol)",
    "claude-sonnet": "Claude Sonnet",
    "claude-opus": "Claude Opus",
    "sonnet": "Claude Sonnet",
    "opus": "Claude Opus",
    "deepseek": "DeepSeek Flash CLI",
    "auto": "Auto (decide por tarefa)",
}


def ensure_dirs() -> None:
    for d in (STATE_DIR, RUN_DIR):
        d.mkdir(parents=True, exist_ok=True)
        try:
            d.chmod(0o700)
        except OSError:
            pass


def extract_images(text: str, extra_images: list[str] | None = None) -> list[str]:
    """Deteta todos os caminhos de imagens existentes no prompt (ex.: PasteBridge / ttyd-paste)."""
    found = []
    for img in (extra_images or []):
        if img and os.path.exists(img) and img not in found:
            found.append(img)
    for m in IMAGE_RE.findall(text or ""):
        if os.path.exists(m) and m not in found:
            found.append(m)
    return found


def fmt_reset(ts_or_iso) -> str:
    if not ts_or_iso:
        return "desconhecido"
    try:
        if isinstance(ts_or_iso, (int, float)):
            d = dt.datetime.fromtimestamp(float(ts_or_iso), tz=UTC).astimezone(TZ)
        else:
            d = dt.datetime.fromisoformat(str(ts_or_iso)).astimezone(TZ)
        dias = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]
        now = dt.datetime.now(TZ)
        delta_h = max(0.0, (d - now).total_seconds() / 3600.0)
        if delta_h < 24:
            left = f"faltam {delta_h:.1f}h"
        else:
            left = f"faltam {delta_h / 24:.1f}d"
        return f"{dias[d.weekday()]} {d.strftime('%d/%m %H:%M')} ({left})"
    except Exception:
        return str(ts_or_iso)


def get_claude_quota(force_refresh: bool = False) -> dict:
    """Lê os limites reais de 5h, 7 dias e Fable do Claude Max via OAuth API (com fallback para state.json)."""
    ensure_dirs()
    cache_file = STATE_DIR / "claude-quota-cache.json"
    info = {
        "five_hour_pct": 0.0,
        "five_hour_reset": None,
        "five_hour_reset_fmt": "Sessão inativa (0%)",
        "seven_day_pct": 0.0,
        "seven_day_reset": None,
        "seven_day_reset_fmt": "qui 20:00",
        "fable_pct": 0.0,
        "limited": False,
        "pace_target_pct": 50.0,
        "pace_status": "ok",
        "source": "local",
    }

    now = time.time()
    api_data = None
    if not force_refresh and cache_file.exists():
        try:
            c = json.loads(cache_file.read_text())
            if now - float(c.get("ts") or 0) < 90:
                api_data = c.get("data")
        except Exception:
            pass

    if api_data is None:
        try:
            creds_file = HOME / ".claude" / ".credentials.json"
            if creds_file.exists():
                creds = json.loads(creds_file.read_text())
                tok = (creds.get("claudeAiOauth") or {}).get("accessToken")
                if tok:
                    req = urllib.request.Request(
                        "https://api.anthropic.com/api/oauth/usage",
                        headers={
                            "Authorization": f"Bearer {tok}",
                            "anthropic-beta": "oauth-2025-04-20",
                            "Accept": "application/json",
                            "User-Agent": "claude-code/2.0.0",
                        },
                    )
                    with urllib.request.urlopen(req, timeout=4) as r:
                        api_data = json.loads(r.read().decode("utf-8"))
                    cache_file.write_text(json.dumps({"ts": now, "data": api_data}, ensure_ascii=False))
                    # Sincroniza também ~/.claude/limits/live.json para o ccquota
                    try:
                        fh_api = api_data.get("five_hour") or {}
                        sd_api = api_data.get("seven_day") or {}
                        sd_rst = sd_api.get("resets_at")
                        sd_ts = int(dt.datetime.fromisoformat(sd_rst).timestamp()) if sd_rst else 0
                        fh_rst = fh_api.get("resets_at")
                        fh_ts = int(dt.datetime.fromisoformat(fh_rst).timestamp()) if fh_rst else int(now + 18000)
                        live_sync = {
                            "five_hour": {"used_percentage": float(fh_api.get("utilization") or 0.0), "resets_at": fh_ts},
                            "seven_day": {"used_percentage": float(sd_api.get("utilization") or 0.0), "resets_at": sd_ts},
                            "ts": now,
                        }
                        CLAUDE_LIVE.write_text(json.dumps(live_sync, indent=2))
                    except Exception:
                        pass
        except Exception:
            pass

    if api_data:
        fh = api_data.get("five_hour") or {}
        sd = api_data.get("seven_day") or {}
        info["five_hour_pct"] = float(fh.get("utilization") or 0.0)
        info["five_hour_reset"] = fh.get("resets_at")
        info["seven_day_pct"] = float(sd.get("utilization") or 0.0)
        info["seven_day_reset"] = sd.get("resets_at")
        for lim in (api_data.get("limits") or []):
            scope_model = ((lim.get("scope") or {}).get("model") or {}).get("display_name")
            if scope_model and "fable" in scope_model.lower():
                info["fable_pct"] = float(lim.get("percent") or 0.0)
        if info["seven_day_pct"] >= 100.0 or info["five_hour_pct"] >= 100.0:
            info["limited"] = True
        info["source"] = "oauth_live"
    else:
        try:
            if CLAUDE_LIVE.exists():
                live = json.loads(CLAUDE_LIVE.read_text())
                fh = live.get("five_hour") or {}
                sd = live.get("seven_day") or {}
                if float(fh.get("resets_at") or 0) > now:
                    info["five_hour_pct"] = float(fh.get("used_percentage") or 0.0)
                    info["five_hour_reset"] = fh.get("resets_at")
                if float(sd.get("resets_at") or 0) > now:
                    info["seven_day_pct"] = float(sd.get("used_percentage") or 0.0)
                    info["seven_day_reset"] = sd.get("resets_at")
        except Exception:
            pass

        try:
            if CLAUDE_STATE.exists():
                st = json.loads(CLAUDE_STATE.read_text())
                if st.get("limited"):
                    info["limited"] = True
                    lim_type = (st.get("limit") or {}).get("type")
                    if lim_type in ("seven_day", "weekly"):
                        info["seven_day_pct"] = 100.0
                if not info["seven_day_pct"] and st.get("week_pct") is not None:
                    info["seven_day_pct"] = float(st["week_pct"])
                if not info["seven_day_reset"] and st.get("week_resets_at"):
                    info["seven_day_reset"] = st["week_resets_at"]
        except Exception:
            pass

    resets_at = info["seven_day_reset"]
    if resets_at:
        info["seven_day_reset_fmt"] = fmt_reset(resets_at)
        try:
            if isinstance(resets_at, (int, float)):
                reset_dt = dt.datetime.fromtimestamp(float(resets_at), tz=UTC)
            else:
                reset_dt = dt.datetime.fromisoformat(str(resets_at))
            now_utc = dt.datetime.now(UTC)
            hours_left = max(0.0, min(168.0, (reset_dt - now_utc).total_seconds() / 3600.0))
            hours_elapsed = 168.0 - hours_left
            ideal_pct = min(92.0, (hours_elapsed / 168.0) * 90.0)
            info["pace_target_pct"] = round(ideal_pct, 1)
        except Exception:
            pass

    if info["five_hour_reset"]:
        info["five_hour_reset_fmt"] = fmt_reset(info["five_hour_reset"])
    else:
        info["five_hour_reset_fmt"] = "Começa na 1.ª msg (0%)"

    if info["limited"] or info["seven_day_pct"] >= 95 or info["five_hour_pct"] >= 95:
        info["pace_status"] = "limitado"
    elif info["seven_day_pct"] >= 80 or info["five_hour_pct"] >= 80:
        info["pace_status"] = "critico"
    elif info["seven_day_pct"] > info["pace_target_pct"] + 12:
        info["pace_status"] = "acima_do_ritmo"
    else:
        info["pace_status"] = "ok"

    return info


def get_codex_quota(force_refresh: bool = False) -> dict:
    """Consulta o uso real 5h e 7d do ChatGPT Plus (Codex) via wham/usage (com fallback para history.jsonl)."""
    ensure_dirs()
    now = time.time()
    five_h_ago = now - 5 * 3600
    day_ago = now - 24 * 3600
    msgs_5h = 0
    msgs_24h = 0
    oldest_in_5h = None

    try:
        if CODEX_HISTORY.exists():
            with open(CODEX_HISTORY, errors="replace") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                        ts = float(rec.get("ts") or 0)
                        if ts >= day_ago:
                            msgs_24h += 1
                        if ts >= five_h_ago:
                            msgs_5h += 1
                            if oldest_in_5h is None or ts < oldest_in_5h:
                                oldest_in_5h = ts
                    except Exception:
                        continue
    except Exception:
        pass

    cache_file = STATE_DIR / "codex-quota-cache.json"
    wham = None
    if not force_refresh and cache_file.exists():
        try:
            c = json.loads(cache_file.read_text())
            if now - float(c.get("ts") or 0) < 90:
                wham = c.get("data")
        except Exception:
            pass

    if wham is None:
        try:
            auth_file = HOME / ".codex" / "auth.json"
            if auth_file.exists():
                auth = json.loads(auth_file.read_text())
                tok = (auth.get("tokens") or {}).get("access_token")
                acc = (auth.get("tokens") or {}).get("account_id")
                if tok:
                    req = urllib.request.Request(
                        "https://chatgpt.com/backend-api/wham/usage",
                        headers={
                            "Authorization": f"Bearer {tok}",
                            "ChatGPT-Account-Id": acc or "",
                            "User-Agent": "codex-cli",
                        },
                    )
                    with urllib.request.urlopen(req, timeout=4) as r:
                        wham = json.loads(r.read().decode("utf-8"))
                    cache_file.write_text(json.dumps({"ts": now, "data": wham}, ensure_ascii=False))
        except Exception:
            pass

    if wham and isinstance(wham.get("rate_limit"), dict):
        rl = wham["rate_limit"]
        pw = rl.get("primary_window") or {}
        sw = rl.get("secondary_window") or {}
        est_pct = int(round(float(pw.get("used_percentage") if "used_percentage" in pw else (pw.get("used_percent") or 0))))
        reset_ts = pw.get("reset_at")
        week_value = sw.get("used_percentage", sw.get("used_percent"))
        week_pct = float(week_value) if week_value is not None else 0
        week_reset_ts = sw.get("reset_at")
        daily = observe_daily(STATE_DIR / "codex-daily-usage.json", week_pct if week_value is not None else None, week_reset_ts, now)
        status = "ok" if est_pct < 70 else ("atencao" if est_pct < 95 else "quase_no_limite")
        return {
            "msgs_5h": msgs_5h,
            "msgs_24h": msgs_24h,
            "five_hour_est_pct": est_pct,
            "five_hour_reset": reset_ts,
            "five_hour_reset_fmt": fmt_reset(reset_ts) if reset_ts else "ok",
            "seven_day_pct": week_pct,
            "seven_day_reset": week_reset_ts,
            "seven_day_reset_fmt": fmt_reset(week_reset_ts) if week_reset_ts else "ok",
            "limit_reached": bool(rl.get("limit_reached")),
            "status": status,
            "model": "gpt-6.1-sol",
            "source": "wham_live",
            **daily,
            **weekly_budget({"seven_day_pct": week_pct if week_value is not None else None, "seven_day_reset": week_reset_ts, **daily}, now),
        }

    est_pct = min(100, int(round((msgs_5h / 45.0) * 100)))
    reset_ts = (oldest_in_5h + 5 * 3600) if oldest_in_5h else None
    status = "ok" if est_pct < 70 else ("atencao" if est_pct < 90 else "quase_no_limite")
    return {
        "msgs_5h": msgs_5h,
        "msgs_24h": msgs_24h,
        "five_hour_est_pct": est_pct,
        "five_hour_reset": reset_ts,
        "five_hour_reset_fmt": fmt_reset(reset_ts) if reset_ts else "janela livre",
        "seven_day_pct": 0,
        "seven_day_reset": None,
        "seven_day_reset_fmt": "ok",
        "limit_reached": False,
        "status": status,
        "model": "gpt-6.1-sol",
        "source": "history_est",
        **weekly_budget({}),
    }


def _extract_deepseek_key() -> str | None:
    env_key = os.environ.get("DEEPSEEK_API_KEY")
    if env_key:
        return env_key.strip()
    try:
        if DSH_CREDS.exists():
            raw = DSH_CREDS.read_text(errors="replace")
            m = re.search(r"(sk-[A-Za-z0-9]{20,})", raw)
            if m:
                return m.group(1)
    except Exception:
        pass
    return None


def get_deepseek_balance(force_refresh: bool = False) -> dict:
    import maestro_budget as B
    result = _get_deepseek_balance(force_refresh)
    try:
        budget = B.status()
    except Exception:
        budget = {'available': False, 'cli_allowed': False, 'error': 'Orçamento indisponível'}
    result = dict(result, budget=budget)
    result['available'] = bool(result.get('available') and (budget['available'] or budget.get('reserve_available', False)))
    return result


def _get_deepseek_balance(force_refresh: bool = False) -> dict:
    """Consulta o saldo real da API DeepSeek (com cache de 10 minutos)."""
    ensure_dirs()
    if not force_refresh and DS_BALANCE_CACHE.exists():
        try:
            cached = json.loads(DS_BALANCE_CACHE.read_text())
            if time.time() - float(cached.get("ts") or 0) < 600:
                return cached
        except Exception:
            pass

    key = _extract_deepseek_key()
    if not key:
        return {
            "available": True,
            "balance_text": "~15.00 € (chave gerida no dsh)",
            "source": "estimado",
            "ts": time.time(),
        }

    try:
        req = urllib.request.Request(
            "https://api.deepseek.com/user/balance",
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        infos = data.get("balance_infos") or []
        parts = []
        for b in infos:
            parts.append(f"{b.get('total_balance')} {b.get('currency')}")
        res = {
            "available": bool(data.get("is_available", True)),
            "balance_text": " + ".join(parts) if parts else "ativo",
            "source": "api_live",
            "ts": time.time(),
        }
        DS_BALANCE_CACHE.write_text(json.dumps(res, ensure_ascii=False))
        return res
    except Exception:
        return {
            "available": True,
            "balance_text": "~15.00 € (API ativa)",
            "source": "fallback",
            "ts": time.time(),
        }


# O endpoint OAuth (retrieveUserQuotaSummary) continua a devolver 100% de quota de PROJETO
# mesmo quando a conta já rebentou o limite INDIVIDUAL. O CLI grava o 429 real nos logs:
#   RESOURCE_EXHAUSTED (code 429): Individual quota reached... Resets in 163h16m4s.
# Esta é a fonte de verdade para o painel não mentir "0% usado".
_QUOTA_429_RE = re.compile(
    r"RESOURCE_EXHAUSTED[^\n]*?Individual quota reached[^\n]*?Resets in\s+"
    r"(?:(?P<h>\d+)h)?(?:(?P<m>\d+)m)?(?:(?P<s>\d+(?:\.\d+)?)s)?",
    re.IGNORECASE,
)
_AGY_LOG_TS_RE = re.compile(r"^[A-Z](?P<md>\d{4})\s+(?P<hms>\d{2}:\d{2}:\d{2}(?:\.\d+)?)")
_AGY_SIGNAL_MEM = {"key": None, "value": None, "ts": 0.0}


def _agy_signal_from_logs(max_age_s: float = 8 * 86400) -> dict | None:
    """Lê o 429 'Individual quota reached' mais recente dos logs do Antigravity.

    Devolve {"reset_ts": epoch, "event_ts": epoch, "raw": str, "log_file": str} ou None.
    O reset é calculado como timestamp da linha + 'Resets in Xh Ym Zs'.
    """
    if not AGY_LOG_DIR.exists():
        return None
    now = time.time()
    logs = []
    for p in AGY_LOG_DIR.glob("cli-*.log"):
        try:
            st = p.stat()
            if now - st.st_mtime <= max_age_s:
                logs.append((st.st_mtime, p, st.st_size))
        except OSError:
            continue
    logs.sort(reverse=True)
    # Só os 2 logs mais recentes interessam (o erro repetido fica no ativo + anterior).
    for _mtime, p, size in logs[:2]:
        try:
            with p.open("rb") as fh:
                fh.seek(max(0, size - 262144))
                text = fh.read().decode("utf-8", errors="ignore")
        except OSError:
            continue
        matches = list(_QUOTA_429_RE.finditer(text))
        if not matches:
            continue
        # A data vem do nome do ficheiro (cli-YYYYMMDD_HHMMSS.log), mais fiável que o ano.
        fm = re.search(r"cli-(\d{8})_", p.name)
        if fm:
            y, mo, d = int(fm.group(1)[:4]), int(fm.group(1)[4:6]), int(fm.group(1)[6:8])
        else:
            lt = dt.datetime.fromtimestamp(_mtime, tz=TZ)
            y, mo, d = lt.year, lt.month, lt.day

        candidates = []
        for m in matches:
            line_start = text.rfind("\n", 0, m.start()) + 1
            line_end = text.find("\n", m.end())
            line = text[line_start:(line_end if line_end != -1 else len(text))]
            tm = _AGY_LOG_TS_RE.search(line)
            if not tm:
                continue
            hms = tm.group("hms")
            try:
                hh, mm, ss = hms.split(":")
                sec_f = float(ss)
                sec = int(sec_f)
                micro = int(round((sec_f - sec) * 1_000_000))
                event_dt = dt.datetime(y, mo, d, int(hh), int(mm), sec, micro, tzinfo=TZ)
            except (ValueError, TypeError):
                continue
            if event_dt.timestamp() > now + 86400:
                # Virou o ano (log de dezembro lido em janeiro).
                event_dt = event_dt.replace(year=event_dt.year - 1)
            dur = int(m.group("h") or 0) * 3600 + int(m.group("m") or 0) * 60 + float(m.group("s") or 0)
            if dur <= 0:
                continue
            reset_ts = event_dt.timestamp() + dur
            if reset_ts <= now:
                continue
            # O erro real vem de errorreport.go/agent executor. Evita textos de chat
            # que apenas citam a mensagem de quota.
            is_real = ("errorreport.go" in line) or ("agent executor error" in line)
            candidates.append((is_real, event_dt.timestamp(), {
                "reset_ts": reset_ts,
                "event_ts": event_dt.timestamp(),
                "duration_s": dur,
                "raw": m.group(0).strip(),
                "log_file": p.name,
            }))
        if candidates:
            reals = [c for c in candidates if c[0]]
            pool = reals or candidates
            return max(pool, key=lambda c: c[1])[2]
    return None


def get_agy_quota_signal(force_refresh: bool = False) -> dict | None:
    """Sinal de quota individual esgotada (429 do CLI), com cache curta em memória/disco."""
    ensure_dirs()
    now = time.time()
    cache_key = None
    try:
        st = AGY_LOG_DIR.stat()
        cache_key = (st.st_mtime, max((p.stat().st_mtime for p in AGY_LOG_DIR.glob("cli-*.log")), default=0.0))
    except OSError:
        cache_key = None

    if not force_refresh and _AGY_SIGNAL_MEM["value"] is not None and now - _AGY_SIGNAL_MEM["ts"] < 20:
        if _AGY_SIGNAL_MEM["key"] == cache_key:
            val = _AGY_SIGNAL_MEM["value"]
            return val if val and val.get("reset_ts", 0) > now else None

    sig = _agy_signal_from_logs()
    if sig is None:
        # Fallback: sinal persistido (logs podem ter rodado/limpado).
        try:
            if AGY_QUOTA_SIGNAL_FILE.exists():
                saved = json.loads(AGY_QUOTA_SIGNAL_FILE.read_text())
                if float(saved.get("reset_ts") or 0) > now:
                    sig = saved
        except Exception:
            pass

    if sig:
        try:
            AGY_QUOTA_SIGNAL_FILE.write_text(json.dumps({**sig, "saved_at": now}, ensure_ascii=False))
        except OSError:
            pass

    _AGY_SIGNAL_MEM.update({"key": cache_key, "value": sig, "ts": now})
    return sig if sig and sig.get("reset_ts", 0) > now else None


def get_antigravity_status(force_refresh: bool = False) -> dict:
    """Estado do Antigravity (agy) e quota real do Gemini e modelos 3P via CloudCode API."""
    ensure_dirs()
    agy_bin = LOCAL_BIN / "agy"
    limited = False
    reset_at = None
    if AGY_COOLDOWN_FILE.exists():
        try:
            cd = json.loads(AGY_COOLDOWN_FILE.read_text())
            if float(cd.get("until") or 0) > time.time():
                limited = True
                reset_at = cd.get("until")
            else:
                AGY_COOLDOWN_FILE.unlink(missing_ok=True)
        except Exception:
            pass

    now = time.time()
    cache_file = STATE_DIR / "agy-quota-cache.json"
    quota_data = None
    if not force_refresh and cache_file.exists():
        try:
            c = json.loads(cache_file.read_text())
            if now - float(c.get("ts") or 0) < 90:
                quota_data = c.get("data")
        except Exception:
            pass

    if quota_data is None:
        try:
            tok_file = HOME / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
            proj_file = HOME / ".gemini" / "antigravity-cli" / "cache" / "default_project_id.txt"
            if tok_file.exists() and proj_file.exists():
                raw_tok = json.loads(tok_file.read_text())
                token = (raw_tok.get("token") or {}).get("access_token")
                proj = proj_file.read_text().strip()
                if token and proj:
                    headers = {
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                        "User-Agent": "antigravity/1.104.0 linux/amd64",
                    }
                    payload = json.dumps({"project": proj}).encode("utf-8")
                    req_m = urllib.request.Request(
                        "https://daily-cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels",
                        data=payload, headers=headers, method="POST"
                    )
                    with urllib.request.urlopen(req_m, timeout=4) as rm:
                        models_resp = json.loads(rm.read().decode("utf-8"))

                    req_q = urllib.request.Request(
                        "https://daily-cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary",
                        data=payload, headers=headers, method="POST"
                    )
                    with urllib.request.urlopen(req_q, timeout=4) as rq:
                        summary_resp = json.loads(rq.read().decode("utf-8"))

                    quota_data = {"models": models_resp.get("models") or {}, "summary": summary_resp}
                    cache_file.write_text(json.dumps({"ts": now, "data": quota_data}, ensure_ascii=False))
        except Exception:
            pass

    gemini_rem_frac = 1.0
    gemini_reset = None
    m3p_5h_rem = 1.0
    m3p_7d_rem = 1.0
    m3p_reset = None

    if quota_data:
        models = quota_data.get("models") or {}
        for mk, mv in models.items():
            if mk.startswith("gemini-"):
                qi = mv.get("quotaInfo") or {}
                if "remainingFraction" in qi:
                    gemini_rem_frac = min(gemini_rem_frac, float(qi["remainingFraction"]))
                if qi.get("resetTime") and not gemini_reset:
                    gemini_reset = qi["resetTime"]
        for grp in ((quota_data.get("summary") or {}).get("groups") or []):
            for b in (grp.get("buckets") or []):
                bid = b.get("bucketId") or ""
                if bid == "3p-5h":
                    m3p_5h_rem = float(b.get("remainingFraction", 1.0))
                    m3p_reset = b.get("resetTime")
                elif bid == "3p-weekly":
                    m3p_7d_rem = float(b.get("remainingFraction", 1.0))

    gemini_used_pct = round(max(0.0, (1.0 - gemini_rem_frac) * 100.0), 1)
    m3p_5h_used_pct = round(max(0.0, (1.0 - m3p_5h_rem) * 100.0), 1)
    m3p_7d_used_pct = round(max(0.0, (1.0 - m3p_7d_rem) * 100.0), 1)
    if gemini_used_pct >= 100.0:
        limited = True

    # --- Fonte de verdade: 429 "Individual quota reached" do CLI ---
    # A API OAuth reporta a quota do PROJETO e pode continuar a dizer 100% livre
    # enquanto a conta já está bloqueada. O log do agy é o que o utilizador vê.
    signal = get_agy_quota_signal()
    quota_source = "oauth_plan" if quota_data else "unknown"
    quota_note = ""
    quota_unknown = quota_data is None

    if signal:
        limited = True
        gemini_used_pct = 100.0
        reset_at = signal["reset_ts"]
        gemini_reset = dt.datetime.fromtimestamp(signal["reset_ts"], tz=UTC).isoformat()
        quota_source = "cli_429"
        quota_note = (
            "Limite individual do Antigravity atingido (429 no CLI). "
            f"A API OAuth ainda reporta quota de projeto livre; reset real em {fmt_reset(signal['reset_ts'])}."
        )
    elif limited and reset_at:
        quota_source = "cooldown"
        quota_note = "Antigravity em cooldown ativo; o Maestro usa o substituto disponível."
    elif quota_unknown:
        quota_note = "Não foi possível ler a quota OAuth do Antigravity; estado mostrado é conservador."

    # Normaliza o reset para epoch e persiste o cooldown para os restantes componentes.
    reset_epoch = None
    for cand in (reset_at, gemini_reset):
        if cand is None:
            continue
        try:
            if isinstance(cand, (int, float)):
                reset_epoch = float(cand)
            else:
                reset_epoch = dt.datetime.fromisoformat(str(cand)).timestamp()
            break
        except Exception:
            continue

    if limited and reset_epoch and reset_epoch > now:
        try:
            prev = {}
            if AGY_COOLDOWN_FILE.exists():
                prev = json.loads(AGY_COOLDOWN_FILE.read_text())
            if abs(float(prev.get("until") or 0) - reset_epoch) > 60 or prev.get("source") != quota_source:
                AGY_COOLDOWN_FILE.write_text(json.dumps({
                    "until": reset_epoch,
                    "reason": quota_note or "Quota individual do Antigravity esgotada.",
                    "source": quota_source,
                    "updated_at": now,
                }, ensure_ascii=False))
        except Exception:
            pass
    elif not limited:
        try:
            AGY_COOLDOWN_FILE.unlink(missing_ok=True)
        except Exception:
            pass

    return {
        "installed": agy_bin.exists(),
        "limited": limited,
        "reset_at": reset_epoch or reset_at or gemini_reset,
        "gemini_used_pct": gemini_used_pct,
        "gemini_remaining_pct": round(100.0 - gemini_used_pct, 1),
        "gemini_reset": gemini_reset,
        "gemini_reset_fmt": fmt_reset(reset_epoch or gemini_reset) if (reset_epoch or gemini_reset) else "janela livre",
        "models_3p_5h_used_pct": m3p_5h_used_pct,
        "models_3p_7d_used_pct": m3p_7d_used_pct,
        "models_3p_reset_fmt": fmt_reset(m3p_reset) if m3p_reset else "janela livre",
        "quota_source": quota_source,
        "quota_note": quota_note,
        "quota_unknown": quota_unknown,
        "quota_signal_at": signal.get("event_ts") if signal else None,
        "quota_signal_raw": signal.get("raw") if signal else "",
        "primary_model": "Gemini 4 Argon / 3.8 Flash",
        "extra_models": ["claude-sonnet-5-5", "claude-opus-5-5", "gpt-oss-120b"],
        "cost": "Incluído no plano (0€ extra por token)",
        "status": "limitado" if limited else "ok",
    }


def choose_active_maestro(force_substitute: bool = False) -> dict:
    """
    Seleciona quem assume o papel de Maestro:
    1. Antigravity (agy) — Maestro Principal
    2. Codex (gpt-6.1-sol) — 1.º Maestro Substituto (se janela 5h < 85%)
    3. Claude Sonnet (cca @sonnet) — 2.º Maestro Substituto (se não estiver limitado)
    4. DeepSeek CLI (dsh-term.sh / dsh headless) — 3.º Maestro Substituto (sempre disponível via API)
    """
    agy = get_antigravity_status()
    codex = get_codex_quota()
    claude = get_claude_quota()

    if not force_substitute and agy["installed"] and not agy["limited"]:
        return {
            "engine": "agy",
            "name": "Antigravity (Maestro Principal)",
            "is_substitute": False,
            "reason": "Ativo, autónomo e com quota disponível.",
        }
    if codex["five_hour_est_pct"] < 85 and not codex.get("pace_blocked", True):
        return {
            "engine": "codex",
            "name": "Codex CLI (Maestro Substituto #1)",
            "is_substitute": True,
            "reason": f"Antigravity indisponível; Codex com janela 5h livre (~{codex['five_hour_est_pct']}%).",
        }
    if claude["pace_status"] != "limitado" and claude["five_hour_pct"] < 85:
        return {
            "engine": "claude-sonnet",
            "name": "Claude Sonnet (Maestro Substituto #2)",
            "is_substitute": True,
            "reason": "Antigravity/Codex ocupados; Claude Sonnet (peso 0.2x) assume o comando.",
        }
    return {
        "engine": "deepseek",
        "name": "DeepSeek CLI · orçamento controlado",
        "is_substitute": True,
        "reason": "Subscrições em limite; DeepSeek Flash executa pelo gateway com orçamento diário e teto de 15 EUR/ciclo.",
    }


def update_shared_context(last_prompt: str | None = None, plan: dict | None = None) -> None:
    """Atualiza ~/.local/state/maestro/shared-context.md para continuidade entre Maestro e Substitutos."""
    ensure_dirs()
    c = get_claude_quota()
    x = get_codex_quota()
    d = get_deepseek_balance()
    m = choose_active_maestro()

    recent_runs = []
    if HISTORY_LOG.exists():
        try:
            lines = HISTORY_LOG.read_text(errors="replace").strip().splitlines()[-8:]
            for ln in lines:
                r = json.loads(ln)
                ts_str = dt.datetime.fromtimestamp(r["ts"], tz=TZ).strftime("%d/%m %H:%M")
                imgs = r.get("images") or []
                img_tag = f" [imagens: {', '.join(imgs)}]" if imgs else ""
                recent_runs.append(
                    f"- [{ts_str}] Worker `{r.get('worker')}` em `{r.get('cwd')}` ({r.get('elapsed_s')}s, rc={r.get('rc')}){img_tag}: {r.get('prompt_preview')}"
                )
        except Exception:
            pass

    now_str = dt.datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    lines = [
        "# Estado & Contexto Partilhado do Maestro AI (`pavilion-nas`)",
        f"Atualizado em: {now_str}",
        f"Maestro Ativo: **{m['name']}** ({m['reason']})",
        "",
        "## Estado Atual das Contas e Quotas",
        f"- **Claude Max**: 5h={c['five_hour_pct']:.0f}% | 7d={c['seven_day_pct']:.0f}% (Ritmo ideal hoje: ≤{c['pace_target_pct']:.0f}%, estado: `{c['pace_status']}`, reset: {fmt_reset(c['seven_day_reset'])})",
        f"- **Codex (ChatGPT Plus)**: 5h=~{x['five_hour_est_pct']}% ({x['msgs_5h']} turnos/5h, reset: {fmt_reset(x['five_hour_reset'])})",
        f"- **DeepSeek CLI/API**: Saldo `{d['balance_text']}`",
        "",
    ]
    if last_prompt and plan:
        lines.extend([
            "## Último Pedido Recebido pelo Maestro",
            f"- **Domínio**: {plan['domain']} | **Dimensão**: {plan['dimension']}",
            f"- **Pipeline escolhido**: {' -> '.join(plan['agents'])}",
            f"- **Motor efetivo**: {plan.get('exec_engine_name') or plan.get('recommended_engine') or '?'} "
            f"(origem: {plan.get('engine_origin') or 'task_route'}; maestro de serviço: {m['name']})",
            f"- **Imagens detetadas**: {', '.join(plan.get('images') or []) or 'nenhuma'}",
            f"- **Resumo do Pedido**: {last_prompt[:500]}",
            "",
        ])
    if recent_runs:
        lines.append("## Histórico Recente de Execuções / Sub-tarefas")
        lines.extend(recent_runs)
        lines.append("")

    SHARED_CONTEXT.write_text("\n".join(lines), encoding="utf-8")


def classify_task(prompt: str, cwd: str | None = None, extra_images: list[str] | None = None) -> dict:
    """Mede a dimensão, deteta o domínio, imagens coladas e decide o pipeline ideal de agente(s)."""
    p_low = prompt.lower()
    cwd_low = (cwd or os.getcwd()).lower()
    claude_q = get_claude_quota()
    codex_q = get_codex_quota()
    ds_q = get_deepseek_balance()
    images = extract_images(prompt, extra_images)

    if any(k in p_low or k in cwd_low for k in ("polistrat", "unity", "mono", "prefab", "shader", "csharp", "c#")):
        domain = "Polistrat (Unity / C#)"
        domain_key = "unity"
    elif any(k in p_low or k in cwd_low for k in ("spacebourne", "modding", "unreal", "uasset", "pak", "utoc", "ue4ss", "fmodel")):
        domain = "Modding (SpaceBourne 2 / Unreal)"
        domain_key = "modding"
    elif any(k in p_low or k in cwd_low for k in ("dungenlabs", "portfolio", "webfolio", "grimorio", "lectoria", "blogartifex", "css", "html", "layout", "design")):
        domain = "Web & Design (DungenLabs / Portfólio)"
        domain_key = "web"
    else:
        domain = "Pavilion-NAS (Sistema / Pulso / Docker)"
        domain_key = "nas"

    word_count = len(prompt.split())
    is_batch = any(k in p_low for k in ("todos os ficheiros", "centenas", "em massa", "batch", "traduzir tudo", "converter todos"))
    is_arch = any(k in p_low for k in (
        "arquitetura", "arquitectura", "refactor grande", "sistema novo",
        "estratégia", "multiplos sistemas", "vários alertas", "alertas estão abertos",
        "desenhar", "reescrever", "complexo"
    )) or word_count > 120

    if is_batch:
        dimension = "Batch / Processamento em Massa"
        dim_level = 4
    elif is_arch:
        dimension = "Grande (Multi-Agente: Scout -> Especialista -> Revisão)"
        dim_level = 3
    elif word_count > 25 or images or any(k in p_low for k in ("bug", "erro", "corrigir", "criar", "implementar", "investiga")):
        dimension = "Média (Execução Cirúrgica)"
        dim_level = 2
    else:
        dimension = "Rápida / Direta"
        dim_level = 1

    claude_ok_sonnet = claude_q["pace_status"] != "limitado" and claude_q["five_hour_pct"] < 90 and claude_q.get("seven_day_pct", 0) < 100
    claude_ok_opus = claude_q["pace_status"] == "ok" and claude_q["five_hour_pct"] < 65 and claude_q.get("seven_day_pct", 0) < 88
    codex_ok = codex_q["five_hour_est_pct"] < 92 and codex_q.get("seven_day_pct", 0) < 100
    ag_q = get_antigravity_status()
    gemini_ok = not ag_q.get("limited", False)
    ds_ok = bool(ds_q.get("available", True))

    available_engines = []
    if gemini_ok:
        available_engines.append("agy")
    if claude_ok_sonnet:
        available_engines.append("claude-sonnet")
    if codex_ok:
        available_engines.append("codex")
    if ds_ok:
        available_engines.append("deepseek")

    two_card_mode = (not claude_ok_sonnet and not codex_ok and gemini_ok and ds_ok)
    needs_vision = bool(images) or any(k in p_low for k in ("screenshot", "imagem", "print", "foto", "ecrã"))
    needs_search = any(k in p_low for k in ("pesquisa", "procura na web", "search", "notícias", "news", "online", "última versão", "release", "documentação"))
    needs_workspace_tools = any(k in p_low for k in ("docker", "container", "pulso", "reinicia", "logs", "edita", "corrige", "altera", "ficheiro", "config", "radarr", "sonarr"))
    is_trivial_isolated = (
        dim_level == 1
        and not needs_vision
        and not needs_search
        and not needs_workspace_tools
        and word_count <= 36
    )

    agents = []
    reason = ""
    multi_agent = False
    # Fallback imediato: se o Antigravity estiver em 429, quem manda é o
    # Maestro Substituto da hierarquia (Codex → Claude Sonnet → DeepSeek),
    # nunca um motor escolhido por atalho que contradiga o painel.
    maestro_now = choose_active_maestro()
    recommended_engine = "agy" if gemini_ok else maestro_now["engine"]

    if two_card_mode:
        if is_trivial_isolated:
            recommended_engine = "deepseek"
            agents = ["DeepSeek Flash CLI (Tarefa Rápida / Isolada — $0.001)"]
            reason = (
                "🃏 Gestão 2 Cartas (Gemini + DeepSeek): Como Claude Max (7d=100%) e Codex (5h=100%) estão em pausa, "
                "esta tarefa rápida/isolada sem visão nem web search é executada pelo DeepSeek V3.2 para resposta imediata e poupança total da janela do Gemini."
            )
        elif needs_search or needs_vision:
            recommended_engine = "agy"
            agents = ["Antigravity · Gemini (Visão Multimodal + Pesquisa Web Nativa)"]
            if dim_level >= 3:
                agents.append("DeepSeek Flash CLI (Apoio em Scripts/Processamento)")
                multi_agent = True
            reason = (
                "🃏 Gestão 2 Cartas (Gemini + DeepSeek): O Gemini lidera tarefas de Pesquisa Web (search_web), "
                "Visão Multimodal e Inspeção de Sistema; o DeepSeek apoia na geração de código/scripts."
            )
        elif dim_level >= 2:
            recommended_engine = "agy"
            agents = [
                "Antigravity · Gemini (Scout, Pesquisa & Orquestração)",
                "DeepSeek Flash CLI (Especialista Cirúrgico Código/Scripts/Batch)",
            ]
            multi_agent = True
            reason = (
                "🃏 Gestão 2 Cartas (Gemini + DeepSeek): Com Claude e Codex a 100%, o Gemini faz o Scout/Pesquisa/Contexto "
                "e combina forças com o DeepSeek V3.2 para geração rápida de código e scripts."
            )
        else:
            recommended_engine = "agy"
            agents = ["Antigravity · Gemini (Execução Direta no Workspace)"]
            reason = "🃏 Gestão 2 Cartas (Gemini + DeepSeek): Gemini executa diretamente a ação no workspace do NAS."
    elif dim_level == 1:
        agents = ["Antigravity (Gemini 3.8 Flash / 4 Argon)"]
        reason = "Tarefa rápida: resolvida diretamente pelo Maestro (Antigravity) a custo 0 das quotas semanais."
    elif dim_level == 4:
        agents = ["Antigravity (Scout/Validação)", "DeepSeek CLI Headless (Execução Batch API)"]
        multi_agent = True
        reason = "Processamento repetitivo/em massa: Antigravity prepara a estrutura e o DeepSeek CLI executa por cêntimos."
    elif domain_key == "modding":
        if dim_level >= 3 and codex_ok:
            agents = ["Antigravity (Scout Web + Leitura Dumps UE)", "Codex (Engenharia Reversa / Scripts)"]
            multi_agent = True
            reason = "Modding SpaceBourne 2: Antigravity pesquisa na web e lê tabelas grandes; Codex escreve a lógica técnica/binária."
        else:
            agents = ["Antigravity (Gemini 4 Argon)", "DeepSeek CLI (Apoio de Scripts/Tabelas)"]
            multi_agent = True
            reason = "Modding: Antigravity analisa ficheiros e web; DeepSeek CLI apoia na geração de scripts/dados."
    elif domain_key == "web":
        if claude_ok_sonnet:
            agents = ["Antigravity (Scout de Ficheiros/Visão)", "Claude Sonnet (UI/UX & Código Frontend - peso 0.2x)"]
            multi_agent = True
            reason = "Site/DungenLabs: Antigravity inspeciona ficheiros/imagens a custo 0 e Claude Sonnet aplica o design/prosa (5x mais económico que Opus)."
        else:
            agents = ["Antigravity (Gemini 4 Argon)"]
            reason = "Quota do Claude Code em poupança/limite: Maestro assume diretamente mantendo qualidade visual."
    elif domain_key == "unity":
        if dim_level >= 3 and claude_ok_opus:
            agents = ["Antigravity (Scout C# / Unity Scene)", "Claude Opus (Arquitetura Core - peso 1.0x)", "Antigravity (Verificação Final)"]
            multi_agent = True
            reason = "Polistrat (Arquitetura Complexa): Antigravity filtra os scripts C# antes para o Opus gastar 80% menos tokens."
        elif claude_ok_sonnet:
            agents = ["Antigravity (Scout C#)", "Claude Sonnet (Implementação C# - peso 0.2x)"]
            multi_agent = True
            reason = "Polistrat (Unity): Antigravity recolhe o contexto e Claude Sonnet escreve o C# (poupando 80% da quota semanal face ao Opus)."
        elif codex_ok:
            agents = ["Antigravity (Scout C#)", "Codex (gpt-6.1-sol)"]
            multi_agent = True
            reason = "Claude Max em proteção de quota: Antigravity + Codex asseguram lógica C# rigorosa no Polistrat."
        else:
            agents = ["Antigravity (Gemini 4 Argon)", "DeepSeek CLI (Apoio C#)"]
            multi_agent = True
            reason = "Claude e Codex em poupança: Maestro + DeepSeek CLI executam."
    else:
        if dim_level >= 3 and codex_ok:
            agents = ["Antigravity (Leitura de Logs e Estado)", "Codex (Diagnóstico Linux/Docker Cirúrgico)"]
            multi_agent = True
            reason = "Pulso/NAS complexo: Antigravity lê os logs pesados a custo 0 e Codex valida a alteração crítica no sistema."
        else:
            agents = ["Antigravity (Gemini 4 Argon)"]
            reason = "Gestão NAS/Pulso: Antigravity analisa logs, imagens e serviços diretamente sem gastar a janela semanal do Claude Max."

    # ── Coerência final: um único motor efetivo, anunciado em todo o lado ──
    # O cartão ao vivo, o prompt enviado ao agente, o contexto partilhado e o
    # painel de quotas passam todos a ler estes campos. Se o Antigravity está
    # em 429, nenhum deles pode continuar a dizer "Antigravity".
    runnable = []
    if gemini_ok:
        runnable.append("agy")
    if claude_ok_sonnet:
        runnable.append("claude-sonnet")
    if codex_ok:
        runnable.append("codex")
    if ds_ok:
        runnable.append("deepseek")

    # A hierarquia escolhe o gestor; a especialidade escolhe o executor.
    # Antes, as listas de agentes sugeriam Claude/Codex mas recommended_engine
    # permanecia no Maestro, tornando a especialização apenas descritiva.
    if not two_card_mode and dim_level >= 2:
        specialist = None
        if is_batch and ds_ok and not needs_vision:
            specialist = "deepseek"
        elif domain_key == "web" and claude_ok_sonnet:
            specialist = "claude-sonnet"
        elif domain_key == "unity":
            specialist = ("claude-opus" if dim_level >= 3 and claude_ok_opus else
                          "claude-sonnet" if claude_ok_sonnet else "codex" if codex_ok else None)
        elif domain_key in ("nas", "modding") and codex_ok:
            specialist = "codex"
        if specialist:
            recommended_engine = specialist
            # Opus shares Claude admission, but retains its stricter pace gate.
            if specialist == "claude-opus" and specialist not in runnable:
                runnable.append(specialist)
            reason = (f"Executor escolhido pela especialidade de {domain}: "
                      f"{ENGINE_DISPLAY.get(specialist, specialist)}. "
                      "O Maestro disponível gere a distribuição; o plano validado define os agentes que vão efetivamente executar.")
            agents = [ENGINE_DISPLAY.get(specialist, specialist)]

    engine_origin = "task_route"
    if recommended_engine == "auto":
        recommended_engine = maestro_now["engine"]
        engine_origin = "maestro_hierarchy"
    if recommended_engine not in runnable:
        fallback = (
            maestro_now["engine"]
            if (maestro_now["engine"] in runnable or not runnable)
            else runnable[0]
        )
        if fallback != recommended_engine:
            engine_origin = "failover"
        recommended_engine = fallback
    if recommended_engine == maestro_now["engine"] and engine_origin == "task_route":
        engine_origin = "maestro_hierarchy"

    engine_label = ENGINE_DISPLAY.get(recommended_engine, recommended_engine)
    if not gemini_ok:
        kept = [a for a in agents if "antigravity" not in a.lower() and "gemini" not in a.lower()]
        has_engine = any(
            ("deepseek" in a.lower() and recommended_engine == "deepseek")
            or ("codex" in a.lower() and recommended_engine == "codex")
            or ("claude" in a.lower() and recommended_engine.startswith("claude"))
            for a in kept
        )
        agents = list(dict.fromkeys(([engine_label] if not has_engine else []) + kept))
        reason = reason.replace(
            "resolvida diretamente pelo Maestro (Antigravity)",
            f"resolvida diretamente por {engine_label}",
        )
        reason = reason.replace("Antigravity (Gemini 4 Argon)", engine_label)
        reason = reason.replace("Antigravity (Gemini 3.8 Flash / 4 Argon)", engine_label)
        reason = reason.replace("Antigravity", engine_label)
        if "Motor efetivo" not in reason:
            if engine_origin == "task_route":
                _maestro_short = str(maestro_now.get("name") or "Maestro").split(" (")[0]
                reason += (
                    f" Motor efetivo: {engine_label} (seleção automática por regras; "
                    f"{_maestro_short} disponível na hierarquia de substituição)."
                )
            else:
                reason += f" Antigravity em 429 — motor efetivo: {engine_label}."
    elif engine_origin == "task_route" and recommended_engine != "agy":
        _maestro_short = str(maestro_now.get("name") or "Maestro").split(" (")[0]
        reason += f" Seleção automática por regras: {engine_label}; {_maestro_short} disponível na hierarquia de substituição."

    return {
        "domain": domain,
        "domain_key": domain_key,
        "dimension": dimension,
        "dim_level": dim_level,
        "multi_agent": multi_agent,
        "agents": agents,
        "recommended_engine": recommended_engine,
        "available_engines": available_engines,
        "two_card_mode": two_card_mode,
        "images": images,
        "reason": reason,
        "exec_engine": recommended_engine,
        "exec_engine_name": engine_label,
        "engine_origin": engine_origin,
        "maestro_engine": maestro_now["engine"],
        "maestro_name": maestro_now["name"],
        "quotas": {
            "antigravity": ag_q,
            "claude": claude_q,
            "codex": codex_q,
            "deepseek": ds_q,
        },
    }


def record_agent_history(worker: str, prompt_preview: str, cwd: str = "", elapsed_s: float = 0.0, rc: int = 0, chat_id: str = "", tools: list[str] | None = None, summary: str = "") -> None:
    """Regista a atividade de qualquer agente no histórico unificado para inspeção na tab Frota."""
    ensure_dirs()
    try:
        entry = {
            "ts": time.time(),
            "time_fmt": dt.datetime.now(TZ).strftime("%d/%m %H:%M:%S"),
            "worker": worker,
            "cwd": cwd or str(HOME),
            "elapsed_s": round(elapsed_s, 1),
            "rc": rc,
            "chat_id": chat_id,
            "prompt_preview": (prompt_preview or "")[:200],
            "summary": (summary or "")[:260],
            "tools": (tools or [])[:6],
        }
        with open(HISTORY_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def get_agent_fleet_summary(active_jobs: dict | None = None) -> dict:
    """Devolve o estado de cada agente da frota, tarefas ativas no momento e histórico individual resumido."""
    ensure_dirs()
    ag_q = get_antigravity_status()
    cl_q = get_claude_quota()
    cx_q = get_codex_quota()
    ds_q = get_deepseek_balance()

    def _norm_key(w: str) -> str:
        wl = (w or "").lower()
        if "deepseek" in wl or "dsh" in wl:
            return "deepseek"
        if "codex" in wl or "gpt" in wl:
            return "codex"
        if "claude" in wl or "sonnet" in wl or "opus" in wl or "haiku" in wl:
            return "claude"
        return "agy"

    history_by_agent: dict[str, list[dict]] = {"agy": [], "claude": [], "codex": [], "deepseek": []}
    all_recent: list[dict] = []

    if HISTORY_LOG.exists():
        try:
            lines = HISTORY_LOG.read_text(encoding="utf-8", errors="ignore").strip().splitlines()[-80:]
            for ln in reversed(lines):
                if not ln.strip():
                    continue
                d = json.loads(ln)
                k = _norm_key(str(d.get("worker") or "agy"))
                if not d.get("time_fmt") and d.get("ts"):
                    d["time_fmt"] = dt.datetime.fromtimestamp(float(d["ts"]), TZ).strftime("%d/%m %H:%M:%S")
                if len(history_by_agent[k]) < 15:
                    history_by_agent[k].append(d)
                if len(all_recent) < 25:
                    d_copy = dict(d)
                    d_copy["agent_key"] = k
                    all_recent.append(d_copy)
        except Exception:
            pass

    active_by_agent: dict[str, list[dict]] = {"agy": [], "claude": [], "codex": [], "deepseek": []}
    for cid, j in (active_jobs or {}).items():
        if j.get("status") == "running":
            k = _norm_key(str(j.get("agent_label") or "agy"))
            active_by_agent[k].append({
                "chat_id": j.get("chat_id") or cid,
                "task_id": j.get("task_id"),
                "agent_label": j.get("agent_label", "Maestro AI"),
                "current_step": j.get("current_step", "Em execução…"),
                "steps": (j.get("steps") or [])[-6:],
                "elapsed_s": round(time.time() - j.get("t0", time.time()), 1),
                "domain": (j.get("plan") or {}).get("domain", ""),
            })

    fleet = [
        {
            "key": "agy",
            "name": "Antigravity · Gemini",
            "icon": "♊",
            "role": "Maestro Principal · Scout, Visão Multimodal & Pesquisa Web Nativa",
            "strengths": ["Pesquisa Web (search_web)", "Visão Multimodal (Prints)", "Janela Contexto 1M+", "Orquestração Multi-Ficheiro"],
            "available": not ag_q.get("limited", False),
            "quota_label": f"{ag_q.get('gemini_used_pct', 0):.1f}% usado (reset {ag_q.get('gemini_reset_fmt', 'livre')})",
            "active_tasks": active_by_agent["agy"],
            "history": history_by_agent["agy"],
        },
        {
            "key": "deepseek",
            "name": "DeepSeek API · orçamento controlado",
            "icon": "🔮",
            "role": "Scripts e implementação · teto 15 EUR/ciclo · reset dia 20",
            "strengths": ["Pesquisa delimitada", "Leitura de ficheiros", "Orçamento diário persistente", "CLI com custo medido por chamada"],
            "available": bool(ds_q.get("available", True)),
            "quota_label": f"Saldo {ds_q.get('balance_text', '14.68 USD')}",
            "active_tasks": active_by_agent["deepseek"],
            "history": history_by_agent["deepseek"],
        },
        {
            "key": "claude",
            "name": "Claude Max (Sonnet / Opus)",
            "icon": "🎭",
            "role": "Arquitetura C# (Polistrat), UI/UX Refinado & Refactoring Profundo",
            "strengths": ["Arquitetura Unity C#", "Design UI/UX & Copywriting", "Refactors multi-módulo"],
            "available": cl_q.get("seven_day_pct", 0) < 100 and cl_q.get("five_hour_pct", 0) < 100,
            "quota_label": f"5h: {cl_q.get('five_hour_pct', 0):.0f}% · 7d: {cl_q.get('seven_day_pct', 0):.0f}% ({cl_q.get('seven_day_reset_fmt', '')})",
            "active_tasks": active_by_agent["claude"],
            "history": history_by_agent["claude"],
        },
        {
            "key": "codex",
            "name": "OpenAI Codex (gpt-6.1-sol)",
            "icon": "⚙️",
            "role": "Engenharia Reversa, Diagnóstico Linux/Docker & Debugging Estrito",
            "strengths": ["Modding SpaceBourne 2", "Diagnóstico Docker/Kernel", "Patching cirúrgico"],
            "available": cx_q.get("five_hour_est_pct", 0) < 100 and cx_q.get("seven_day_pct", 0) < 100,
            "quota_label": f"5h: {cx_q.get('five_hour_est_pct', 0)}% · 7d: {cx_q.get('seven_day_pct', 0)}% ({cx_q.get('five_hour_reset_fmt', '')})",
            "active_tasks": active_by_agent["codex"],
            "history": history_by_agent["codex"],
        },
    ]
    avail_names = [f["name"] for f in fleet if f["available"]]
    return {
        "fleet": fleet,
        "available_count": len(avail_names),
        "available_names": avail_names,
        "recent_all": all_recent,
    }


def cleanup_resources(verbose: bool = True) -> dict:
    """Limpa sessões temporárias do Pulso (pv-ask-*), ficheiros antigos e caches."""
    ensure_dirs()
    cleaned_sessions = []
    cleaned_files = 0

    try:
        r = subprocess.run(
            ["tmux", "-L", "pvterm", "ls", "-F", "#{session_name} #{session_created} #{session_attached}"],
            capture_output=True, text=True, timeout=5
        )
        now = time.time()
        for line in r.stdout.strip().splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[0].startswith("pv-ask-"):
                sname, created, attached = parts[0], int(parts[1]), int(parts[2])
                if attached == 0 and (now - created) > 3600:
                    subprocess.run(["tmux", "-L", "pvterm", "kill-session", "-t", sname], capture_output=True)
                    cleaned_sessions.append(sname)
    except Exception:
        pass

    now = time.time()
    for p in RUN_DIR.glob("*"):
        try:
            if now - p.stat().st_mtime > 86400:
                p.unlink()
                cleaned_files += 1
        except Exception:
            pass

    if verbose:
        print(f"✓ Limpeza Maestro concluída: {len(cleaned_sessions)} sessões temporárias encerradas, {cleaned_files} ficheiros temp removidos.")
    return {"cleaned_sessions": cleaned_sessions, "cleaned_files": cleaned_files}


def print_dashboard(plan: dict | None = None, force_substitute: bool = False) -> None:
    c = get_claude_quota()
    x = get_codex_quota()
    d = get_deepseek_balance()
    a = get_antigravity_status()
    m = choose_active_maestro(force_substitute=force_substitute)

    C_CYAN = "\033[96m"
    C_GREEN = "\033[92m"
    C_AMBER = "\033[93m"
    C_RED = "\033[91m"
    C_VIOLET = "\033[95m"
    C_BOLD = "\033[1m"
    C_DIM = "\033[2m"
    C_RESET = "\033[0m"

    c_color = C_RED if c["pace_status"] in ("limitado", "critico") else (C_AMBER if c["pace_status"] == "acima_do_ritmo" else C_GREEN)
    x_color = C_RED if x["five_hour_est_pct"] >= 90 else (C_AMBER if x["five_hour_est_pct"] >= 70 else C_GREEN)
    a_color = C_RED if a["limited"] else C_GREEN

    print(f"\n{C_CYAN}{C_BOLD}╔══════════════════════════════════════════════════════════════════════════════╗{C_RESET}")
    print(f"{C_CYAN}{C_BOLD}║  ⚡ MAESTRO AI · ORQUESTRADOR CENTRAL (PAVILION-NAS)                         ║{C_RESET}")
    print(f"{C_CYAN}{C_BOLD}╚══════════════════════════════════════════════════════════════════════════════╝{C_RESET}")
    print(f"  {C_BOLD}• Maestro em Comando{C_RESET}     : {C_GREEN if not m['is_substitute'] else C_AMBER}{m['name']}{C_RESET} {C_DIM}({m['reason']}){C_RESET}")
    print(
        f"  {C_BOLD}1. Gemini / Antigravity{C_RESET}  : "
        f"Gemini: {a_color}{a.get('gemini_used_pct', 0):.1f}% usado{C_RESET} "
        f"(reset: {a.get('gemini_reset_fmt', 'livre')}) · "
        f"3P (Claude 5.5/GPT): 5h={a.get('models_3p_5h_used_pct', 0):.0f}% / 7d={a.get('models_3p_7d_used_pct', 0):.0f}%"
    )
    print(
        f"  {C_BOLD}2. Claude Max (5h / 7d){C_RESET}  : "
        f"Sessão 5h: {c['five_hour_pct']:.0f}% · "
        f"Semanal: {c_color}{c['seven_day_pct']:.0f}%{C_RESET} · Fable: {c.get('fable_pct', 0):.0f}% "
        f"{C_DIM}(ritmo ideal: ≤{c['pace_target_pct']:.0f}% · reset: {c.get('seven_day_reset_fmt') or fmt_reset(c['seven_day_reset'])}){C_RESET}"
    )
    print(
        f"  {C_BOLD}3. Codex (ChatGPT Plus){C_RESET}  : "
        f"5h: {x_color}{x['five_hour_est_pct']}%{C_RESET} (reset: {x.get('five_hour_reset_fmt') or fmt_reset(x['five_hour_reset'])}) · "
        f"Semanal 7d: {x.get('seven_day_pct', 0)}% · Modelo: {x['model']}"
        f" · Orçamento diário: {x.get('daily_budget_pct', 100/7):.1f}% · Teto acumulado: {x.get('pace_target_pct')}% · {x.get('pace_status', 'desconhecido')}"
    )
    print(
        f"  {C_BOLD}4. DeepSeek CLI (dsh){C_RESET}    : "
        f"{C_VIOLET}{d['balance_text']}{C_RESET} {C_DIM}(Pay-as-you-go · CLI & Headless disponíveis){C_RESET}"
    )

    if plan:
        print(f"\n{C_AMBER}{C_BOLD}── ANÁLISE DE TAREFA & ROTEAMENTO ──────────────────────────────────────────────{C_RESET}")
        print(f"  • {C_BOLD}Domínio{C_RESET}      : {plan['domain']}")
        print(f"  • {C_BOLD}Dimensão{C_RESET}     : {plan['dimension']}")
        if plan.get("images"):
            print(f"  • {C_BOLD}Imagens{C_RESET}      : {', '.join(plan['images'])} (visíveis ao Maestro e filhos)")
        print(f"  • {C_BOLD}Pipeline{C_RESET}     : {'  ➔  '.join(plan['agents'])}")
        print(f"  • {C_BOLD}Justificação{C_RESET} : {plan['reason']}")
    print(f"{C_DIM}────────────────────────────────────────────────────────────────────────────────{C_RESET}\n")


def delegate_worker(worker: str, prompt: str, cwd: str | None = None, extra_images: list[str] | None = None) -> int:
    """
    Executa um worker especialista ("filho" do Maestro) em modo 100% autónomo (sem pedir permissões)
    e passa-lhe automaticamente quaisquer imagens coladas!
    """
    ensure_dirs()
    env = dict(os.environ)
    env["PATH"] = f"{NODE_BIN}:{NPM_BIN}:{LOCAL_BIN}:{env.get('PATH', '/usr/bin:/bin')}"
    work_cwd = cwd or os.getcwd()
    images = extract_images(prompt, extra_images)
    t0 = time.time()

    full_prompt = prompt
    if images:
        img_note = (
            "\n\n[Nota de Imagens do Maestro: O utilizador colou a(s) seguinte(s) imagem(ns) no NAS: "
            + ", ".join(images)
            + ". Inspeciona-a(s) diretamente (read_file / read_image) como parte essencial deste pedido.]"
        )
        if img_note not in full_prompt:
            full_prompt += img_note

    if worker in ("sonnet", "opus", "haiku", "claude"):
        model = "sonnet" if worker == "claude" else worker
        cq = get_claude_quota()
        if model == "opus" and cq["pace_status"] in ("limitado", "critico", "acima_do_ritmo"):
            print(f"[Maestro] Quota semanal do Claude Max a {cq['seven_day_pct']:.0f}% (acima do ritmo ideal {cq['pace_target_pct']:.0f}%). A usar Sonnet (0.2x) para proteger a tua semana.")
            model = "sonnet"
        claude_bin = str(NODE_BIN / "claude") if (NODE_BIN / "claude").exists() else str(NPM_BIN / "claude")
        cmd = [
            claude_bin, "-p", full_prompt,
            "--model", model,
            "--dangerously-skip-permissions",
            "--add-dir", PASTES_DIR,
            "--add-dir", "/srv/cloud4tb/WORK/DEVELOPMENT",
        ]
    elif worker == "codex":
        if get_codex_quota().get("pace_blocked", True):
            print("[Maestro] Codex reservado: orçamento diário/semanal atingido ou desconhecido. Aguarda quota ou usa outro motor compatível.")
            return 75
        codex_bin = str(NPM_BIN / "codex") if (NPM_BIN / "codex").exists() else str(LOCAL_BIN / "codex")
        cmd = [
            codex_bin, "exec",
            # O Maestro corre fora de um repo git; sem isto o Codex recusa o turno.
            "--skip-git-repo-check",
            "-s", "danger-full-access",
            "--add-dir", PASTES_DIR,
            "-C", work_cwd,
        ]
        for img in images:
            cmd.extend(["-i", img])
        cmd.append(full_prompt)
    elif worker == "deepseek":
        import maestro_budget as B
        B.require_bounded_cli()
        dsh_bin = str(NPM_BIN / "dsh")
        ds_key = _extract_deepseek_key()
        if ds_key:
            env["DEEPSEEK_API_KEY"] = ds_key
        env["DSH_TELEMETRY_MODE"] = "DISABLED"
        from maestro_deepseek_gateway import cli_command
        cmd = cli_command([dsh_bin, "headless", "--approval-policy", "never", full_prompt])
    else:
        raise ValueError(f"Worker desconhecido: {worker}")

    img_info = f" (com {len(images)} imagem(ns))" if images else ""
    print(f"[Maestro ➔ {worker.upper()}] A executar sub-tarefa{img_info} em {work_cwd}...")
    r = subprocess.run(cmd, env=env, cwd=work_cwd)
    elapsed = round(time.time() - t0, 1)

    try:
        with open(HISTORY_LOG, "a") as f:
            f.write(json.dumps({
                "ts": time.time(),
                "worker": worker,
                "cwd": work_cwd,
                "images": images,
                "elapsed_s": elapsed,
                "rc": r.returncode,
                "prompt_preview": prompt[:200],
            }, ensure_ascii=False) + "\n")
    except Exception:
        pass

    update_shared_context()
    return r.returncode


def launch_substitute_maestro(engine: str, header_prompt: str | None = None, images: list[str] | None = None) -> None:
    """Arranca o Maestro Substituto (Codex, Claude Sonnet ou DeepSeek CLI) em modo 100% autónomo."""
    env = dict(os.environ)
    env["PATH"] = f"{NODE_BIN}:{NPM_BIN}:{LOCAL_BIN}:{env.get('PATH', '/usr/bin:/bin')}"

    ctx_note = (
        f"Estás a assumir como MAESTRO SUBSTITUTO no pavilion-nas. "
        f"Lê {SHARED_CONTEXT} para veres o contexto partilhado e as quotas atuais."
    )
    full_prompt = f"{ctx_note}\n\n{header_prompt}" if header_prompt else None

    if engine == "codex":
        codex_bin = str(NPM_BIN / "codex") if (NPM_BIN / "codex").exists() else str(LOCAL_BIN / "codex")
        cmd = [codex_bin, "-s", "danger-full-access", "-a", "never", "--add-dir", PASTES_DIR]
        for img in (images or []):
            cmd.extend(["-i", img])
        if full_prompt:
            cmd.append(full_prompt)
        os.execvpe(codex_bin, cmd, env)
    elif engine == "claude-sonnet":
        claude_bin = str(NODE_BIN / "claude") if (NODE_BIN / "claude").exists() else str(NPM_BIN / "claude")
        cmd = [claude_bin, "--model", "sonnet", "--dangerously-skip-permissions", "--add-dir", PASTES_DIR]
        if full_prompt:
            cmd.append(full_prompt)
        os.execvpe(claude_bin, cmd, env)
    else:
        import maestro_budget as B
        B.require_bounded_cli()
        ds_key = _extract_deepseek_key()
        if ds_key:
            env["DEEPSEEK_API_KEY"] = ds_key
        env["DSH_TELEMETRY_MODE"] = "DISABLED"
        if full_prompt:
            dsh_bin = str(NPM_BIN / "dsh")
            from maestro_deepseek_gateway import cli_command
            subprocess.run(cli_command([dsh_bin, "headless", "--approval-policy", "never", full_prompt]), env=env)
        else:
            dsh_term = str(HOME / "scripts/dsh-term.sh")
            from maestro_deepseek_gateway import cli_command
            cmd = cli_command([dsh_term])
            os.execvpe(cmd[0], cmd, env)


def configure_tmux_maestro_hud() -> None:
    if not os.environ.get("TMUX"):
        return
    try:
        ag = get_antigravity_status()
        cq = get_claude_quota()
        ds = get_deepseek_balance()
        # "usado" explícito: sem isto, "Gemini 100%" podia ler-se como 100% livre.
        ag_pct = float(ag.get('gemini_used_pct') or 0)
        ag_col = "#f87171" if (ag.get('limited') or ag_pct >= 100) else "#34d399"
        ag_txt = f"{ag_pct:.0f}% usado"
        left = f" #[fg=#38bdf8,bold]👑 MAESTRO AI ORQUESTRADOR#[default] #[fg=#94a3b8]·#[default] #[fg={ag_col}]Gemini {ag_txt}#[default] #[fg=#94a3b8]|#[default] #[fg=#a78bfa]Claude 5h:{cq.get('five_hour_pct', 0):.0f}% 7d:{cq.get('seven_day_pct', 0):.0f}%#[default] #[fg=#94a3b8]|#[default] #[fg=#38bdf8]DeepSeek {ds.get('balance_text', '')}#[default] "
        right = " #[fg=#fbbf24]📱 Studio App :8109#[default] #[fg=#64748b]|#[default] #[fg=#94a3b8]PgUp/PgDn Scroll#[default] "
        for cmd in [
            ["tmux", "set-option", "status", "on"],
            ["tmux", "set-option", "status-position", "top"],
            ["tmux", "set-option", "status-style", "bg=#070c18,fg=#e2e8f0"],
            ["tmux", "set-option", "status-left-length", "140"],
            ["tmux", "set-option", "status-right-length", "80"],
            ["tmux", "set-option", "status-left", left],
            ["tmux", "set-option", "status-right", right],
            ["tmux", "set-window-option", "window-status-format", ""],
            ["tmux", "set-window-option", "window-status-current-format", ""],
        ]:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
    except Exception:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Maestro AI — Orquestrador Central do pavilion-nas")
    parser.add_argument("prompt", nargs="*", help="Pedido a analisar ou executar")
    parser.add_argument("--status", action="store_true", help="Mostra o painel de quotas, resets e saldos")
    parser.add_argument("--json", action="store_true", help="Devolve o estado/plano em JSON")
    parser.add_argument("--plan", action="store_true", help="Apenas mede a dimensão e mostra o plano de agentes")
    parser.add_argument("--from-file", help="Lê o pedido a partir de um ficheiro .md (ex.: Pulso)")
    parser.add_argument("--image", "-i", action="append", default=[], help="Caminho de imagem a anexar e passar aos agentes filhos")
    parser.add_argument("--delegate", choices=["sonnet", "opus", "haiku", "codex", "deepseek"],
                        help="Delega uma sub-tarefa (e imagens) num agente especialista e limpa no fim")
    parser.add_argument("--substitute", action="store_true",
                        help="Força o arranque do Maestro Substituto (caso o Maestro principal esteja sem quota)")
    parser.add_argument("--new", "-n", action="store_true",
                        help="Abre uma conversa limpa (novo chat) em vez de retomar a anterior")
    parser.add_argument("--cleanup", action="store_true", help="Limpa sessões temporárias e ficheiros antigos")
    parser.add_argument("--cwd", help="Diretório de trabalho alvo")
    args = parser.parse_args()

    ensure_dirs()
    configure_tmux_maestro_hud()

    if args.cleanup:
        res = cleanup_resources(verbose=not args.json)
        if args.json:
            print(json.dumps(res, ensure_ascii=False))
        return

    prompt_text = " ".join(args.prompt).strip()
    if args.from_file and os.path.exists(args.from_file):
        prompt_text = pathlib.Path(args.from_file).read_text(errors="replace").strip()

    if args.status and not prompt_text:
        update_shared_context()
        if args.json:
            print(json.dumps({
                "active_maestro": choose_active_maestro(args.substitute),
                "claude": get_claude_quota(),
                "codex": get_codex_quota(),
                "deepseek": get_deepseek_balance(),
                "antigravity": get_antigravity_status(),
            }, ensure_ascii=False, indent=2))
        else:
            print_dashboard(force_substitute=args.substitute)
        return

    if args.delegate:
        if not prompt_text:
            sys.exit("Erro: indica o prompt para delegar.")
        rc = delegate_worker(args.delegate, prompt_text, args.cwd, args.image)
        cleanup_resources(verbose=False)
        sys.exit(rc)

    active = choose_active_maestro(force_substitute=args.substitute)
    agy_common_flags = [
        "--dangerously-skip-permissions",
        "--mode", "auto",
        "--add-dir", "/srv/cloud4tb/WORK/DEVELOPMENT",
        "--add-dir", "/srv/appdata/0_SERVER/2_CONFIG",
        "--add-dir", PASTES_DIR,
    ]

    if prompt_text:
        plan = classify_task(prompt_text, args.cwd, args.image)
        update_shared_context(prompt_text, plan)
        if args.json:
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            return
        print_dashboard(plan, force_substitute=args.substitute)
        if args.plan:
            return

        images_line = (
            f"- Imagens Anexadas: {', '.join(plan['images'])} (abre-as imediatamente com `read_file` e passa-as aos filhos com `--image` se delegares)\n"
            if plan.get("images") else ""
        )
        header = (
            f"[MAESTRO AI — CONTEXTO AUTOMÁTICO DE QUOTAS E ROTEAMENTO]\n"
            f"- Domínio: {plan['domain']} | Dimensão: {plan['dimension']}\n"
            f"- Pipeline recomendado: {' -> '.join(plan['agents'])}\n"
            f"- Motivo: {plan['reason']}\n"
            f"{images_line}"
            f"- Estado Quotas: Claude Max 7d={plan['quotas']['claude']['seven_day_pct']:.0f}% "
            f"(ritmo ideal ≤{plan['quotas']['claude']['pace_target_pct']:.0f}%, status={plan['quotas']['claude']['pace_status']}), "
            f"Codex 5h=~{plan['quotas']['codex']['five_hour_est_pct']}%, "
            f"DeepSeek={plan['quotas']['deepseek']['balance_text']}\n"
            f"- Contexto partilhado: {SHARED_CONTEXT}\n"
            f"- Instrução Maestro: Resolve diretamente se for mais eficiente, ou usa `python3 ~/scripts/maestro/maestro.py --delegate <sonnet|codex|deepseek|opus> [--image /caminho/img.png] \"briefing cirúrgico\"` após recolheres o contexto. Faz cleanup no fim.\n"
            f"---\nPEDIDO DO HUBERT:\n{prompt_text}"
        )

        if active["engine"] != "agy":
            launch_substitute_maestro(active["engine"], header, plan.get("images"))
            return

        agy_bin = str(LOCAL_BIN / "agy")
        cmd = [agy_bin, *agy_common_flags, "-i", header]
        rc = subprocess.call(cmd)
        if rc != 0:
            print("\n⚠ [Maestro Failover] O Antigravity terminou com código de erro/limite. A ativar o Maestro Substituto automaticamente...")
            sub = choose_active_maestro(force_substitute=True)
            launch_substitute_maestro(sub["engine"], header, plan.get("images"))
    else:
        update_shared_context()
        print_dashboard(force_substitute=args.substitute)
        if active["engine"] != "agy":
            launch_substitute_maestro(active["engine"], None)
            return

        agy_bin = str(LOCAL_BIN / "agy")
        cmd = [agy_bin, *agy_common_flags] + ([] if args.new else ["--continue"])
        rc = subprocess.call(cmd)
        if rc != 0:
            print("\n⚠ [Maestro Failover] O Antigravity não pôde retomar ou atingiu limite. A ativar o Maestro Substituto automaticamente...")
            sub = choose_active_maestro(force_substitute=True)
            launch_substitute_maestro(sub["engine"], None)


if __name__ == "__main__":
    main()
