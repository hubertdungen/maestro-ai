"""A tabela do Maestro: quem decide, quem executa e com que modelos.

Este módulo é deliberadamente puro: recebe as quotas, a frota e o catálogo já
descobertos e devolve uma estrutura pronta a renderizar. Não faz descoberta,
não lança agentes e não inventa modelos em falta. A UI mostra a mesma verdade
que o servidor usa para atribuir tarefas.
"""
from __future__ import annotations

import time
import maestro_model_policy as MODEL_POLICY


# A ordem é a hierarquia real de substituição do Maestro. `family` liga a linha
# ao catálogo descoberto; `rank` explica o papel sem prometer acesso.
ENGINES = [
    {
        "key": "agy",
        "icon": "♊",
        "name": "Antigravity · Gemini",
        "agent": "Antigravity",
        "rank": "Maestro principal",
        "role": "Tech específica: visão, pesquisa web nativa, contexto longo e orquestração multi-ficheiro.",
        "decision": "Primeiro na gestão de agentes: distribui trabalho e propõe um esboço. Pode atribuir a arquitetura e a coordenação técnica a outro modelo.",
        "access": ["Maestro", "Visão", "Pesquisa web", "Escrita"],
        "family": "antigravity",
    },
    {
        "key": "codex",
        "icon": "⚙️",
        "name": "Codex · OpenAI",
        "agent": "Codex CLI",
        "rank": "Substituto #1",
        "role": "Implementação, debug estrito, Docker, kernel e diagnóstico Linux.",
        "decision": "Pode executar, coordenar tecnicamente ou rever o esboço quando designado. Como substituto, gere a distribuição de agentes. Preserva o objetivo do utilizador.",
        "access": ["Substituto #1", "Escrita", "Shell"],
        "family": "codex",
    },
    {
        "key": "claude",
        "icon": "🎭",
        "name": "Claude Max · Anthropic",
        "agent": "Claude Code",
        "rank": "Substituto #2",
        "role": "Arquitetura, UI/UX, refactor profundo e revisão de código.",
        "decision": "Pode assumir a arquitetura e a coordenação técnica, rever o esboço ou executar. Como substituto, gere a distribuição de agentes. Preserva o objetivo do utilizador.",
        "access": ["Substituto #2", "Escrita", "Revisão"],
        "family": "claude",
    },
    {
        "key": "deepseek-api",
        "icon": "🔮",
        "name": "DeepSeek API · só leitura",
        "agent": "Scout DeepSeek",
        "rank": "Scout",
        "role": "Pesquisa e leitura económica: listar pastas, ler excertos e procurar texto.",
        "decision": "Não decide nem escreve. Recebe instruções e devolve resultados ao Maestro.",
        "access": ["Só leitura", "API", "Scout"],
        "family": "deepseek",
        "transport": "deepseek-api",
    },
    {
        "key": "deepseek",
        "icon": "🔮",
        "name": "DeepSeek CLI · custo controlado",
        "agent": "DeepSeek CLI",
        "rank": "Substituto #3",
        "role": "Flash para triagem, scripts e implementação corrente; Pro para problemas complexos quando justificado.",
        "decision": "Executa pelo gateway local: reserva por chamada, acerto por tokens e teto mensal de 15 EUR.",
        "access": ["CLI ativo", "Custo medido"],
        "family": "deepseek",
        "transport": "cli",
    },
]

# Regras que a UI mostra por extenso. São as mesmas que o servidor aplica em
# maestro_workflow.py; a tabela não é uma promessa separada do executor.
POLICY = [
    {
        "title": "Quem decide",
        "rule": "O Maestro de serviço gere quem faz o quê e propõe o esboço inicial. "
                "A substituição por quota segue Antigravity → Codex → Claude → DeepSeek. "
                "O coordenador/arquiteto designado pode rever o esboço, preservando o objetivo do utilizador.",
    },
    {
        "title": "Pedido simples",
        "rule": "Uma tarefa, um motor. Pedidos simples independentes podem avançar enquanto outros agentes trabalham, se houver capacidade e quota.",
    },
    {
        "title": "Pedido amplo",
        "rule": "O Maestro distribui até 6 tarefas, com instruções, agente/modelo/esforço e dependências. "
                "Se necessário, atribui primeiro a arquitetura a um modelo mais capaz; "
                "a implementação recebe as decisões técnicas dessa análise. O plano é validado antes de arrancar.",
    },
    {
        "title": "Paralelo",
        "rule": "Até 4 fornecedores em simultâneo, com uma execução por plano de subscrição. Gemini, Codex e Claude podem tratar tarefas da mesma natureza em paralelo. Distribui trabalho útil pelos planos abaixo do ritmo pretendido; escritas nos mesmos ficheiros e dependências esperam.",
    },
    {
        "title": "Espera sem tokens",
        "rule": "Quem depende de outro fica suspenso no servidor, sem chamada ao modelo. "
                "Só consome tokens quando arranca.",
    },
    {
        "title": "Passagem de informação",
        "rule": "Cada tarefa recebe apenas os resultados dos pré-requisitos, com limite. "
                "O histórico completo fica no disco.",
    },
    {
        "title": "Falha e retoma",
        "rule": "Uma falha bloqueia as dependentes. «Retomar» conserva as concluídas e repete só as "
                "falhadas ou interrompidas.",
    },
    {
        "title": "Escolha do motor",
        "rule": "Executores e coordenador técnico: especialidade + capacidade exigida + quota + custo. "
                "Poupa tokens sem sacrificar qualidade ou celeridade: pesquisa económica, contexto limitado e sem duplicação de trabalho. Antigravity tem prioridade na gestão de agentes; isso não lhe dá prioridade "
                "para executar a tarefa ou definir a arquitetura.",
    },
    {
        "title": "Identidade",
        "rule": "«Maestro disponível» é a hierarquia. «Motor» é quem executou o turno. "
                "A tabela separa os dois para não atribuir trabalho a quem só está disponível.",
    },
]

MAESTRO_MAP = {
    "agy": "agy",
    "codex": "codex",
    "claude-sonnet": "claude",
    "claude-opus": "claude",
    "sonnet": "claude",
    "opus": "claude",
    "deepseek": "deepseek",
}

CLAUDE_ENGINES = ("sonnet", "opus", "haiku", "fable")


def _pct_left(used):
    try:
        value = float(used)
    except (TypeError, ValueError):
        return None
    return round(max(0.0, min(100.0, 100.0 - value)), 1)


def _tone(left, limited=False):
    if limited or (left is not None and left <= 0):
        return "bad"
    if left is not None and left < 25:
        return "bad"
    if left is not None and left < 50:
        return "warn"
    return "ok"


def _quota_for(key, quotas):
    """Devolve a leitura real da quota, sem inventar percentagens."""
    q = (quotas or {}).get("antigravity" if key == "agy" else key, {}) or {}
    if key == "agy":
        left = _pct_left(q.get("gemini_used_pct"))
        limited = bool(q.get("limited"))
        return {
            "available": not limited and (left is None or left > 0),
            "tone": _tone(left, limited),
            "label": ("Resta %s%%" % left) if left is not None else "Sem leitura de quota",
            "reset": q.get("gemini_reset_fmt") or "janela livre",
            "detail": q.get("quota_note") or "Quota individual do Gemini/Antigravity.",
            "source": q.get("quota_source") or "",
        }
    if key == "claude":
        left7 = _pct_left(q.get("seven_day_pct"))
        left5 = _pct_left(q.get("five_hour_pct"))
        limited = bool(q.get("limited"))
        return {
            "available": not limited and (left7 is None or left7 > 0),
            "tone": _tone(left7, limited),
            "label": "Resta 7d %s%% · 5h %s%%" % (left7 if left7 is not None else "?",
                                                  left5 if left5 is not None else "?"),
            "reset": q.get("seven_day_reset_fmt") or "—",
            "detail": "Fable resta %s%% · ritmo ideal ≤%s%%." % (
                _pct_left(q.get("fable_pct")) if q.get("fable_pct") is not None else "?",
                int(q.get("pace_target_pct") or 0),
            ),
            "source": q.get("source") or "",
        }
    if key == "codex":
        left5 = _pct_left(q.get("five_hour_est_pct"))
        left7 = _pct_left(q.get("seven_day_pct"))
        limited = bool(q.get("limit_reached"))
        return {
            "available": not limited and (left5 is None or left5 > 0) and (left7 is None or left7 > 0),
            "tone": _tone(left5, limited),
            "label": "Resta 5h %s%% · 7d %s%%" % (left5 if left5 is not None else "?",
                                                  left7 if left7 is not None else "?"),
            "reset": q.get("five_hour_reset_fmt") or "—",
            "detail": "Janela de 5h é a que aperta primeiro neste plano.",
            "source": q.get("source") or "",
        }
    # DeepSeek (CLI e API partilham o saldo da conta).
    return {
        "available": bool(q.get("available", True)),
        "tone": "ok" if q.get("available", True) else "bad",
        "label": q.get("balance_text") or "Sem leitura de saldo",
        "reset": (q.get('budget') or {}).get('reset_at', '—'),
        "detail": ("Contabilizado/reservado %.2f/15 EUR · hoje %.3f/%.3f EUR · reset dia 20 · CLI/API controlados · extra +0,50 EUR (últimos 0,20 críticos)" % (
            q['budget'].get('spent_eur', 0), q['budget'].get('today_eur', 0), q['budget'].get('daily_eur', 0)))
            if q.get('budget') else "Saldo em dólares; orçamento por configurar.",
        "source": q.get("source") or "",
    }


def _models_for(row, catalog):
    entries = (catalog or {}).get("models", []) or []
    family = row["family"]
    transport = row.get("transport")
    out = []
    for e in entries:
        if family == "claude":
            if e.get("engine") not in CLAUDE_ENGINES:
                continue
        elif family == "antigravity":
            if e.get("engine") != "agy":
                continue
        elif e.get("engine") != family:
            continue
        if family == "deepseek":
            is_api = e.get("transport") == "deepseek-api"
            if transport == "deepseek-api" and not is_api:
                continue
            if transport == "cli" and is_api:
                continue
        out.append({
            **MODEL_POLICY.profile(e),
            "id": e.get("id") or "",
            "label": e.get("label") or e.get("model") or e.get("id") or "",
            "model": e.get("model") or "",
            "efforts": [str(x) for x in (e.get("efforts") or [])],
            "default_effort": e.get("default_effort"),
            "available": bool(e.get("available", True)),
            "read_only": bool(e.get("read_only_enforced")),
            "read_only_only": e.get("transport") == "deepseek-api",
            "transport": e.get("transport") or ("cli" if e.get("engine") == "deepseek" else ""),
            "source": e.get("source") or "",
            "note": e.get("note") or "",
        })
    # Preserva a ordem anunciada pelo CLI; só coloca os indisponíveis no fim.
    out.sort(key=lambda m: not m["available"])
    return out


def _active_tasks(fleet, key):
    for f in (fleet or {}).get("fleet", []) or []:
        if f.get("key") == key:
            return list(f.get("active_tasks") or [])
    return []


def build(quotas=None, fleet=None, catalog=None, maestro=None):
    """Constrói a tabela. Nunca afirma acesso que o catálogo não confirmou."""
    active = dict(maestro or {})
    maestro_engine = str(active.get("engine") or "")
    maestro_key = MAESTRO_MAP.get(maestro_engine, "")

    rows = []
    total_models = 0
    available_models = 0
    for meta in ENGINES:
        models = _models_for(meta, catalog)
        available = any(m["available"] for m in models)
        quota_key = "deepseek" if meta["key"].startswith("deepseek") else meta["key"]
        quota = _quota_for(quota_key, quotas)
        # Sem catálogo não há promessa de acesso; a linha continua visível para
        # explicar o papel e a ausência, em vez de desaparecer da tabela.
        engine_available = bool(available and quota["available"])
        if not models:
            engine_available = False
        effort_count = len({effort for m in models for effort in m["efforts"]})
        total_models += len(models)
        available_models += sum(1 for m in models if m["available"])
        rows.append({
            **{k: meta[k] for k in ("key", "icon", "name", "agent", "rank", "role", "decision", "access")},
            "models": models,
            "model_count": len(models),
            "available_model_count": sum(1 for m in models if m["available"]),
            "effort_count": effort_count,
            "available": engine_available,
            "quota": quota,
            "active_tasks": _active_tasks(fleet, meta["key"]),
            "is_maestro": meta["key"] == maestro_key,
        })

    missing = [str(n) for n in ((catalog or {}).get("notes") or []) if n]
    codex_readonly = any(
        m["read_only"] for row in rows if row["key"] == "codex" for m in row["models"]
    )
    read_only_note = (
        "A leitura restrita verificada é a do DeepSeek API (sem shell, escrita ou URLs livres). "
        + ("O Codex também anuncia leitura restrita neste NAS." if codex_readonly else
           "O sandbox de leitura do Codex falha neste NAS; as pesquisas usam o scout DeepSeek.")
    )
    return {
        "generated_at": time.time(),
        "maestro": active,
        "maestro_key": maestro_key,
        "rows": rows,
        "policy": POLICY,
        "counts": {
            "engines": len(rows),
            "models": total_models,
            "available_models": available_models,
            "available_engines": sum(1 for r in rows if r["available"]),
        },
        "notes": {
            "catalogue": "O catálogo confirma o que cada CLI anuncia; não prova acesso efectivo. "
                         "A quota é verificada outra vez na admissão da tarefa. As finalidades são preferências locais, não benchmarks. "
                         "Codex partilha a quota Plus; Claude partilha Max; DeepSeek CLI/API partilham saldo. "
                         "Trocar de modelo na mesma conta não cria tokens. Antigravity está bloqueado pelo sinal 429; acesso 3P separado não foi confirmado.",
            "read_only": read_only_note,
            "vision": "Pedidos com imagem exigem um motor com visão. Sem um executor de leitura com visão e quota, "
                      "o Maestro mantém uma execução única em vez de fingir capacidade.",
            "missing": missing,
        },
    }
