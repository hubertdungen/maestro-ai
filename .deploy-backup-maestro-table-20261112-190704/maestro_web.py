#!/usr/bin/env python3
"""
Maestro AI Studio 3.0 — App Web, Android/Tablet PWA & Windows Widescreen Desktop (.exe) na porta 8109.
Inclui:
1. 💬 Chat & Orquestrador (com ✨ Melhorar Prompt, 🎤 Voz pt-PT, 📷 Imagens e Split Code/Preview Canvas)
2. 🎨 Gerador de Imagens AI & GIFs Animados + Galeria Visual (Lightbox, Download, Usar no Chat)
3. 🎬 Media & Containers Hub:
   - Pesquisa ao vivo no Radarr (:7878) e Sonarr (:8989) com posters e botão 1-toque «⬇ Sacar Agora» + Fila de Downloads;
   - Cartões dos Containers Docker com estado ao vivo, ações predefinidas para o Maestro, Ver Logs e Reiniciar.
4. 🏠 Casa, Impressoras 3D & Schedulers (Home Assistant :8123):
   - Telemetria em tempo real da Centauri Carbon 2 e Qidi Plus 4 (progresso %, tempo restante, temperaturas bico/cama);
   - Controlo 1-toque de Tomadas Inteligentes e Luzes;
   - Schedulers & Automação integrada (criar horários, ligar/desligar, «▶ Executar Agora»).
5. 🧩 Artifacts & Pastas Coloridas Personalizáveis (criar pastas com ícones e cores próprias, guardar e pré-visualizar Artifacts).
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import pathlib
import py_compile
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HOME = pathlib.Path.home()
MAESTRO_DIR = HOME / "scripts" / "maestro"
WIN_DIR = HOME / "scripts" / "windows"
sys.path.insert(0, str(MAESTRO_DIR))
import maestro as M  # noqa: E402
import build_windows_exe as WIN_BUILDER  # noqa: E402
import maestro_features as F  # noqa: E402
import maestro_actions as ACTIONS
import maestro_queue as Q
import maestro_models as MODELS
import maestro_workflow as WORKFLOW
import maestro_runtime as RUNTIME
import maestro_readonly as READONLY
import maestro_workflow_ui as WORKFLOW_UI
import maestro_organizer as ORGANIZER  # noqa: E402

CHATS_DIR = M.STATE_DIR / "chats"
TURN_QUEUE = Q.TurnQueue(M.STATE_DIR / "turn-queue.json")
HANDOVER_PATH = M.STATE_DIR / "handover.json"
HANDOVER_TTL_S = 12 * 3600


def read_handover() -> dict | None:
    try:
        data = json.loads(HANDOVER_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def write_handover(payload: dict) -> None:
    """Snapshot do estado do turno para o próximo orquestrador continuar sem recomeçar."""
    try:
        M.STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = HANDOVER_PATH.with_name("handover.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(HANDOVER_PATH)
    except Exception:
        pass


def clear_handover() -> None:
    try:
        HANDOVER_PATH.unlink()
    except Exception:
        pass


def handover_prompt_block(chat_id: str) -> str:
    ho = read_handover()
    if not ho or not chat_id or ho.get("chat_id") != chat_id:
        return ""
    try:
        age = time.time() - float(ho.get("ts") or 0)
    except Exception:
        age = 1e9
    if age > HANDOVER_TTL_S:
        return ""
    lines = [
        "## CONTEXTO DE PASSAGEM (orquestrador anterior)",
        f"Motor anterior: {ho.get('agent_label') or ho.get('engine') or '—'} · estado: {ho.get('status') or '—'}",
        f"Objectivo: {ho.get('objective') or '—'}",
        f"Projecto/pasta: {ho.get('project') or '—'}",
        f"Plano: {json.dumps(ho.get('plan') or {}, ensure_ascii=False)}",
        f"Próximo passo: {ho.get('next_step') or '—'}",
    ]
    if ho.get('workflow_path'):
        lines.append('Plano e passagens persistidos: ' + str(ho['workflow_path']))
    if ho.get("files"):
        lines.append("Ficheiros tocados: " + ", ".join(map(str, ho["files"][:20])))
    if ho.get("summary"):
        lines.append("Última saída (resumo):\n" + str(ho["summary"])[-1800:])
    lines.append("Não recomeces do zero: confirma este estado e continua a partir daqui.")
    return "\n".join(lines)
PASTES_DIR = pathlib.Path(M.PASTES_DIR)
# Blocos de texto colados (grandes) vão para aqui: PASTES_DIR é gerido por root/ACL
# e não é escrevível pelo hubert, logo usamos uma pasta de dados do próprio utilizador.
DOCS_DIR = pathlib.Path.home() / ".local" / "share" / "maestro" / "docs"
PULSE_JSON = pathlib.Path("/srv/appdata/0_SERVER/2_CONFIG/homepage/images/pulse.json")
SVG_PATH = MAESTRO_DIR / "maestro.svg"
ICO_PATH = MAESTRO_DIR / "maestro.ico"
ICONS_DIR = MAESTRO_DIR / "icons"
ICON_192_PATH = ICONS_DIR / "icon-192.png"
ICON_512_PATH = ICONS_DIR / "icon-512.png"
ICON_MASKABLE_PATH = ICONS_DIR / "icon-maskable-512.png"
APPLE_TOUCH_PATH = ICONS_DIR / "apple-touch-icon.png"
EXE_PATH = WIN_DIR / "Maestro-AI.exe"
BOOT_TIME = int(time.time())
ACTIVE_JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()
CHAT_STORAGE_LOCK = threading.RLock()

# Tempo máximo de uma execução de agente. O antigo limite fixo de 420s (7 min)
# matava tarefas longas (refactors, builds, análises) a meio.
# Configurável: MAESTRO_AGENT_TIMEOUT_S (segundos). 0 = sem limite.
try:
    AGENT_TIMEOUT_S = max(0, int(os.environ.get("MAESTRO_AGENT_TIMEOUT_S", "7200")))
except Exception:
    AGENT_TIMEOUT_S = 7200


class AgentTimeout(Exception):
    """Execução do agente excedeu o tempo limite configurado."""

    def __init__(self, seconds: int):
        self.seconds = int(seconds)
        super().__init__(f"timeout após {self.seconds}s")


def get_build_info() -> dict:
    """Calcula a assinatura ao vivo dos ficheiros do Maestro para o Auto-Updater."""
    watched = [
        MAESTRO_DIR / "maestro_web.py",
        MAESTRO_DIR / "maestro_features.py",
        MAESTRO_DIR / "maestro_organizer.py",
        MAESTRO_DIR / "maestro_queue.py",
        *[MAESTRO_DIR / name for name in ("maestro_models.py", "maestro_workflow.py", "maestro_runtime.py", "maestro_workflow_ui.py", "maestro_readonly.py")],
        MAESTRO_DIR / "maestro.py",
        SVG_PATH,
        ICON_192_PATH,
        ICON_512_PATH,
        ICON_MASKABLE_PATH,
        APPLE_TOUCH_PATH,
        EXE_PATH,
    ]
    h = hashlib.sha256()
    latest_mtime = 0.0
    for p in watched:
        try:
            st = p.stat()
            latest_mtime = max(latest_mtime, st.st_mtime)
            h.update(f"{p.name}:{int(st.st_mtime)}:{st.st_size};".encode("utf-8"))
        except Exception:
            pass
    short_hash = h.hexdigest()[:10]
    return {
        "version": "3.3",
        "hash": short_hash,
        "updated_at": int(latest_mtime),
        "updated_str": time.strftime("%d/%m %H:%M:%S", time.localtime(latest_mtime or time.time())),
        "boot_time": BOOT_TIME,
    }


def start_hot_reload_watcher() -> None:
    """Monitoriza alterações nos ficheiros Python do Maestro e reinicia o servidor automaticamente."""
    watched_py = [
        MAESTRO_DIR / "maestro_web.py",
        MAESTRO_DIR / "maestro_features.py",
        MAESTRO_DIR / "maestro_organizer.py",
        MAESTRO_DIR / "maestro_queue.py",
        *[MAESTRO_DIR / name for name in ("maestro_models.py", "maestro_workflow.py", "maestro_runtime.py", "maestro_workflow_ui.py", "maestro_readonly.py")],
        MAESTRO_DIR / "maestro.py",
    ]
    initial_mtimes = {}
    for p in watched_py:
        try:
            initial_mtimes[str(p)] = p.stat().st_mtime
        except Exception:
            initial_mtimes[str(p)] = 0.0

    def _watch_loop():
        while True:
            time.sleep(3)
            with JOBS_LOCK:
                busy = any(j.get("status") == "running" for j in ACTIVE_JOBS.values())
            if busy:
                continue
            changed = False
            for p in watched_py:
                try:
                    m = p.stat().st_mtime
                    if m != initial_mtimes.get(str(p), 0.0):
                        py_compile.compile(str(p), doraise=True)
                        changed = True
                except Exception:
                    changed = False
                    break
            if changed:
                time.sleep(0.4)
                os.execv(sys.executable, [sys.executable] + sys.argv)

    t = threading.Thread(target=_watch_loop, daemon=True)
    t.start()


def ensure_all_dirs() -> None:
    M.ensure_dirs()
    F.ensure_feature_dirs()
    CHATS_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    if not EXE_PATH.exists() or not ICO_PATH.exists():
        try:
            WIN_BUILDER.main()
        except Exception:
            pass


def get_folder_map() -> dict[str, dict]:
    return {f["id"]: f for f in F.get_folders()}


def chat_activity(chat: dict) -> dict:
    """Small preview from public conversation data, without an extra model call."""
    def compact(value: str) -> str:
        value = re.sub(r"```[\s\S]*?```", " [código] ", str(value or ""))
        value = re.sub(r"[\n\r]+", " · ", value)
        value = re.sub(r"[#*`]+", "", value)
        value = re.sub(r"\s+", " ", value).strip(" ·")
        return value[:177] + "…" if len(value) > 180 else value

    messages = chat.get("messages") or []
    latest = messages[-1] if messages else {}
    objective = next((m.get("text", "") for m in reversed(messages) if m.get("role") == "user"), "")
    with JOBS_LOCK:
        job = next((j for j in ACTIVE_JOBS.values() if j.get("chat_id") == chat.get("id") and j.get("status") == "running"), {})
        status = job.get("status")
        step = job.get("current_step", "")
    if status == "running":
        return {"state": "running", "label": "Em curso", "text": compact(objective) or "A processar anexos", "detail": compact(step)}
    if status == "stopped":
        return {"state": "stopped", "label": "Interrompido", "text": compact(objective), "detail": ""}
    if latest.get("role") == "assistant":
        return {"state": "idle", "label": "Última resposta", "text": compact(latest.get("text", "")), "detail": ""}
    return {"state": "idle", "label": "Último pedido" if messages else "Nova conversa", "text": compact(objective) or "À espera do primeiro pedido", "detail": ""}


def list_chats() -> list[dict]:
    ensure_all_dirs()
    items = []
    for f in CHATS_DIR.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            items.append({
                "id": d["id"],
                "title": d.get("title") or "Nova conversa",
                "project": d.get("folder_id") or d.get("project") or "nas",
                "execution_project": d.get("project") or "nas",
                "folder_source": d.get("folder_source") or "original",
                "pinned": bool(d.get("pinned", False)),
                "engine": d.get("engine") or "auto",
                "updated_at": d.get("updated_at") or 0,
                "msg_count": len(d.get("messages") or []),
                "activity": chat_activity(d),
            })
        except Exception:
            continue
    items.sort(key=lambda x: (1 if x["pinned"] else 0, x["updated_at"]), reverse=True)
    return items


def load_chat(chat_id: str) -> dict | None:
    ensure_all_dirs()
    clean_id = re.sub(r"[^a-zA-Z0-9_-]", "", chat_id)
    p = CHATS_DIR / f"{clean_id}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_chat(chat: dict) -> dict:
    ensure_all_dirs()
    clean_id = re.sub(r"[^a-zA-Z0-9_-]", "", chat["id"])
    chat["id"] = clean_id
    chat["updated_at"] = time.time()
    p = CHATS_DIR / f"{clean_id}.json"
    tmp = CHATS_DIR / f"{clean_id}.json.tmp"
    with CHAT_STORAGE_LOCK:
        # Filing metadata is independent of a worker's earlier conversation snapshot.
        if p.exists():
            try:
                previous = json.loads(p.read_text(encoding="utf-8"))
                for key in ("folder_id", "folder_source", "organized_at"):
                    if key in previous:
                        chat[key] = previous[key]
            except (OSError, ValueError):
                pass
        tmp.write_text(json.dumps(chat, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
    return chat


def organize_idle_chats(now: float | None = None) -> list[dict]:
    """Only sidebar metadata changes; timestamps, messages and execution cwd stay intact."""
    now = time.time() if now is None else now
    candidates = ORGANIZER.discover_candidates(HOME, F.get_folders())
    changes = []
    for path in CHATS_DIR.glob("*.json"):
        with CHAT_STORAGE_LOCK, JOBS_LOCK:
            try:
                chat = json.loads(path.read_text(encoding="utf-8"))
                if any((j.get('chat_id') or key) == chat['id'] and j.get('status') == 'running' for key, j in ACTIVE_JOBS.items()):
                    continue
                timestamps = [float(chat.get('updated_at') or chat.get('created_at') or now)]
                timestamps += [float(m.get('ts') or 0) for m in chat.get('messages', [])]
                if now - max(timestamps) < ORGANIZER.IDLE_SECONDS:
                    continue
                if chat.get('folder_source') == 'manual':
                    continue
                target = ORGANIZER.classify_chat(chat, candidates)
                if not target or chat.get('folder_id') == target['id']:
                    continue
                if target['id'] not in get_folder_map():
                    F.save_folder(target)
                chat.update(folder_id=target['id'], folder_source='auto', organized_at=now)
                tmp = path.with_suffix('.json.organizing')
                tmp.write_text(json.dumps(chat, ensure_ascii=False, indent=2), encoding='utf-8')
                tmp.replace(path)
                changes.append({'id': chat['id'], 'folder': target['id']})
            except (OSError, ValueError, KeyError, TypeError):
                continue
    return changes


def start_chat_organizer() -> None:
    def run():
        while True:
            try:
                organize_idle_chats()
            except Exception as exc:
                print(f'Chat organizer: {exc}', file=sys.stderr, flush=True)
            time.sleep(60)
    threading.Thread(target=run, daemon=True, name='chat-organizer').start()


def create_chat(project: str = "nas", engine: str = "auto", title: str = "Nova conversa") -> dict:
    cid = f"chat-{int(time.time()*1000)}-{uuid.uuid4().hex[:6]}"
    chat = {
        "id": cid,
        "title": title,
        "project": project,
        "pinned": False,
        "engine": engine,
        "created_at": time.time(),
        "updated_at": time.time(),
        "messages": [],
    }
    return save_chat(chat)


def enhance_prompt(raw_text: str, project_key: str) -> dict:
    fmap = get_folder_map()
    proj = fmap.get(project_key) or {"name": "Pavilion-NAS", "cwd": str(HOME)}
    raw_text = raw_text.strip()
    if not raw_text:
        return {"enhanced": "", "source": "empty"}

    ds_key = M._extract_deepseek_key()
    if ds_key:
        try:
            sys_msg = (
                "Tu és o Melhorador de Pedidos do Maestro AI Studio no servidor pavilion-nas do Hubert. "
                "Reescreve o rascunho em Português de Portugal (pt-PT) de forma clara, estruturada e cirúrgica "
                f"para a pasta/projeto '{proj['name']}' em '{proj['cwd']}'. "
                "Se for um pedido de filme/série, menciona Sonarr (:8989) ou Radarr (:7878). "
                "Se for domótica/impressão 3D, menciona Home Assistant (:8123). "
                "Devolve APENAS o texto melhorado, sem introduções nem aspas."
            )
            payload = json.dumps({
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": sys_msg},
                    {"role": "user", "content": raw_text},
                ],
                "temperature": 0.2,
                "max_tokens": 380,
            }).encode("utf-8")
            req = urllib.request.Request(
                "https://api.deepseek.com/chat/completions",
                data=payload,
                headers={"Authorization": f"Bearer {ds_key}", "Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                enhanced = data["choices"][0]["message"]["content"].strip()
                if enhanced:
                    return {"enhanced": enhanced, "source": "DeepSeek V3.2"}
        except Exception:
            pass

    enhanced = (
        f"[Projeto: {proj['name']} · Pasta: {proj['cwd']}]\n"
        f"Objetivo: {raw_text}\n"
        f"Plano: 1) Inspecionar ficheiros/serviços relevantes em {proj['cwd']}; "
        f"2) Executar a ação com qualidade máxima; 3) Confirmar o resultado."
    )
    return {"enhanced": enhanced, "source": "Local"}


def inspect_project(project_key: str) -> dict:
    fmap = get_folder_map()
    proj = fmap.get(project_key) or {"name": "Pavilion-NAS", "cwd": str(HOME)}
    cwd = proj.get("cwd") or str(HOME)
    if not os.path.isdir(cwd):
        return {"project": proj, "exists": False, "git_branch": "—", "git_status": [], "recent_files": []}

    branch = "—"
    status_list = []
    try:
        b = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd, capture_output=True, text=True, timeout=3)
        if b.returncode == 0:
            branch = b.stdout.strip()
        st = subprocess.run(["git", "status", "--porcelain"], cwd=cwd, capture_output=True, text=True, timeout=4)
        if st.returncode == 0:
            for line in st.stdout.splitlines()[:20]:
                if len(line) > 3:
                    status_list.append({"code": line[:2].strip(), "path": line[3:].strip()})
    except Exception:
        pass

    recent_files = []
    try:
        exts = (".cs", ".py", ".js", ".ts", ".tsx", ".jsx", ".yaml", ".yml", ".json", ".md", ".html", ".css", ".sh")
        found = []
        base = pathlib.Path(cwd)
        skip_dirs = {".git", "node_modules", "Library", "Temp", "Obj", "Logs", ".venv", "__pycache__", ".cache", ".local"}
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
            if len(pathlib.Path(root).relative_to(base).parts) > 3:
                dirs[:] = []
                continue
            for fn in files:
                if fn.endswith(exts):
                    fp = pathlib.Path(root) / fn
                    try:
                        found.append((fp.stat().st_mtime, str(fp.relative_to(base)), str(fp)))
                    except Exception:
                        pass
            if len(found) > 220:
                break
        found.sort(reverse=True)
        for mtime, rel, full in found[:16]:
            recent_files.append({"rel": rel, "path": full, "mtime": int(mtime)})
    except Exception:
        pass

    return {"project": proj, "exists": True, "git_branch": branch, "git_status": status_list, "recent_files": recent_files}


def read_project_file(file_path: str) -> dict:
    allowed_roots = ("/home/hubert", "/srv/cloud4tb/WORK/DEVELOPMENT", "/srv/appdata/0_SERVER")
    p = os.path.abspath(file_path)
    if not any(p.startswith(r) for r in allowed_roots) or not os.path.isfile(p):
        return {"error": "Ficheiro fora das pastas autorizadas ou inexistente."}
    try:
        text = pathlib.Path(p).read_text(encoding="utf-8", errors="replace")[:48000]
        return {"path": p, "name": os.path.basename(p), "content": text}
    except Exception as e:
        return {"error": str(e)}


def build_conversation_prompt(chat: dict, user_text: str, images: list[str], plan: dict) -> str:
    history_lines = []
    messages = chat.get("messages") or []
    answered = {m.get("turn_id") for m in messages if m.get("role") == "assistant" and m.get("turn_id")}
    prior = [m for m in messages if (not m.get("turn_id") or m.get("turn_id") in answered) and m.get("turn_id") != plan.get("turn_id", "__none__")][-10:]
    for m in prior:
        role = "HUBERT" if m.get("role") == "user" else f"MAESTRO ({m.get('agent_used', 'AI')})"
        txt = (m.get("text") or "")[:1800]
        imgs = m.get("images") or []
        img_str = f" [Imagens: {', '.join(imgs)}]" if imgs else ""
        history_lines.append(f"{role}{img_str}:\n{txt}")

    hist_block = ""
    if history_lines:
        hist_block = "=== HISTÓRICO DESTA CONVERSA ===\n" + "\n\n".join(history_lines) + "\n=== FIM DO HISTÓRICO ===\n\n"

    img_block = ""
    if images:
        img_block = (
            f"\n[IMAGENS ANEXADAS PELO HUBERT NESTA MENSAGEM: {', '.join(images)}]\n"
            f"Instrução: Usa a ferramenta de leitura de ficheiros/imagens para veres a(s) imagem(ns) acima.\n"
        )

    fmap = get_folder_map()
    proj = fmap.get(chat.get("project") or "nas") or {"name": "Pavilion-NAS", "cwd": str(HOME)}
    return (
        f"[MAESTRO AI STUDIO · PASTA/PROJETO: {proj['name']} ({proj['cwd']})]\n"
        f"- Domínio: {plan['domain']} | Dimensão: {plan['dimension']}\n"
        f"- Pipeline: {' -> '.join(plan['agents'])}\n"
        f"- Motor efetivo deste turno: {plan.get('exec_engine_name') or plan.get('exec_engine') or 'n/d'}"
        f" | Maestro disponível na hierarquia: {str(plan.get('maestro_name') or 'n/d').split(' (')[0]}\n"
        "- Identidade: responde como o motor efetivo deste turno. A hierarquia indica disponibilidade; não prova que outro modelo tenha participado ou delegado este pedido.\n"
        "- Autonomia: executa e valida os passos já autorizados quando as ferramentas o permitem. Confirma as permissões reais desta sessão; não herdes a limitação de outro motor. Se for necessária uma ação manual/autorização, apresenta o comando exato num bloco bash, sh ou shell completo: a app oferece Autorizar e avançar, mostra a pasta e executa no NAS após aprovação específica. Não afirmes que esse botão altera as permissões internas do fornecedor.\n"
        f"- Contexto Serviçoes NAS: Radarr (:7878, key={F.RADARR_KEY}), Sonarr (:8989, key={F.SONARR_KEY}), Home Assistant (:8123).\n"
        f"- Quotas: Claude 7d={plan['quotas']['claude']['seven_day_pct']:.0f}% (ideal ≤{plan['quotas']['claude']['pace_target_pct']:.0f}%), "
        f"Codex 5h=~{plan['quotas']['codex']['five_hour_est_pct']}%, DeepSeek={plan['quotas']['deepseek']['balance_text']}\n\n"
        f"{hist_block}"
        f"MENSAGEM ATUAL DO HUBERT:\n{user_text}"
        f"{img_block}"
    )


def _push_job_step(job: dict | None, text: str) -> None:
    if not job or not text:
        return
    clean = text.strip()
    if not clean:
        return
    with JOBS_LOCK:
        steps = job.setdefault("steps", [])
        if not steps or steps[-1] != clean:
            steps.append(clean)
            if len(steps) > 14:
                del steps[:-14]
        job["current_step"] = clean


def _tail_agy_transcript(conv_id: str, job: dict, seen_steps: set[int]) -> None:
    if not conv_id or not job:
        return
    tf = HOME / ".gemini" / "antigravity-cli" / "brain" / conv_id / ".system_generated" / "logs" / "transcript.jsonl"
    if not tf.exists():
        return
    try:
        for line in tf.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            sidx = int(d.get("step_index") or 0)
            if sidx in seen_steps:
                continue
            seen_steps.add(sidx)
            thinking = str(d.get("thinking") or "").strip()
            if thinking:
                first_ln = next((ln.strip() for ln in thinking.splitlines() if ln.strip()), "")
                if first_ln:
                    _push_job_step(job, "💭 " + first_ln[:96] + ("…" if len(first_ln) > 96 else ""))
            for tc in d.get("tool_calls") or []:
                tname = str(tc.get("name") or "")
                raw_args = tc.get("args") or tc.get("arguments") or {}
                if isinstance(raw_args, str):
                    try:
                        raw_args = json.loads(raw_args)
                    except Exception:
                        raw_args = {}
                if not isinstance(raw_args, dict):
                    raw_args = {}
                if tname in ("read_file", "view_file"):
                    fp = os.path.basename(str(raw_args.get("Path") or raw_args.get("AbsolutePath") or "ficheiro"))
                    _push_job_step(job, f"📖 A ler ficheiro: {fp}")
                elif tname in ("edit_file", "replace_file_content", "multi_replace_file_content", "sed_file"):
                    fp = os.path.basename(str(raw_args.get("Path") or raw_args.get("TargetFile") or "ficheiro"))
                    _push_job_step(job, f"✏️ A editar ficheiro: {fp}")
                elif tname in ("write_file", "write_to_file"):
                    fp = os.path.basename(str(raw_args.get("Path") or raw_args.get("TargetFile") or "ficheiro"))
                    _push_job_step(job, f"📝 A criar/escrever: {fp}")
                elif tname == "run_command":
                    summ = str(raw_args.get("toolSummary") or raw_args.get("CommandLine") or "comando shell")[:68]
                    _push_job_step(job, f"⚙️ A executar: {summ}")
                elif tname == "search_web":
                    q = str(raw_args.get("query") or "")[:58]
                    _push_job_step(job, f"🌐 Pesquisa Web: {q}")
                elif tname in ("grep_search", "find_by_name", "list_dir"):
                    q = str(raw_args.get("Query") or raw_args.get("Pattern") or raw_args.get("DirectoryPath") or "")[:50]
                    _push_job_step(job, f"🔍 A inspecionar código: {q}")
                elif tname:
                    _push_job_step(job, f"🔧 Tool: {tname}")
    except Exception:
        pass


def get_job_snapshot(chat_id: str) -> dict | None:
    pending = [t for t in TURN_QUEUE.pending(chat_id) if t["status"] == "queued"]
    with JOBS_LOCK:
        jobs = [j for j in ACTIVE_JOBS.values() if j.get("chat_id") == chat_id]
        j = next((j for j in jobs if j.get("status") == "running"), None)
        if not j:
            if not pending:
                return None
            return {"chat_id": chat_id, "task_id": pending[0]['id'], "status": "queued", "agent_label": "Maestro · Fila / Queue",
                    "current_step": pending[0].get("reason", "evaluating"), "steps": [], "partial_text": "",
                    "queued": pending, "plan": {}, "elapsed_s": 0}

        snap = {
            "chat_id": chat_id,
            "task_id": j.get("task_id"),
            "parallel_count": sum(x.get("status") == "running" for x in jobs),
            "active_agents": [{"name": x.get("agent_label"), "step": x.get("current_step")} for x in jobs if x.get("status") == "running"],
            "status": j.get("status", "running"),
            "agent_label": j.get("agent_label", "Maestro AI"),
            "maestro_label": j.get("maestro_label", ""),
            "engine_origin": j.get("engine_origin", ""),
            "elapsed_s": round(time.time() - j.get("t0", time.time()), 1),
            "current_step": j.get("current_step", "🧠 A iniciar orquestração…"),
            "steps": list(j.get("steps") or []),
            "partial_text": j.get("partial_text", ""),
            "workflow": copy.deepcopy(j.get("workflow")),
            "plan": j.get("plan") or {},
            "last_line": j.get("last_line", ""),
            "idle_s": round(max(0.0, time.time() - (j.get("last_output_ts") or time.time())), 1),
            "out_bytes": j.get("out_bytes", 0),
            "cpu_s": 0.0,
        }
        if j.get('workflow'):
            snap['active_agents'] = [{'name': c.get('agent_label'), 'step': c.get('current_step'), 'task': c.get('node_id')}
                                     for c in j.get('children', []) if c.get('status') == 'running']
            snap['parallel_count'] = len(snap['active_agents'])
            for node in (snap.get('workflow') or {}).get('nodes', []):
                child = next((c for c in reversed(j.get('children', [])) if c.get('node_id') == node['id'] and c.get('status') == 'running'), None)
                if child:
                    node.update(actual_agent=child.get('agent_label'), reason=child.get('current_step', ''),
                                model_id=child.get('plan', {}).get('model_id'), effort=child.get('plan', {}).get('effort'))
            if len(snap['active_agents']) == 1:
                snap['agent_label'] = snap['active_agents'][0]['name']
            elif snap['active_agents']:
                snap['agent_label'] = 'Maestro · ' + str(len(snap['active_agents'])) + ' agentes em execução'
            live_children = [c for c in j.get('children', []) if c.get('status') == 'running']
            if live_children:
                recent = max(live_children, key=lambda c: c.get('last_output_ts', c.get('t0', 0)))
                snap['last_line'] = recent.get('last_line', '')
                snap['idle_s'] = round(max(0, time.time() - recent.get('last_output_ts', recent.get('t0', time.time()))), 1)
        proc = j.get("proc")
    # CPU acumulada do subprocesso: mostra que ele está vivo mesmo sem output
    if proc is not None and proc.poll() is None:
        try:
            with open(f"/proc/{proc.pid}/stat", "rb") as fh:
                parts = fh.read().split()
            snap["cpu_s"] = round((int(parts[13]) + int(parts[14])) / os.sysconf("SC_CLK_TCK"), 1)
        except Exception:
            pass
    return snap


def stop_chat_job(chat_id: str, reason: str = "Interrompido pelo utilizador") -> bool:
    with TURN_QUEUE.lock:
        with TURN_QUEUE.lock:
            for task in TURN_QUEUE.tasks:
                if task['chat_id'] == chat_id and task['status'] in (('running',) if reason == 'steer' else ('queued', 'running', 'interrupted')):
                    task.update(status='stopped', reason=reason)
            TURN_QUEUE.save()
        with JOBS_LOCK:
            jobs = [j for j in ACTIVE_JOBS.values() if j.get('chat_id') == chat_id and j.get('status') == 'running']
            for j in jobs:
                j.update(status='stopped', stop_reason=reason)
        all_jobs = jobs + [child for parent in jobs for child in parent.get('children', [])]
        for child in all_jobs:
            child.update(status='stopped', stop_reason=reason)
        for j in all_jobs:
            proc = j.get('proc')
            if proc:
                try:
                    if os.getpgid(proc.pid) == proc.pid:
                        os.killpg(proc.pid, signal.SIGTERM)
                    else:
                        proc.terminate()
                    proc.wait(timeout=1.5)
                except Exception:
                    try:
                        if os.getpgid(proc.pid) == proc.pid:
                            os.killpg(proc.pid, signal.SIGKILL)
                        else:
                            proc.kill()
                    except Exception:
                        pass
        return bool(jobs)

def execute_agent_turn(engine_choice: str, full_prompt: str, raw_user_text: str, cwd: str, images: list[str], plan: dict, folder_id: str = "nas", job: dict | None = None) -> dict:
    t0 = time.time()
    _ho_chat = "" if plan.get("_workflow_internal") else ((job or {}).get("chat_id") or "")
    selection = MODELS.resolve(plan["model_id"], plan.get("effort")) if plan.get("model_id") else None
    try:
        _ho_block = handover_prompt_block(_ho_chat)
        if _ho_block:
            full_prompt = _ho_block + "\n\n---\n\n" + full_prompt
            _push_job_step(job, "🧠 Contexto de passagem carregado do turno anterior")
    except Exception:
        pass
    env = dict(os.environ)
    env["PATH"] = f"{M.NODE_BIN}:{M.NPM_BIN}:{M.LOCAL_BIN}:{env.get('PATH', '/usr/bin:/bin')}"
    work_cwd = cwd if os.path.isdir(cwd) else str(HOME)

    chosen = engine_choice
    if chosen == "auto":
        # O plano já resolveu o motor efetivo (e validou a disponibilidade).
        # Ler sempre daqui evita que o cartão ao vivo e o painel digam coisas
        # diferentes sobre quem está a trabalhar.
        rec = plan.get("exec_engine") or plan.get("recommended_engine")
        if rec in ("agy", "deepseek", "sonnet", "opus", "claude-sonnet", "claude-opus", "haiku", "fable", "codex"):
            chosen = rec
        else:
            active = M.choose_active_maestro()
            chosen = active["engine"]

    canonical_chosen = chosen.removeprefix('claude-')
    if selection and selection['engine'] != canonical_chosen:
        raise ValueError('O modelo selecionado não corresponde ao motor')
    if plan.get('read_only'):
        if not selection:
            candidate = next((e for e in MODELS.catalog()['models'] if e['engine'] == canonical_chosen and e.get('read_only_enforced')), None)
            if not candidate:
                raise ValueError('Motor sem restrição de leitura verificada')
            selection = MODELS.resolve(candidate['id'], plan.get('effort'))
        if not selection.get('read_only_enforced'):
            raise ValueError('Motor sem restrição de leitura verificada')
    if selection and selection.get('transport') == 'deepseek-api':
        if job is not None:
            job['agent_label'] = 'Motor · DeepSeek API (' + selection['model'] + ')'
        res = READONLY.run(full_prompt, work_cwd, model=selection['model'],
                           effort=selection.get('effort'),
                           allowed_roots=[MAESTRO_DIR],
                           on_step=lambda text: _push_job_step(job, text),
                           stopped=lambda: bool(job and job.get('status') == 'stopped'))
        if not plan.get('_workflow_internal'):
            M.update_shared_context(raw_user_text, dict(plan, exec_engine='deepseek', exec_engine_name=res['agent_used']))
            if job:
                write_handover({'ts': time.time(), 'chat_id': job.get('chat_id'), 'engine': 'deepseek',
                               'agent_label': res['agent_used'], 'project': folder_id, 'status': 'ok' if res.get('rc') == 0 else 'falha',
                               'objective': raw_user_text[:600], 'summary': res['text'][-1800:], 'cwd': work_cwd,
                               'next_step': 'Continuar a partir da resposta e das leituras confirmadas.'})
        M.record_agent_history(worker=res['agent_used'], prompt_preview=raw_user_text,
                               cwd=work_cwd, elapsed_s=round(time.time()-t0, 1), rc=res.get('rc', 1),
                               chat_id=(job or {}).get('chat_id', ''), tools=res.get('tools', []), summary=res['text'][:240])
        return dict(res, elapsed_s=round(time.time()-t0, 1), plan=dict(plan, exec_engine='deepseek',
                    exec_engine_name=res['agent_used'].removeprefix('Motor · '), model_id=selection['id'],
                    effort=selection.get('effort')))
    tools_used = []
    agent_label = "Motor · Antigravity"
    _motor_name = selection['label'] if selection else M.ENGINE_DISPLAY.get(chosen, chosen)
    _plan_line = (
        f"🧭 Plano: {plan.get('domain', 'nas')} ({plan.get('dimension', 'media')}) "
        f"· motor: {_motor_name}"
    )
    _maestro_short = str(plan.get("maestro_name") or "").split(" (")[0]
    if plan.get("engine_origin") == "task_route" and _maestro_short:
        _plan_line += f" · suplente disponível: {_maestro_short}"
    _push_job_step(job, _plan_line)
    if images:
        _push_job_step(job, f"🖼️ A processar {len(images)} imagem(ns) anexada(s)…")

    def run_cmd_capture(cmd_list: list[str], timeout_s: int | None = None) -> tuple[int, str, str]:
        # Publicar o motor real antes de arrancar, incluindo após failover.
        if job is not None:
            with JOBS_LOCK:
                live_plan = job.setdefault("plan", {})
                if live_plan.get("exec_engine") != chosen:
                    live_plan["engine_origin"] = "failover"
                live_plan["exec_engine"] = chosen
                live_plan["exec_engine_name"] = agent_label.removeprefix("Motor · ")
        limit = AGENT_TIMEOUT_S if timeout_s is None else int(timeout_s)
        if job is not None and job.get('status') == 'stopped':
            raise RuntimeError('Interrompido antes de iniciar o processo')
        if plan.get("read_only"):
            cmd_list = [arg for arg in cmd_list if arg != "--dangerously-skip-permissions"]
        p = subprocess.Popen(
            cmd_list,
            cwd=work_cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if job is not None:
            with JOBS_LOCK:
                job["proc"] = p
                if job.get('status') == 'stopped':
                    try:
                        os.killpg(p.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                job["last_output_ts"] = time.time()
                job.setdefault("out_bytes", 0)
        try:
            if job is None:
                # limit <= 0 significa "sem limite": espera até o processo terminar.
                out, err = p.communicate(timeout=(limit if limit > 0 else None))
                return p.returncode, out or "", err or ""
            # Com job: ler à medida que sai, para o UI mostrar o que está a acontecer.
            out_chunks: list[str] = []
            err_chunks: list[str] = []

            def _pump(pipe, sink):
                try:
                    for line in pipe:
                        if not line:
                            break
                        sink.append(line)
                        clean = line.strip()
                        if not clean:
                            continue
                        with JOBS_LOCK:
                            job["last_line"] = clean[:240]
                            job["last_output_ts"] = time.time()
                            job["out_bytes"] = job.get("out_bytes", 0) + len(line)
                except Exception:
                    pass

            t_out = threading.Thread(target=_pump, args=(p.stdout, out_chunks), daemon=True)
            t_err = threading.Thread(target=_pump, args=(p.stderr, err_chunks), daemon=True)
            t_out.start(); t_err.start()
            started = time.time()
            while p.poll() is None:
                if job.get("status") == "stopped":
                    break
                if limit > 0 and (time.time() - started) > limit:
                    p.kill()
                    raise AgentTimeout(limit)
                time.sleep(0.25)
            try:
                p.wait(timeout=5)
            except Exception:
                p.kill()
            t_out.join(timeout=3); t_err.join(timeout=3)
            return (p.returncode or 0), "".join(out_chunks), "".join(err_chunks)
        except subprocess.TimeoutExpired:
            p.kill()
            raise AgentTimeout(limit)

    rc = 1
    reply_text = ""
    stderr_text = ""

    try:
        if chosen == "agy":
            agent_label = "Motor · Antigravity"
            if job is not None:
                with JOBS_LOCK:
                    job["agent_label"] = agent_label
            agy_bin = str(M.LOCAL_BIN / "agy")
            cmd = [
                agy_bin,
                "--dangerously-skip-permissions",
                "--output-format", "stream-json",
                "--add-dir", "/srv/cloud4tb/WORK/DEVELOPMENT",
                "--add-dir", "/srv/appdata/0_SERVER/2_CONFIG",
                "--add-dir", str(PASTES_DIR),
                "-p", full_prompt,
            ]
            if selection and selection['engine'] == 'agy':
                cmd.extend(MODELS.command_config(selection))
                agent_label = 'Motor · Antigravity (' + selection['model'] + ')'
                if job is not None:
                    job['agent_label'] = agent_label
            _push_job_step(job, agent_label + " a analisar o pedido…")
            p = subprocess.Popen(
                cmd,
                cwd=work_cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            if job is not None:
                with JOBS_LOCK:
                    job["proc"] = p
                    if job.get('status') == 'stopped':
                        try:
                            os.killpg(p.pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass

            conv_id = ""
            seen_steps: set[int] = set()
            partial_chunks: list[str] = []
            final_resp = ""
            raw_lines: list[str] = []

            # O agy lê stdout até ao EOF: sem watchdog, um processo pendurado
            # ficava eternamente à espera. O timer mata-o ao fim do limite.
            agy_timed_out = {"flag": False}

            def _agy_timeout_kill():
                agy_timed_out["flag"] = True
                _push_job_step(job, "⏱ Tempo limite atingido — a terminar o agente…")
                try:
                    p.kill()
                except Exception:
                    pass

            watchdog = None
            if AGENT_TIMEOUT_S > 0:
                watchdog = threading.Timer(AGENT_TIMEOUT_S, _agy_timeout_kill)
                watchdog.daemon = True
                watchdog.start()

            stop_watcher = threading.Event()

            def _transcript_poller():
                while not stop_watcher.is_set():
                    if conv_id and job is not None:
                        _tail_agy_transcript(conv_id, job, seen_steps)
                    time.sleep(0.75)

            poller_thread = threading.Thread(target=_transcript_poller, daemon=True)
            poller_thread.start()

            try:
                assert p.stdout is not None
                for line in p.stdout:
                    if job is not None and job.get("status") == "stopped":
                        break
                    sline = line.strip()
                    if not sline:
                        continue
                    if not sline.startswith("{"):
                        raw_lines.append(sline)
                        continue
                    try:
                        ev = json.loads(sline)
                    except Exception:
                        raw_lines.append(sline)
                        continue
                    ev_type = ev.get("event")
                    if ev_type == "init":
                        conv_id = str(ev.get("conversation_id") or "")
                        _push_job_step(job, "⚡ Sessão iniciada · A inspecionar workspace e ferramentas…")
                    elif ev_type == "step_update":
                        su = ev.get("step_update") or {}
                        if not conv_id and su.get("conversation_id"):
                            conv_id = str(su.get("conversation_id"))
                        if conv_id and job is not None:
                            _tail_agy_transcript(conv_id, job, seen_steps)
                        td = su.get("text_delta") or ""
                        if td:
                            partial_chunks.append(td)
                            if job is not None:
                                with JOBS_LOCK:
                                    job["partial_text"] = "".join(partial_chunks).strip()
                            _push_job_step(job, "✍️ A redigir resposta final…")
                    elif ev_type == "result":
                        res_obj = ev.get("result") or {}
                        if res_obj.get("response"):
                            final_resp = str(res_obj.get("response")).strip()
                p.wait(timeout=30)
            finally:
                stop_watcher.set()
                if watchdog is not None:
                    watchdog.cancel()

            if agy_timed_out["flag"]:
                raise AgentTimeout(AGENT_TIMEOUT_S)

            rc = p.returncode or 0
            stderr_text = (p.stderr.read() if p.stderr else "").strip()
            reply_text = (final_resp or "".join(partial_chunks).strip() or "\n".join(raw_lines).strip())
            tools_used.append("Motor: " + agent_label.removeprefix("Motor · "))
            if images:
                tools_used.append(f"Visão: {len(images)} imagem(ns)")
            if job is not None and job.get("status") == "stopped":
                reply_text = reply_text or f"⏹ Execução interrompida ({job.get('stop_reason', 'a pedido')})."
            elif rc != 0 or not reply_text:
                sub = M.choose_active_maestro(force_substitute=True)
                chosen = sub["engine"]
                selection = None
                tools_used.append(f"Failover ➔ {sub['name']}")
                _push_job_step(job, f"🔄 Failover automático para {sub['name']}…")

        if chosen in ("claude-sonnet", "claude-opus", "sonnet", "opus", "haiku", "fable"):
            model = chosen.removeprefix("claude-")
            if selection and selection["engine"] in ("sonnet", "claude-sonnet", "opus", "claude-opus", "haiku", "fable"):
                model = selection["model"]
            cq = M.get_claude_quota()
            if not selection and model == "opus" and cq["pace_status"] in ("limitado", "critico", "acima_do_ritmo"):
                model = "sonnet"
                tools_used.append(f"Pace Budget: Opus➔Sonnet ({cq['seven_day_pct']:.0f}%)")
            agent_label = f"Motor · Claude {model.capitalize()}"
            if job is not None:
                with JOBS_LOCK:
                    job["agent_label"] = agent_label
            _push_job_step(job, f"🎭 A executar com Claude {model.capitalize()}…")
            claude_bin = str(M.NODE_BIN / "claude") if (M.NODE_BIN / "claude").exists() else str(M.NPM_BIN / "claude")
            cmd = [
                claude_bin, "-p", full_prompt,
                "--model", model,
                "--dangerously-skip-permissions",
                "--add-dir", str(PASTES_DIR),
                "--add-dir", "/srv/cloud4tb/WORK/DEVELOPMENT",
            ]
            if selection:
                # Remove base model flags before applying the verified provider adapter.
                model_at = cmd.index("--model")
                del cmd[model_at:model_at+2]
                cmd.extend(MODELS.command_config(selection, read_only=bool(plan.get("read_only"))))
                if plan.get("no_tools"):
                    tools_at = cmd.index("--tools")
                    cmd[tools_at+1] = ""
            rc, out, err = run_cmd_capture(cmd)
            reply_text = out.strip()
            stderr_text = err.strip()
            tools_used.append(f"Motor: Claude {model.capitalize()}")

        elif chosen == "codex":
            codex_model = selection["model"] if selection and selection["engine"] == "codex" else "gpt-6.1-sol"
            agent_label = f"Motor · Codex ({codex_model})"
            if job is not None:
                with JOBS_LOCK:
                    job["agent_label"] = agent_label
            _push_job_step(job, f"A executar com OpenAI Codex ({codex_model})…")
            codex_bin = str(M.NPM_BIN / "codex") if (M.NPM_BIN / "codex").exists() else str(M.LOCAL_BIN / "codex")
            cmd = [
                codex_bin, "exec",
                # O Maestro corre fora de um repo git; sem isto o Codex recusa o turno.
                "--skip-git-repo-check",
                "-s", "read-only" if plan.get("read_only") else "danger-full-access",
                "--add-dir", str(PASTES_DIR),
                "-C", work_cwd,
            ]
            cmd.extend(["-m", codex_model])
            if plan.get('read_only'):
                cmd.extend(['-c', 'approval_policy="never"'])
            if selection and selection["engine"] == "codex" and selection.get("effort"):
                cmd.extend(["-c", "model_reasoning_effort=" + json.dumps(selection["effort"])])
            for img in images:
                cmd.extend(["-i", img])
            cmd.extend(["--", full_prompt])
            rc, out, err = run_cmd_capture(cmd)
            reply_text = out.strip()
            stderr_text = err.strip()
            tools_used.append(f"Motor: Codex ({codex_model})")

        elif chosen == "deepseek":
            agent_label = "Motor · DeepSeek V3.2 CLI"
            if job is not None:
                with JOBS_LOCK:
                    job["agent_label"] = agent_label
            _push_job_step(job, "🔮 A executar com DeepSeek V3.2 CLI…")
            dsh_bin = str(M.NPM_BIN / "dsh")
            ds_key = M._extract_deepseek_key()
            if ds_key:
                env["DEEPSEEK_API_KEY"] = ds_key
            env["DSH_TELEMETRY_MODE"] = "DISABLED"
            cmd = [dsh_bin, "headless", "--approval-policy", "never", full_prompt]
            rc, out, err = run_cmd_capture(cmd)
            reply_text = out.strip()
            stderr_text = err.strip()
            tools_used.append("Motor: DeepSeek CLI (API)")

    except AgentTimeout as e:
        rc = 124
        _mins = e.seconds // 60 if e.seconds else 0
        _hint = (
            f" ({_mins} min)"
            if e.seconds
            else ""
        )
        reply_text = (
            f"⚠ A execução excedeu o tempo limite{_hint}. "
            "Podes aumentar o limite com a variável de ambiente "
            "`MAESTRO_AGENT_TIMEOUT_S` (em segundos; 0 = sem limite) e reiniciar o servidor."
        )
    except subprocess.TimeoutExpired:
        rc = 124
        reply_text = "⚠ A execução excedeu o tempo limite."
    except Exception as e:
        rc = 1
        reply_text = f"⚠ Erro ao executar o agente ({chosen}): {e}"

    if not reply_text:
        rc = rc or 1
        reply_text = stderr_text[-1200:] if stderr_text else "Sem resposta de texto devolvida pelo agente."

    if plan.get('read_only') and any(marker in (reply_text + stderr_text) for marker in
                                      ('RTM_NEWADDR', 'bwrap: setting up uid map', 'filesystem-restricted execution requires bubblewrap')):
        rc = 78
    # Auto-capturar blocos de código grandes como Artifacts na pasta atual
    for m_code in ([] if plan.get('_workflow_internal') or plan.get('read_only') else re.finditer(r"```([a-zA-Z0-9_+-]+)\n([\s\S]{120,}?)```", reply_text)):
        lang = m_code.group(1).lower()
        code_body = m_code.group(2).strip()
        kind = "html" if lang in ("html", "svg") else "code"
        F.save_artifact({
            "title": f"{raw_user_text[:36]} ({lang})",
            "folder": folder_id,
            "kind": kind,
            "lang": lang,
            "content": code_body,
        })

    # Motor que realmente correu (pode ter mudado por failover): é este que o
    # painel, o histórico e o contexto partilhado devem mostrar, não a intenção
    # inicial do plano.
    if "DeepSeek" in agent_label:
        _exec_key = "deepseek"
    elif "Codex" in agent_label:
        _exec_key = "codex"
    elif "Claude" in agent_label:
        _exec_key = model
    else:
        _exec_key = "agy"
    _exec_name = agent_label.replace("Motor · ", "").strip() or plan.get("exec_engine_name")
    _exec_origin = plan.get("engine_origin") or "task_route"
    if _exec_key != plan.get("exec_engine"):
        _exec_origin = "failover"
    plan = dict(plan)
    plan.update({
        "exec_engine": _exec_key,
        "exec_engine_name": _exec_name,
        "engine_origin": _exec_origin,
    })

    elapsed = round(time.time() - t0, 1)
    if not plan.get("_workflow_internal"):
        M.update_shared_context(raw_user_text, plan)

    if job is not None and not plan.get("_workflow_internal"):
        try:
            write_handover({
                "ts": time.time(),
                "chat_id": job.get("chat_id"),
                "engine": chosen,
                "agent_label": agent_label,
                "project": folder_id,
                "status": "ok" if rc == 0 else "falha",
                "rc": rc,
                "objective": (raw_user_text or "")[:600],
                "plan": plan,
                "summary": (reply_text or "")[-1800:],
                "next_step": "Continuar a partir do último turno (o histórico do chat já tem o resto).",
                "cwd": work_cwd,
                "elapsed_s": elapsed,
            })
        except Exception:
            pass

    if job is not None:
        with JOBS_LOCK:
            steps_done = list(job.get("steps") or [])
            for s in steps_done[-6:]:
                if s not in tools_used:
                    tools_used.append(s)

    M.record_agent_history(
        worker=agent_label,
        prompt_preview=raw_user_text,
        cwd=work_cwd,
        elapsed_s=elapsed,
        rc=rc,
        chat_id=(job.get("chat_id") if job else ""),
        tools=tools_used,
        summary=reply_text[:240],
    )

    if job is not None:
        with JOBS_LOCK:
            jp = job.setdefault("plan", {})
            jp["exec_engine"] = _exec_key
            jp["exec_engine_name"] = _exec_name
            jp["engine_origin"] = _exec_origin

    return {
        "rc": rc,
        "model_id": (selection or {}).get("id"),
        "effort": (selection or {}).get("effort"),
        "text": reply_text,
        "agent_used": agent_label,
        "elapsed_s": elapsed,
        "plan": {
            "domain": plan["domain"],
            "dimension": plan["dimension"],
            "agents": plan["agents"],
            "reason": plan["reason"],
            "exec_engine": _exec_key,
            "exec_engine_name": _exec_name,
            "engine_origin": _exec_origin,
            "maestro_name": plan.get("maestro_name"),
            "model_id": (selection or {}).get("id"),
            "effort": (selection or {}).get("effort"),
        },
        "tools": tools_used,
    }


def start_async_chat_job(chat: dict, eng_key: str, full_prompt: str, user_text: str, cwd: str, images: list[str], plan: dict, proj_key: str) -> dict:
    cid = chat["id"]
    task_id = plan.get("turn_id")
    job_key = task_id or cid
    if not task_id:
        stop_chat_job(cid, reason="Nova orientação recebida")
    if eng_key == "auto":
        _plan_engine = plan.get("exec_engine") or plan.get("recommended_engine") or "agy"
        _engine_name = plan.get("exec_engine_name") or M.ENGINE_DISPLAY.get(_plan_engine, _plan_engine)
    else:
        _plan_engine = eng_key
        _engine_name = M.ENGINE_DISPLAY.get(eng_key, eng_key)
    job = {
        "chat_id": cid,
        "task_id": task_id,
        "project": proj_key,
        "status": "running",
        "t0": time.time(),
        "agent_label": f"Motor · {_engine_name}",
        "maestro_label": str(plan.get("maestro_name") or "").split(" (")[0],
        "engine_origin": plan.get("engine_origin") or "maestro_hierarchy",
        "current_step": "🚀 A iniciar orquestração Maestro…",
        "steps": ["🚀 A iniciar orquestração Maestro…"],
        "partial_text": "",
        "last_line": "",
        "last_output_ts": time.time(),
        "out_bytes": 0,
        "plan": {
            "domain": plan["domain"],
            "dimension": plan["dimension"],
            "multi_agent": plan.get('multi_agent', False),
            "agents": plan["agents"],
            "exec_engine": _plan_engine,
            "exec_engine_name": _engine_name,
            "engine_origin": (plan.get("engine_origin") if eng_key == "auto" else "user_choice"),
            "maestro_name": plan.get("maestro_name"),
        },
        "proc": None,
    }
    with JOBS_LOCK:
        ACTIVE_JOBS[job_key] = job

    def _worker():
        try:
            if WORKFLOW.needs_workflow(user_text, plan):
                res = RUNTIME.run_turn(sys.modules[__name__], eng_key, full_prompt, user_text, cwd, images, plan, proj_key, job)
            else:
                res = execute_agent_turn(eng_key, full_prompt, user_text, cwd, images, plan, folder_id=proj_key, job=job)
            failed = res.get("rc", 0) != 0
        except Exception as exc:
            failed = True
            res = {"text": f"Falha na execução: {type(exc).__name__}: {exc}", "agent_used": job["agent_label"],
                   "elapsed_s": round(time.time()-job["t0"], 1), "plan": job["plan"], "tools": []}
        with JOBS_LOCK:
            was_stopped = job.get("status") == "stopped"
        if was_stopped and job.get("stop_reason") == "steer":
            return
        with CHAT_STORAGE_LOCK:
            latest_chat = load_chat(cid) or chat
            message = {"role": "assistant", "text": res["text"], "agent_used": res["agent_used"],
                       "elapsed_s": res["elapsed_s"], "plan": res["plan"], "tools": res["tools"],
                       "ts": time.time(), "turn_id": task_id}
            messages = latest_chat.setdefault("messages", [])
            # Keep request/reply pairs in submission order even when workers finish out of order.
            index = next((i+1 for i,m in enumerate(messages) if task_id and m.get("turn_id") == task_id and m.get("role") == "user"), len(messages))
            existing = next((i for i,m in enumerate(messages) if task_id and m.get('turn_id') == task_id and m.get('role') == 'assistant'), None)
            if existing is not None:
                messages[existing] = message
            else:
                messages.insert(index, message)
            save_chat(latest_chat)
        if res.get('plan', {}).get('workflow'):
            final_plan = res['plan']
            M.update_shared_context(user_text, final_plan)
            write_handover({'ts': time.time(), 'chat_id': cid, 'engine': final_plan.get('exec_engine'),
                           'agent_label': res['agent_used'], 'project': proj_key,
                           'status': 'falha' if failed else 'ok', 'objective': user_text[:600],
                           'plan': {k: v for k, v in final_plan.items() if k not in ('workflow', 'quotas')},
                           'workflow_path': str(M.STATE_DIR / 'workflows' / (str(task_id) + '.json')),
                           'summary': res['text'][-1800:], 'next_step': 'Consultar o plano persistido; preservar tarefas concluídas.',
                           'cwd': cwd})
        if task_id:
            TURN_QUEUE.update(task_id, status="stopped" if was_stopped else ("interrupted" if failed else "done"), reason="interrupted" if failed else "done")
        with JOBS_LOCK:
            if not was_stopped and job.get("status") == "running":
                job["status"] = "failed" if failed else "done"
                job["current_step"] = "Falhou · resultado parcial guardado" if failed else "✅ Concluído!"

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return get_job_snapshot(cid) or {}


def enqueue_turn(body):
    cid = str(body.get('id') or '')
    with TURN_QUEUE.lock, CHAT_STORAGE_LOCK:
        chat = load_chat(cid) or create_chat(body.get('project') or 'nas', body.get('engine') or 'auto')
        body = dict(body, id=chat['id'])
        if body.get('model_id'):
            selected = MODELS.resolve(body['model_id'], body.get('effort'))
            body.update(engine=selected['engine'], effort=selected.get('effort'))
        body['images'] = M.extract_images(str(body.get('text') or ''), body.get('images') or [])
        task, added = TURN_QUEUE.add(body)
        if added:
            messages = chat.setdefault('messages', [])
            if not messages and body.get('text'):
                chat['title'] = str(body['text'])[:48]
            messages.append({'role': 'user', 'text': str(body.get('text') or ''),
                             'images': body.get('images') or [], 'docs': body.get('docs') or [],
                             'ts': task['ts'], 'turn_id': task['id']})
            save_chat(chat)
    return dict(chat_activity_snapshot(chat['id']), ok=True, **{'async': True})


def chat_activity_snapshot(chat_id):
    # Admission takes the same locks: a response cannot show a task both
    # waiting in the composer and executing in the history.
    with TURN_QUEUE.lock, CHAT_STORAGE_LOCK:
        job = get_job_snapshot(chat_id)
        return {'chat': load_chat(chat_id), 'job': job,
                'running': bool(job and job['status'] == 'running'),
                'queue': TURN_QUEUE.pending(chat_id), 'activity_version': time.monotonic_ns()}


def schedule_turns_once():
    with TURN_QUEUE.lock:
        pending = [dict(t) for t in TURN_QUEUE.tasks if t['status'] == 'queued']
        interrupted = [dict(t) for t in TURN_QUEUE.tasks if t['status'] == 'interrupted']
    blocked_chats = set()
    for task in pending:
        cid = task['chat_id']
        if cid in blocked_chats or (not Q.independent_analysis(task) and any(t['chat_id'] == cid and t['ts'] < task['ts'] for t in interrupted)):
            TURN_QUEUE.update(task['id'], reason='dependency')
            continue
        with JOBS_LOCK:
            running = [dict(j) for j in ACTIVE_JOBS.values() if j.get('status') == 'running']
        task['separate_project'] = bool(task.get('project') and task['project'] != 'nas' and running and all(j.get('project') and j['project'] != 'nas' and j['project'] != task['project'] for j in running))
        # Never launch a queued write alongside another worker, even in another chat.
        if running and not Q.independent_analysis(task):
            TURN_QUEUE.update(task['id'], reason='dependency')
            blocked_chats.add(cid)
            continue
        if len(running) >= 2:
            TURN_QUEUE.update(task['id'], reason='capacity')
            blocked_chats.add(cid)
            continue
        # Classification reads cached quotas. No extra model request is spent planning the queue.
        if task.get('reason') == 'quota' and time.time() - task.get('checked_at', 0) < 30:
            blocked_chats.add(cid)
            continue
        cwd = (get_folder_map().get(task.get('project') or 'nas') or {}).get('cwd') or str(HOME)
        try:
            plan = M.classify_task(task.get('text') or 'Analisa a imagem anexada', cwd, task.get('images') or [])
            chat = load_chat(cid)
            if not chat:
                TURN_QUEUE.update(task['id'], status='cancelled', reason='chat_deleted')
                continue
            history_chars = sum(len(str(m.get('text') or '')) for m in (chat.get('messages') or [])[-10:])
            task['images'] = M.extract_images(task.get('text') or '', task.get('images') or [])
            task['estimated_input_tokens'] = (len(task.get('text') or '') + min(history_chars, 18000) + sum(int(d.get('chars') or 0) for d in task.get('docs') or [])) // 4
            engine, reason = Q.decide(task, plan, running)
            if not engine:
                TURN_QUEUE.update(task['id'], reason=reason, checked_at=time.time())
                blocked_chats.add(cid)
                continue
            with TURN_QUEUE.lock:
                current = next(t for t in TURN_QUEUE.tasks if t['id'] == task['id'])
                if current['status'] != 'queued':
                    continue
                TURN_QUEUE.update(task['id'], status='running', reason=reason, assigned_engine=engine, estimated_input_tokens=task['estimated_input_tokens'])
            label = M.ENGINE_DISPLAY.get(engine, engine)
            plan.update(model_id=task.get('model_id'), effort=task.get('effort'))
            plan.update(exec_engine=engine, exec_engine_name=label, agents=[label], multi_agent=WORKFLOW.needs_workflow(task.get('text') or '', plan),
                        engine_origin='queue_strategy', reason=f'Fila Maestro: {reason} · {label}')
            body = dict(task, id=cid)
            body.update({'async': True, '_turn_id': task['id'], '_plan': plan, '_queue_reason': reason})
            with TURN_QUEUE.lock:
                current = next(t for t in TURN_QUEUE.tasks if t['id'] == task['id'])
                if current['status'] == 'running':
                    dispatch_turn(body)
        except Exception as exc:
            TURN_QUEUE.update(task['id'], status='interrupted', reason='interrupted', error=str(exc)[:500])


def start_turn_scheduler():
    def loop():
        while True:
            try:
                schedule_turns_once()
            except Exception as exc:
                print(f'Queue scheduler: {exc}', flush=True)
            time.sleep(1)
    threading.Thread(target=loop, daemon=True, name='maestro-turn-queue').start()


def dispatch_turn(body):
    cid = str(body.get("id") or "")
    chat = load_chat(cid) or create_chat(body.get("project") or "nas", body.get("engine") or "auto")
    user_text = str(body.get("text") or "").strip()
    images = M.extract_images(user_text, body.get("images") or [])
    proj_key = body.get("project") or chat.get("project") or "nas"
    eng_key = body.get("engine") or chat.get("engine") or "auto"
    is_steer = bool(body.get("steer", False))
    is_async = bool(body.get("async", False))
    chat["project"] = proj_key
    chat["engine"] = eng_key

    if len(chat.get("messages") or []) == 0 and user_text:
        chat["title"] = user_text[:48] + ("…" if len(user_text) > 48 else "")

    prev_steps_note = ""
    if is_steer:
        prev_snap = get_job_snapshot(chat["id"])
        stop_chat_job(chat["id"], reason="steer")
        if prev_snap and prev_snap.get("steps"):
            prev_steps_note = "\n[PASSOS JÁ EXECUTADOS NO TURNO ANTERIOR INTERROMPIDO]: " + " -> ".join(prev_snap["steps"][-5:]) + "\n"

    fmap = get_folder_map()
    cwd = (fmap.get(proj_key) or {}).get("cwd") or str(HOME)
    effective_user_text = (
        f"[🧭 ORIENTAÇÃO EM TEMPO REAL DO HUBERT DURANTE A EXECUÇÃO]: {user_text}{prev_steps_note}"
        if is_steer else (user_text or "Analisa detalhadamente a imagem anexada.")
    )
    plan = body.get("_plan") or M.classify_task(user_text or "Analisa a imagem anexada", cwd, images)
    if body.get("_turn_id"):
        plan["turn_id"] = body["_turn_id"]
        plan["queue_reason"] = body.get("_queue_reason", "ready")
        if body.get("_queue_reason") == "parallel":
            effective_user_text += "\n[Execução paralela autorizada apenas para análise: usa ferramentas de leitura; não alteres ficheiros nem serviços.]"
    if body.get("model_id"):
        selected = MODELS.resolve(body["model_id"], body.get("effort"))
        eng_key = selected["engine"]
        plan.update(model_id=selected["id"], effort=selected.get("effort"))
    if eng_key != "auto":
        plan.update(exec_engine=eng_key,
                    exec_engine_name=M.ENGINE_DISPLAY.get(eng_key, eng_key),
                    engine_origin="user_choice")
    full_prompt = build_conversation_prompt(chat, effective_user_text, images, plan)

    # Anexos de texto colados: o ficheiro já está em disco (PASTES_DIR).
    # Passamos só a referência para o prompt não ficar gigante.
    doc_list = []
    for d in (body.get("docs") or []):
        if not isinstance(d, dict) or not d.get("path"):
            continue
        dpath = os.path.realpath(str(d["path"]))
        if not dpath.startswith(str(DOCS_DIR)) or not os.path.exists(dpath):
            continue
        doc_list.append({
            "path": dpath,
            "name": str(d.get("name") or os.path.basename(dpath))[:80],
            "chars": int(d.get("chars") or os.path.getsize(dpath)),
        })
    if doc_list:
        note = "\n\n[📎 ANEXOS COLADOS PELO HUBERT — ficheiros já gravados no NAS, conteúdo NÃO repetido aqui]\n"
        note += "\n".join(
            f"- {d['name']} ({d['chars']} caracteres) -> {d['path']}" for d in doc_list
        )
        note += (
            "\nLê estes ficheiros com as tuas ferramentas (cat/sed/grep/head) quando precisares do conteúdo; "
            "não peças ao Hubert para voltar a colar. Trabalha sobre o ficheiro se tiveres de o editar.\n"
        )
        full_prompt += note

    if not body.get("_turn_id"):
        plan['turn_id'] = uuid.uuid4().hex
        chat.setdefault("messages", []).append({
            "turn_id": plan['turn_id'],
            "role": "user",
            "text": (f"🧭 [Orientação em Tempo Real] {user_text}" if is_steer else user_text),
            "images": images,
            "docs": doc_list,
            "ts": time.time(),
        })
        save_chat(chat)

    if is_async:
        job_snap = start_async_chat_job(chat, eng_key, full_prompt, user_text, cwd, images, plan, proj_key)
        return {"ok": True, "async": True, "chat": chat, "job": job_snap}

    result = execute_agent_turn(eng_key, full_prompt, user_text, cwd, images, plan, folder_id=proj_key)
    chat["messages"].append({
        "role": "assistant",
        "text": result["text"],
        "agent_used": result["agent_used"],
        "elapsed_s": result["elapsed_s"],
        "plan": result["plan"],
        "tools": result["tools"],
        "ts": time.time(),
    })
    save_chat(chat)
    return {"chat": chat}


APP_HTML = r"""<!DOCTYPE html>
<html lang="pt">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#050912">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Maestro AI">
<link rel="manifest" href="/manifest.json" crossorigin="use-credentials">
<link rel="icon" type="image/svg+xml" href="/icon.svg">
<link rel="icon" type="image/png" sizes="192x192" href="/icons/icon-192.png">
<link rel="icon" type="image/png" sizes="512x512" href="/icons/icon-512.png">
<link rel="apple-touch-icon" sizes="180x180" href="/icons/apple-touch-icon.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Chakra+Petch:wght@400;600;700&family=Cinzel:wght@500;700;800&family=Cormorant+Garamond:ital,wght@0,500;0,700;1,500&family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;600;700&family=Orbitron:wght@500;700;800&family=Rajdhani:wght@500;600;700&family=Share+Tech+Mono&family=Space+Grotesk:wght@400;500;700&display=swap" rel="stylesheet">
<title>Maestro AI Studio · Pavilion-NAS</title>
<style>
@font-face {
  font-family: "HD Orbitron";
  src: url("/api/image?path=/home/hubert/hubert-portfolio/public/assets/hd-2026/Orbitron-Variable.woff2") format("woff2");
  font-weight: 400 900; font-style: normal; font-display: swap;
}
@font-face {
  font-family: "HD Plex";
  src: url("/api/image?path=/home/hubert/hubert-portfolio/public/assets/hd-2026/IBMPlexSans-Variable.woff2") format("woff2");
  font-weight: 100 700; font-style: normal; font-display: swap;
}
:root {
  color-scheme: dark;
  --bg: #050912;
  --panel: #0c1322;
  --card: #121c32;
  --card-hover: #192746;
  --glass: rgba(12, 19, 34, 0.85);
  --line: rgba(255, 255, 255, 0.08);
  --line-strong: rgba(56, 189, 248, 0.34);
  --text: #f8fafc;
  --muted: #94a3b8;
  --cyan: #38bdf8;
  --violet: #a78bfa;
  --green: #34d399;
  --amber: #fbbf24;
  --rose: #f43f5e;
  --gal-col: 200px;
  --radius-main: 14px;
  --mono: "JetBrains Mono", "Fira Code", ui-monospace, SFMono-Regular, Consolas, monospace;
  --sans: "Inter", system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  --display: var(--sans);
}
[hidden] { display: none !important; }
* {
  box-sizing: border-box;
  -webkit-tap-highlight-color: transparent;
  scrollbar-width: thin;
  scrollbar-color: rgba(56, 189, 248, 0.32) rgba(5, 9, 18, 0.35);
}
/* ULTRA-CLEAN CUSTOM SCROLLBARS (NO WHITE TRACK, NO ARROWS) */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-button { display: none !important; width: 0; height: 0; }
::-webkit-scrollbar-track { background: rgba(5, 9, 18, 0.4); border-radius: 999px; }
::-webkit-scrollbar-corner { background: transparent; }
::-webkit-scrollbar-thumb {
  background: linear-gradient(180deg, rgba(56, 189, 248, 0.36), rgba(167, 139, 250, 0.36));
  border-radius: 999px;
  border: 1px solid rgba(255, 255, 255, 0.08);
}
::-webkit-scrollbar-thumb:hover {
  background: linear-gradient(180deg, var(--cyan), var(--violet));
}

/* CUSTOM NEON RANGE SLIDERS & COLOR PICKERS */
input[type="range"].ui-slider {
  -webkit-appearance: none; appearance: none;
  height: 6px; border-radius: 999px;
  background: linear-gradient(90deg, rgba(56,189,248,.42), rgba(167,139,250,.42));
  border: 1px solid var(--line); outline: none; cursor: pointer;
}
input[type="range"].ui-slider::-webkit-slider-thumb {
  -webkit-appearance: none; appearance: none;
  width: 16px; height: 16px; border-radius: 50%;
  background: var(--cyan); border: 2px solid #fff;
  box-shadow: 0 0 10px rgba(56,189,248,.75);
  cursor: pointer; transition: transform .15s ease, background .15s ease;
}
input[type="range"].ui-slider::-webkit-slider-thumb:hover {
  transform: scale(1.18); background: #fff;
}
input[type="color"] {
  -webkit-appearance: none; appearance: none;
  border: 1px solid var(--line-strong) !important; border-radius: 8px;
  padding: 2px; background: var(--card) !important; cursor: pointer;
}
input[type="color"]::-webkit-color-swatch-wrapper { padding: 0; }
input[type="color"]::-webkit-color-swatch { border: none; border-radius: 6px; }

html, body {
  margin: 0; padding: 0; width: 100%; height: 100%;
  background: var(--bg); color: var(--text); color-scheme: dark;
  font-family: var(--sans); overflow: hidden;
  -webkit-font-smoothing: antialiased;
}

/* ANIMATED TRANSITIONS */
@keyframes fadeSlideUp {
  from { opacity: 0; transform: translateY(10px) scale(0.992); }
  to { opacity: 1; transform: translateY(0) scale(1); }
}
@keyframes glowPulse {
  0%, 100% { filter: drop-shadow(0 0 10px rgba(56,189,248,.4)); }
  50% { filter: drop-shadow(0 0 20px rgba(167,139,250,.7)); }
}
.anim-in { animation: fadeSlideUp 0.24s cubic-bezier(.22,1,.36,1) both; }

/* STUDIO SHELL */
.studio {
  display: flex; width: 100%; height: 100dvh; position: relative; overflow: hidden;
  background:
    radial-gradient(65% 50% at 50% 0%, rgba(56,189,248,0.08), transparent 70%),
    radial-gradient(50% 45% at 85% 15%, rgba(167,139,250,0.06), transparent 70%),
    var(--bg);
}

/* LEFT SIDEBAR (NAVIGATION + COLORED FOLDERS + HISTORY) */
.sidebar-left {
  width: 310px; background: var(--panel); border-right: 1px solid var(--line);
  display: flex; flex-direction: column; flex-shrink: 0; z-index: 45;
  overflow: hidden;
  transition: transform 0.28s cubic-bezier(.22,1,.36,1), width 0.22s ease;
}
.sidebar-left.collapsed { width: 0; overflow: hidden; border-right: 0; }
.sidebar-left:focus, .inspector-right:focus { outline: none; }
.brand-bar {
  padding: 14px 16px; display: flex; align-items: center; gap: 11px;
  border-bottom: 1px solid var(--line);
}
.brand-bar img { width: 38px; height: 38px; animation: glowPulse 4s infinite ease-in-out; }
.brand-title {
  font-weight: 800; font-size: 1.05rem; letter-spacing: 0.02em;
  background: linear-gradient(95deg, #fff 10%, #7dd3fc 55%, #c084fc 95%);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
}
.brand-sub { display: block; font-size: 0.67rem; color: var(--muted); font-weight: 500; }
.brand-title { display: block; }
button.brand-bar {
  width: 100%; background: transparent; color: inherit; font-family: inherit;
  text-align: left; cursor: pointer; border: 0; border-bottom: 1px solid var(--line);
}
button.brand-bar:hover { background: rgba(255,255,255,.035); }
button.brand-bar:focus-visible { outline: 2px solid var(--cyan); outline-offset: -2px; }
button.brand-bar > span { min-width: 0; }

/* PRIMARY MODULE SWITCHER IN SIDEBAR */
.nav-modules {
  display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 4px;
  padding: 10px 12px 6px;
}
.nav-mod-btn {
  min-width: 0; padding: 8px 2px; border-radius: 11px; border: 1px solid var(--line);
  background: rgba(255,255,255,.05); color: #cbd5e1;
  display: flex; flex-direction: column; align-items: center; gap: 3px;
  font-size: 0.63rem; font-weight: 700; cursor: pointer; transition: all .16s ease;
}
.nav-mod-btn span.ico { font-size: 1.08rem; }
.nav-mod-btn:hover { background: rgba(255,255,255,.06); color: #fff; transform: translateY(-1px); }
.nav-mod-btn.active {
  background: linear-gradient(135deg, rgba(56,189,248,.22), rgba(167,139,250,.18));
  border-color: var(--cyan); color: #fff; box-shadow: 0 6px 16px rgba(56,189,248,.16);
}

.new-chat-cta {
  margin: 6px 12px 8px; padding: 10px 14px; border-radius: 12px;
  border: 1px solid rgba(56,189,248,.42);
  background: linear-gradient(135deg, rgba(56,189,248,.22), rgba(99,102,241,.18));
  color: #fff; font-weight: 700; font-size: 0.84rem; cursor: pointer;
  display: flex; align-items: center; justify-content: space-between;
  transition: transform .15s, border-color .15s;
}
.new-chat-cta:hover { transform: translateY(-1px); border-color: var(--cyan); }
.kbd-hint {
  font-family: var(--mono); font-size: 0.63rem; padding: 2px 6px; border-radius: 5px;
  background: rgba(0,0,0,.35); color: #cbd5e1; border: 1px solid var(--line);
}

/* COLLAPSIBLE COLORED FOLDERS ACCORDION & COMPACT DRAWER */
.sec-label-row {
  padding: 4px 14px; display: flex; align-items: center; justify-content: space-between;
  font-size: 0.68rem; font-weight: 700; color: var(--muted); letter-spacing: 0.04em;
}
.sec-label-row button {
  background: none; border: 0; color: var(--cyan); font-size: 0.72rem; font-weight: 700; cursor: pointer;
}
.colored-folders {
  padding: 2px 12px 6px; display: flex; flex-direction: column; gap: 6px;
}
.folder-acc-head {
  display: flex; align-items: center; justify-content: space-between; gap: 6px;
  padding: 8px 10px; border-radius: 11px; border: 1px solid var(--line-strong);
  background: rgba(255, 255, 255, 0.05); cursor: pointer; transition: all .16s ease;
  font-family: var(--sans); width: 100%;
}
.folder-acc-head:hover {
  background: rgba(255, 255, 255, 0.08); border-color: var(--cyan);
}
.folder-acc-cur {
  display: flex; align-items: center; gap: 7px; min-width: 0; flex: 1;
  font-size: 0.76rem; font-weight: 700; color: var(--text);
}
.folder-acc-cur .txt { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.folder-acc-meta { display: flex; align-items: center; gap: 5px; flex-shrink: 0; }
.folder-acc-cnt {
  font-family: var(--mono); font-size: 0.65rem; padding: 1px 6px; border-radius: 999px;
  background: rgba(56, 189, 248, 0.14); color: var(--cyan); border: 1px solid rgba(56, 189, 248, 0.28);
}
.folder-acc-chev { font-size: 0.72rem; color: var(--muted); transition: transform .2s ease; }
.folder-acc-head.open .folder-acc-chev { transform: rotate(180deg); color: var(--cyan); }
.folder-acc-list {
  display: flex; flex-direction: column; gap: 4px; padding: 6px;
  border-radius: 12px; border: 1px solid var(--line);
  background: rgba(4, 8, 16, 0.65); max-height: 320px; overflow-y: auto;
}
.folder-acc-row {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  padding: 7px 9px; border-radius: 9px; border: 1px solid transparent;
  background: rgba(255, 255, 255, 0.025); color: var(--text);
  font-family: var(--sans); font-size: 0.74rem; font-weight: 600; cursor: pointer; text-align: left;
  transition: all .14s ease; width: 100%;
}
.folder-acc-row:hover {
  background: rgba(255, 255, 255, 0.07); border-color: rgba(56, 189, 248, 0.35);
}
.folder-acc-row.active {
  background: rgba(56, 189, 248, 0.14); border-color: var(--cyan); color: #fff;
}
.folder-acc-row-left { display: flex; align-items: center; gap: 7px; min-width: 0; flex: 1; }
.folder-acc-row-left .dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.folder-acc-row-left .fname { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

/* RICH SMART MARKDOWN FORMATTING (TRANSVERSAL A TODOS OS AGENTES) */
.rich-h1, .rich-h2, .rich-h3, .rich-h4 {
  font-family: var(--display); font-weight: 800; color: var(--text);
  margin: 14px 0 6px; line-height: 1.3; letter-spacing: 0.01em;
}
.rich-h1 { font-size: 1.22rem; padding-bottom: 5px; border-bottom: 1px solid var(--line-strong); color: var(--cyan); }
.rich-h2 { font-size: 1.08rem; padding-bottom: 4px; border-bottom: 1px solid var(--line); }
.rich-h3 { font-size: 0.98rem; color: var(--cyan); display: flex; align-items: center; gap: 6px; }
.rich-h4 { font-size: 0.9rem; color: var(--violet); }
.rich-hr {
  border: 0; height: 1px; margin: 12px 0;
  background: linear-gradient(90deg, transparent, var(--line-strong), transparent);
}
.rich-callout {
  margin: 10px 0; padding: 10px 13px; border-radius: 11px;
  border-left: 3px solid var(--cyan); background: rgba(56, 189, 248, 0.08);
  font-size: 0.86rem; line-height: 1.5; color: var(--text);
}
.rich-callout.warn { border-left-color: var(--amber); background: rgba(251, 191, 36, 0.09); }
.rich-callout.err { border-left-color: var(--rose); background: rgba(244, 63, 94, 0.09); }
.rich-callout.ok { border-left-color: var(--green); background: rgba(52, 211, 153, 0.09); }
.rich-list { margin: 6px 0; padding-left: 0; list-style: none; display: flex; flex-direction: column; gap: 5px; }
.rich-p { margin: 6px 0; line-height: 1.55; }
.rich-p a, .rich-table a { color: var(--cyan); text-decoration: underline; text-underline-offset: 2px; }
.rich-check-box { color: var(--green); font-weight: 800; flex-shrink: 0; }
.rich-check.done > span:last-child { color: var(--muted); text-decoration: line-through; }
.rich-list-item {
  display: flex; align-items: flex-start; gap: 8px; line-height: 1.52;
}
.rich-list-bullet {
  color: var(--cyan); font-weight: 800; font-family: var(--mono); font-size: 0.78rem;
  margin-top: 2px; flex-shrink: 0;
}
.rich-kv-key {
  display: inline-block; padding: 1px 6px; margin-right: 4px; border-radius: 5px;
  background: rgba(255, 255, 255, 0.06); border: 1px solid var(--line);
  color: var(--cyan); font-weight: 700; font-size: 0.84em;
}
.rich-table-wrap {
  margin: 10px 0; overflow-x: auto; border-radius: 11px; border: 1px solid var(--line-strong);
  background: rgba(5, 9, 18, 0.55);
}
.rich-table {
  width: 100%; border-collapse: collapse; font-size: 0.8rem; text-align: left;
}
.rich-table th {
  padding: 8px 11px; background: rgba(56, 189, 248, 0.12); color: var(--cyan);
  font-family: var(--mono); font-size: 0.72rem; font-weight: 700; border-bottom: 1px solid var(--line-strong);
}
.rich-table td {
  padding: 7px 11px; border-bottom: 1px solid var(--line); color: var(--text);
}
.rich-table tr:last-child td { border-bottom: 0; }
.rich-table tr:nth-child(even) td { background: rgba(255, 255, 255, 0.02); }

@container (max-width: 470px) {
  .rich-table-wrap { border-radius: 10px; }
  .rich-table thead { display: none; }
  .rich-table, .rich-table tbody, .rich-table tr, .rich-table td { display: block; width: 100%; }
  .rich-table tr { padding: 7px 9px; border-bottom: 1px solid var(--line); }
  .rich-table tr:last-child { border-bottom: 0; }
  .rich-table td { border: 0; padding: 1px 0; font-size: 0.73rem; line-height: 1.35; }
  .rich-table td::before {
    content: attr(data-l) " · "; color: var(--cyan); font-family: var(--mono);
    font-size: 0.58rem; text-transform: uppercase; letter-spacing: 0.06em;
  }
  .rich-table td:first-child::before { content: ""; }
  .rich-table td:first-child { font-weight: 700; padding-bottom: 3px; }
}

/* CONTEXTUAL QUICK-REPLY CHIPS ABOVE COMPOSER */
.context-chips-strip {
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  padding: 2px 2px 4px;
}
.ctx-chip {
  padding: 4px 10px; border-radius: 999px; border: 1px solid rgba(56, 189, 248, 0.32);
  background: rgba(56, 189, 248, 0.08); color: var(--text);
  font-family: var(--sans); font-size: 0.71rem; font-weight: 600;
  cursor: pointer; transition: all .14s ease; white-space: nowrap;
}
.ctx-chip:hover {
  background: rgba(56, 189, 248, 0.2); border-color: var(--cyan); transform: translateY(-1px);
}

/* PULSO MULTI-SELECT & BATCH COMMAND BAR */
.pulse-cmd-bar {
  display: flex; flex-direction: column; gap: 6px; padding: 9px 10px;
  border-radius: 12px; border: 1px solid var(--line-strong);
  background: rgba(56, 189, 248, 0.06); margin-bottom: 4px;
}
.pulse-cmd-row { display: flex; align-items: center; gap: 5px; flex-wrap: wrap; }
.pulse-chk {
  width: 15px; height: 15px; accent-color: var(--cyan); cursor: pointer; flex-shrink: 0;
}

/* FLEET SUB-TABS & PER-AGENT ACTIVITY/HISTORY */
.fleet-subtabs {
  display: flex; gap: 4px; flex-wrap: wrap; padding: 4px;
  border-radius: 10px; background: rgba(0,0,0,0.3); border: 1px solid var(--line);
}
.fleet-subtab {
  flex: 1; min-width: 54px; padding: 5px 6px; border-radius: 7px; border: 0;
  background: transparent; color: var(--muted); font-size: 0.68rem; font-weight: 700;
  cursor: pointer; text-align: center; transition: all .14s;
}
.fleet-subtab.active {
  background: var(--card); color: var(--cyan); box-shadow: 0 2px 8px rgba(0,0,0,0.35);
}
.agent-hist-row {
  padding: 8px 10px; border-radius: 10px; border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.025); display: flex; flex-direction: column; gap: 4px;
  font-size: 0.73rem;
}

/* TOTAL VISUAL WORLDS & ANIMATED BACKGROUND LAYER */
.world-bg-canvas {
  position: fixed; inset: 0; width: 100vw; height: 100dvh;
  pointer-events: none; z-index: 0; opacity: 0.55;
}
.world-fx-overlay {
  position: fixed; inset: 0; width: 100vw; height: 100dvh;
  pointer-events: none; z-index: 1;
}
.sidebar-left, .center-stage, .artifact-canvas, .inspector-right { position: relative; z-index: 5; }

/* WORLD 1: MATRIX DIGITAL RAIN */
html[data-world="matrix"] {
  --bg: #020804; --panel: rgba(3, 15, 7, 0.88); --card: rgba(5, 24, 11, 0.9);
  --card-hover: #093518; --glass: rgba(2, 12, 5, 0.88);
  --line: rgba(0, 255, 65, 0.22); --line-strong: rgba(0, 255, 65, 0.55);
  --text: #d1ffd7; --muted: #4ade80;
  --cyan: #00ff41; --violet: #10b981; --green: #00ff41;
  --sans: "Share Tech Mono", "JetBrains Mono", monospace;
  --display: "Share Tech Mono", "JetBrains Mono", monospace;
}
html[data-world="matrix"] .world-fx-overlay {
  background: repeating-linear-gradient(0deg, rgba(0, 0, 0, 0.22) 0px, rgba(0, 0, 0, 0.22) 1px, transparent 1px, transparent 3px);
}
html[data-world="matrix"] .bubble, html[data-world="matrix"] .stat-card, html[data-world="matrix"] .feature-card {
  border-radius: 4px; box-shadow: 0 0 14px rgba(0, 255, 65, 0.12);
}

/* WORLD 2: SCI-FI ORBITAL HUD */
html[data-world="scifi"] {
  --bg: #020813; --panel: rgba(6, 18, 36, 0.86); --card: rgba(9, 27, 52, 0.88);
  --card-hover: #103768; --glass: rgba(5, 16, 34, 0.86);
  --line: rgba(0, 229, 255, 0.2); --line-strong: rgba(0, 229, 255, 0.52);
  --text: #e0f7fa; --muted: #7dd3fc;
  --cyan: #00e5ff; --violet: #38bdf8;
  --sans: "Rajdhani", "Inter", sans-serif;
  --display: "Orbitron", "Rajdhani", sans-serif;
}
html[data-world="scifi"] .brand-title, html[data-world="scifi"] h1, html[data-world="scifi"] h2 {
  font-family: var(--display); letter-spacing: 0.08em; text-transform: uppercase;
}

/* WORLD 3: CYBERPUNK 2099 NEON */
html[data-world="cyberpunk"] {
  --bg: #090314; --panel: rgba(18, 6, 34, 0.88); --card: rgba(28, 10, 51, 0.9);
  --card-hover: #2b124c; --glass: rgba(16, 5, 30, 0.88);
  --line: rgba(255, 0, 85, 0.28); --line-strong: rgba(252, 238, 10, 0.65);
  --text: #fffbeb; --muted: #f472b6;
  --cyan: #fcee0a; --violet: #ff0055; --green: #00f0ff;
  --sans: "Chakra Petch", "Inter", sans-serif;
  --display: "Orbitron", "Chakra Petch", sans-serif;
}
html[data-world="cyberpunk"] .turn.user .bubble {
  background: linear-gradient(135deg, #ff0055, #7928ca);
}

/* WORLD 4: HESPERIA · ARQUEOFUTURISMO GRECO-ROMANO (CIVITAS AETERNA) */
html[data-world="hesperia"] {
  --bg: #08070d; --panel: rgba(10, 9, 16, 0.92); --card: rgba(22, 20, 30, 0.86);
  --card-hover: #27262d; --glass: rgba(12, 11, 18, 0.84);
  --line: rgba(212, 175, 55, 0.2); --line-strong: rgba(196, 181, 253, 0.45);
  --text: #f4f4f5; --muted: #b8b4c7;
  --cyan: #d4af37; --violet: #a78bfa; --amber: #facc15;
  --sans: "Inter", "Cormorant Garamond", serif;
  --display: "Cinzel", "Cormorant Garamond", serif;
}
html[data-world="hesperia"] .world-fx-overlay {
  background:
    radial-gradient(circle at 78% 8%, rgba(126, 34, 206, 0.16), transparent 32%),
    radial-gradient(circle at 22% 88%, rgba(212, 175, 55, 0.1), transparent 35%),
    linear-gradient(rgba(255, 255, 255, 0.012) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255, 255, 255, 0.012) 1px, transparent 1px);
  background-size: 100% 100%, 100% 100%, 42px 42px, 42px 42px;
}
html[data-world="hesperia"] .brand-title, html[data-world="hesperia"] h1, html[data-world="hesperia"] h2, html[data-world="hesperia"] .sec-label-row {
  font-family: "Cinzel", Georgia, serif; letter-spacing: 0.14em; text-transform: uppercase;
}
html[data-world="hesperia"] .turn.user .bubble {
  background: linear-gradient(135deg, #5b21b6, #92400e); border: 1px solid rgba(212, 175, 55, 0.45);
}

/* WORLD 5: DUNE · ARRAKIS & PLANETA ANIMADO */
html[data-world="dune"] {
  --bg: #0c0704; --panel: rgba(22, 13, 8, 0.86); --card: rgba(34, 20, 12, 0.88);
  --card-hover: #331f14; --glass: rgba(20, 12, 7, 0.86);
  --line: rgba(224, 122, 46, 0.22); --line-strong: rgba(245, 158, 11, 0.5);
  --text: #f7ede2; --muted: #d6b899;
  --cyan: #f59e0b; --violet: #38bdf8; --amber: #e07a2e;
  --sans: "Space Grotesk", "Inter", sans-serif;
  --display: "Cinzel", "Space Grotesk", serif;
}
html[data-world="dune"] .brand-title, html[data-world="dune"] h1, html[data-world="dune"] h2 {
  font-family: "Cinzel", serif; letter-spacing: 0.12em; text-transform: uppercase;
}
html[data-world="dune"] .turn.user .bubble {
  background: linear-gradient(135deg, #b45309, #0369a1);
}

/* WORLD 6: HUBERT DUNGEN WEBSITE · BONE, CHARCOAL DARK GREY & SAND TEXTURE */
html[data-world="dungen"] {
  --bg: #141414; --panel: rgba(25, 25, 24, 0.92); --card: rgba(37, 37, 35, 0.92);
  --card-hover: #3a3a38; --glass: rgba(20, 20, 20, 0.9);
  --line: rgba(214, 208, 196, 0.16); --line-strong: rgba(243, 242, 239, 0.42);
  --text: #f3f2ef; --muted: #c4bfb4;
  --cyan: #d6d0c4; --violet: #a6a6a6; --green: #d6d0c4;
  --sans: "HD Plex", "Space Grotesk", "Inter", sans-serif;
  --display: "HD Orbitron", "Orbitron", "Space Grotesk", sans-serif;
}
html[data-world="dungen"] .world-fx-overlay {
  background-image:
    linear-gradient(rgba(20, 20, 20, 0.78), rgba(20, 20, 20, 0.82)),
    url("/api/image?path=/home/hubert/hubert-portfolio/public/assets/hd-2026/textura_areia.webp");
  background-size: cover, 680px auto;
  opacity: 0.42;
}
html[data-world="dungen"] .brand-title, html[data-world="dungen"] h1, html[data-world="dungen"] h2 {
  font-family: var(--display); letter-spacing: 0.05em; text-transform: uppercase;
  background: linear-gradient(95deg, #f3f2ef 15%, #d6d0c4 65%, #a6a6a6 100%);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
}
html[data-world="dungen"] .turn.user .bubble {
  background: linear-gradient(135deg, #3a3a38, #252523);
  color: #f3f2ef; border: 1px solid rgba(214, 208, 196, 0.4);
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.45);
}
html[data-world="dungen"] .send-circle-btn, html[data-world="dungen"] .new-chat-cta {
  background: linear-gradient(135deg, #d6d0c4, #a6a6a6);
  color: #141414 !important; border-color: #f3f2ef; font-weight: 800;
}

/* ---- APROFUNDAMENTO DOS MUNDOS (camadas animadas e detalhe) ---- */
html[data-world="matrix"] .world-fx-overlay { box-shadow: inset 0 0 160px rgba(0,255,65,.10); }
html[data-world="matrix"] .brand-title { text-shadow: 0 0 14px rgba(0,255,65,.55); }
html[data-world="matrix"] .bubble, html[data-world="matrix"] .stat-card { border-color: rgba(0,255,65,.22); }

html[data-world="scifi"] .world-fx-overlay {
  background-image:
    linear-gradient(rgba(0,229,255,.07) 1px, transparent 1px),
    linear-gradient(90deg, rgba(0,229,255,.07) 1px, transparent 1px);
  background-size: 44px 44px, 44px 44px;
  -webkit-mask-image: radial-gradient(ellipse at 50% 0%, #000 22%, transparent 76%);
  mask-image: radial-gradient(ellipse at 50% 0%, #000 22%, transparent 76%);
}
html[data-world="scifi"] .brand-title { letter-spacing: .18em; text-transform: uppercase; }
html[data-world="scifi"] .stat-card, html[data-world="scifi"] .bubble {
  border-radius: 3px; box-shadow: inset 0 0 0 1px rgba(0,229,255,.08), 0 0 20px rgba(0,229,255,.06);
}

html[data-world="cyberpunk"] .world-fx-overlay::before {
  content: ""; position: absolute; left: -20%; right: -20%; bottom: 0; height: 46%;
  background:
    repeating-linear-gradient(90deg, rgba(255,0,153,.30) 0 1px, transparent 1px 48px),
    linear-gradient(transparent, rgba(255,0,153,.12));
  transform: perspective(340px) rotateX(64deg); transform-origin: bottom center; opacity: .55;
}
html[data-world="cyberpunk"] .brand-title { text-shadow: 2px 0 rgba(255,0,153,.75), -2px 0 rgba(0,229,255,.75); }
html[data-world="cyberpunk"] .bubble { border-color: rgba(255,0,153,.32); box-shadow: 3px 0 0 rgba(0,229,255,.28), -3px 0 0 rgba(255,0,153,.28); }

html[data-world="hesperia"] .world-fx-overlay {
  background-image:
    radial-gradient(circle at 50% -18%, rgba(212,175,55,.14), transparent 62%),
    repeating-linear-gradient(90deg, rgba(212,175,55,.055) 0 1px, transparent 1px 118px);
}
html[data-world="hesperia"] .brand-title { letter-spacing: .12em; }
html[data-world="hesperia"] .bubble { border-color: rgba(212,175,55,.34); border-radius: 2px; }

html[data-world="dune"] .world-fx-overlay::after {
  content: ""; position: absolute; right: -110px; top: 6%; width: 430px; height: 430px; border-radius: 50%;
  background: radial-gradient(circle at 34% 30%, #f6c98a, #c2703a 40%, #7b3d1d 72%, #2a1207 100%);
  box-shadow: 0 0 140px rgba(224,122,46,.22), inset -30px -24px 90px rgba(0,0,0,.68);
  opacity: .45; animation: duneSpin 120s linear infinite;
}
@keyframes duneSpin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
html[data-world="dune"] .bubble { border-color: rgba(224,122,46,.32); }

html[data-world="dungen"] .bubble { border-radius: 3px; }

/* ---- APRESENTAÇÃO ADAPTATIVA DO CHAT (CÓDIGO/CLI vs PROSA) ---- */
#chatScroll[data-style="code"] .bubble {
  font-family: var(--mono); font-size: 0.79rem; line-height: 1.58; border-radius: 6px;
}
#chatScroll[data-style="code"] .turn.assistant .bubble {
  background: rgba(4, 9, 16, 0.94); border-left: 2px solid var(--cyan); border-radius: 4px;
}
#chatScroll[data-style="code"] .turn.user .bubble {
  background: rgba(56, 189, 248, 0.10); border: 1px solid rgba(56, 189, 248, 0.28); border-radius: 4px;
}
#chatScroll[data-style="code"] pre, #chatScroll[data-style="code"] code { font-size: 0.77rem; }
#chatScroll[data-style="code"] .bubble p { margin: 4px 0; }
#chatScroll[data-style="code"] .bubble { max-width: min(100%, 980px); }
#chatScroll[data-style="code"] .turn-foot { font-family: var(--mono); }

.search-wrap { padding: 4px 12px; }
.search-input {
  width: 100%; padding: 8px 11px; border-radius: 10px;
  border: 1px solid var(--line); background: rgba(0,0,0,.3);
  color: #fff; font-size: 0.78rem; outline: none;
}
.search-input:focus { border-color: var(--cyan); }

.chat-list {
  flex: 1; overflow-y: auto; padding: 6px 10px; display: flex; flex-direction: column; gap: 3px;
}
.chat-row {
  padding: 6px 9px; border-radius: 9px; cursor: pointer;
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  border: 1px solid transparent; transition: all .14s;
}
.chat-row:hover { background: rgba(255,255,255,.04); }
.chat-row.active { background: var(--card); border-color: rgba(56,189,248,.32); }
.chat-row-info { min-width: 0; flex: 1; }
.chat-row-title {
  font-size: 0.78rem; font-weight: 600; color: var(--text);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.chat-row-sub {
  font-size: 0.61rem; color: var(--muted); margin-top: 1px;
  display: flex; align-items: center; gap: 6px;
}
.chat-row-preview {
  font-size: .68rem; line-height: 1.4; color: var(--muted); margin-top: 4px;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
  overflow-wrap: anywhere;
}
.chat-row-preview.running { color: var(--cyan); }
.chat-row-step { font-size: .62rem; color: var(--muted); margin-top: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.chat-row-preview.stopped { color: var(--rose); }
.chat-row-preview b { font-weight: 600; }
.chat-row-actions { display: flex; gap: 2px; opacity: 0; transition: opacity .12s; }
.chat-row:hover .chat-row-actions, .chat-row.active .chat-row-actions { opacity: 1; }
.chat-group-head {
  display: flex; align-items: center; gap: 6px; margin: 7px 4px 2px; padding: 2px 4px;
  font-family: var(--mono); font-size: 0.6rem; letter-spacing: .09em; text-transform: uppercase;
  color: var(--muted); cursor: pointer; user-select: none;
}
.chat-group-head:hover { color: var(--text); }
.chat-group-head .chev { width: 9px; opacity: .8; }
.chat-group-head .n {
  margin-left: auto; padding: 0 6px; border-radius: 999px;
  background: rgba(148,163,184,.14); font-size: 0.58rem;
}
.chat-group { display: flex; flex-direction: column; gap: 2px; }
.chat-group.collapsed { display: none; }
.chat-row-actions button {
  background: none; border: 0; color: var(--muted); cursor: pointer; padding: 3px; border-radius: 6px;
}
.chat-row-actions button:hover { color: #fff; }

.sb-footer {
  padding: 10px 12px; border-top: 1px solid var(--line); display: flex; flex-direction: column; gap: 7px;
  background: rgba(5, 8, 17, 0.35);
}
.exe-download-btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 8px;
  min-height: 38px; padding: 8px 14px; border-radius: 11px; text-decoration: none;
  background: linear-gradient(135deg, rgba(52,211,153,.2), rgba(56,189,248,.18));
  border: 1px solid rgba(52,211,153,.48); color: #ecfdf5;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.12), 0 4px 14px rgba(16,185,129,.12);
  font-family: var(--sans); font-size: 0.76rem; font-weight: 700; line-height: 1;
  cursor: pointer; transition: all .16s cubic-bezier(.22,1,.36,1);
}
.exe-download-btn:hover {
  transform: translateY(-1px); border-color: var(--green);
  background: linear-gradient(135deg, rgba(52,211,153,.28), rgba(56,189,248,.24));
  box-shadow: inset 0 1px 0 rgba(255,255,255,.18), 0 6px 18px rgba(16,185,129,.22);
}
.exe-download-btn:active { transform: translateY(0) scale(0.985); }

.sb-footer-row {
  display: grid; grid-template-columns: 1.15fr 0.95fr 0.8fr; gap: 6px; width: 100%;
}
.sb-footer-row button, .sb-footer-row a {
  min-width: 0; width: 100%; height: 34px; padding: 0 6px; border-radius: 10px;
  border: 1px solid var(--line); background: rgba(255,255,255,.04);
  box-shadow: inset 0 1px 0 rgba(255,255,255,.06);
  color: #cbd5e1; font-family: var(--sans); font-size: 0.71rem; font-weight: 600;
  line-height: 1; text-decoration: none; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  display: inline-flex; align-items: center; justify-content: center; gap: 5px;
  cursor: pointer; transition: all .15s ease;
}
.sb-footer-row button:hover, .sb-footer-row a:hover {
  background: rgba(56,189,248,.12); border-color: rgba(56,189,248,.42);
  color: #fff; transform: translateY(-1px);
}
.sb-footer-row button:active, .sb-footer-row a:active {
  transform: translateY(0) scale(0.98);
}

/* UNIFIED MODERN BUTTONS & BADGES (.card-btn, .badge-chip) */
.card-btn {
  min-height: 34px; padding: 6px 12px; border-radius: 10px;
  border: 1px solid var(--line); background: rgba(255,255,255,.045);
  box-shadow: inset 0 1px 0 rgba(255,255,255,.06);
  color: #e2e8f0; font-family: var(--sans); font-size: 0.75rem; font-weight: 600;
  line-height: 1.1; text-decoration: none; white-space: nowrap;
  display: inline-flex; align-items: center; justify-content: center; gap: 6px;
  cursor: pointer; transition: all .15s ease;
}
.card-btn:hover {
  background: rgba(255,255,255,.09); border-color: rgba(56,189,248,.45);
  color: #fff; transform: translateY(-1px);
}
.card-btn:active { transform: translateY(0) scale(0.98); }
.card-btn.primary {
  background: linear-gradient(135deg, rgba(56,189,248,.24), rgba(167,139,250,.2));
  border-color: var(--cyan); color: #fff;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.14), 0 4px 14px rgba(56,189,248,.16);
}
.card-btn.primary:hover {
  background: linear-gradient(135deg, rgba(56,189,248,.32), rgba(167,139,250,.28));
}
.badge-chip {
  display: inline-flex; align-items: center; justify-content: center; gap: 5px;
  height: 24px; padding: 0 9px; border-radius: 999px;
  font-family: var(--mono); font-size: 0.67rem; font-weight: 700;
  background: rgba(56,189,248,.12); border: 1px solid rgba(56,189,248,.32); color: var(--cyan);
}

/* CENTER STAGE */
.center-stage {
  flex: 1; display: flex; flex-direction: column; min-width: 0; height: 100dvh; position: relative;
}
.studio-header {
  height: 56px; padding: 0 14px; border-bottom: 1px solid var(--line);
  background: var(--glass); backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px);
  display: flex; align-items: center; gap: 8px; flex-shrink: 0; z-index: 20;
}
.icon-pill-btn {
  height: 35px; padding: 0 12px; border-radius: 10px; border: 1px solid var(--line);
  background: rgba(255,255,255,.04); box-shadow: inset 0 1px 0 rgba(255,255,255,.06);
  color: var(--text); font-family: var(--sans); font-size: 0.76rem; font-weight: 600;
  line-height: 1; white-space: nowrap;
  display: inline-flex; align-items: center; justify-content: center; gap: 6px;
  cursor: pointer; transition: all .15s ease;
}
.icon-pill-btn:hover {
  border-color: rgba(56,189,248,.48); background: rgba(56,189,248,.1);
  color: #fff; transform: translateY(-1px);
}
.icon-pill-btn:active { transform: translateY(0) scale(0.98); }
.icon-pill-btn.active {
  border-color: rgba(52,211,153,.55); background: rgba(52,211,153,.15); color: var(--green);
}
.header-select {
  height: 35px; padding: 0 28px 0 11px; border-radius: 10px; border: 1px solid var(--line-strong);
  background-color: var(--card); color: #fff; font-family: var(--sans); font-size: 0.76rem; font-weight: 600;
  outline: none; cursor: pointer; -webkit-appearance: none; appearance: none;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%2338bdf8' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolyline points='6 9 12 15 18 9'/%3E%3C/svg%3E");
  background-repeat: no-repeat; background-position: right 9px center;
  transition: border-color .15s ease, background-color .15s ease;
}
.header-select:hover { border-color: var(--cyan); background-color: var(--card-hover); }
.header-select option { background: var(--panel); color: #fff; }
.header-spacer { flex: 1; }

/* MODULE PANES */
.mod-pane { flex: 1; display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
.mod-pane[hidden] { display: none !important; }
.hub-scroll {
  flex: 1; overflow-y: auto; padding: 20px 18px 90px;
  max-width: 1120px; width: 100%; margin: 0 auto;
  display: flex; flex-direction: column; gap: 18px;
}
.hub-hero {
  display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px;
  padding: 16px 18px; border-radius: 16px; border: 1px solid var(--line-strong);
  background: linear-gradient(135deg, rgba(20,31,54,.88), rgba(12,19,34,.92));
}
.hub-hero h2 { margin: 0 0 4px; font-size: 1.2rem; }
.hub-hero p { margin: 0; font-size: 0.8rem; color: var(--muted); }

/* CARDS GRID FOR HUBS (MEDIA, GALLERY, HA, ARTIFACTS) */
.cards-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 12px;
}
.feature-card {
  padding: 14px; border-radius: 14px; border: 1px solid var(--line);
  background: rgba(18, 28, 50, 0.78); display: flex; flex-direction: column; gap: 10px;
  transition: transform .16s ease, border-color .16s ease;
}
.feature-card:hover { transform: translateY(-2px); border-color: rgba(56,189,248,.4); }
.poster-card {
  display: flex; gap: 12px; padding: 12px; border-radius: 14px;
  border: 1px solid var(--line); background: rgba(18, 28, 50, 0.82);
}
.poster-card img {
  width: 78px; height: 116px; object-fit: cover; border-radius: 9px; flex-shrink: 0;
  background: #080d18; border: 1px solid var(--line);
}
.gallery-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(var(--gal-col, 200px), 1fr)); gap: 12px;
}
.gallery-item {
  border-radius: 14px; overflow: hidden; border: 1px solid var(--line);
  background: var(--card); display: flex; flex-direction: column;
  transition: transform .18s ease, border-color .18s ease, box-shadow .18s ease;
}
.gallery-item:hover {
  transform: translateY(-3px); border-color: var(--cyan);
  box-shadow: 0 10px 26px rgba(0,0,0,.45);
}
.gallery-item img {
  width: 100%; height: calc(var(--gal-col, 200px) * 0.86); object-fit: cover; cursor: zoom-in; background: #050811;
}
.gallery-item-foot {
  padding: 9px 11px; display: flex; align-items: center; justify-content: space-between;
  gap: 6px; font-size: 0.72rem; color: var(--muted);
}
.pagination-bar {
  display: flex; align-items: center; justify-content: center; flex-wrap: wrap;
  gap: 6px; padding: 10px 12px; border-radius: 14px;
  border: 1px solid var(--line); background: var(--panel);
}

/* CHAT STREAM & COMPOSER */
#paneChat {
  position: relative;
  --chat-column-width: 840px;
  --chat-side-padding: 16px;
}
/* Reserve identical scrollbar gutters so messages and composer share one axis. */
.chat-scroll, .composer-dock { scrollbar-gutter: stable both-edges; }
.chat-scroll {
  flex: 1; min-width: 0; overflow-y: auto; padding: 22px var(--chat-side-padding) 48px;
  display: flex; flex-direction: column; gap: 18px;
  scroll-behavior: auto; -webkit-overflow-scrolling: touch;
  /* O chat é uma zona de scroll própria: não deixa o gesto "vazar" para a
     página (era o que fazia a app inteira saltar em telemóvel/tablet). */
  overscroll-behavior: contain;
  /* Feather the clipping edge above the composer; keep the final line above it. */
  -webkit-mask-image: linear-gradient(to bottom, #000 calc(100% - 36px), transparent 100%);
  mask-image: linear-gradient(to bottom, #000 calc(100% - 36px), transparent 100%);
}
/* Botão flutuante «Ir para o fim» — aparece quando o Hubert faz scroll para cima */
.jump-end-btn {
  /* FAB discreto no canto inferior direito da área de mensagens (nunca por cima da caixa de texto).
     O `bottom` real é calculado por JS (placeJumpEndBtn) para ficar sempre ~20px acima do composer. */
  position: absolute; right: max(14px, calc((100% - 860px) / 2 + 4px)); bottom: 132px;
  transform: translateY(10px) scale(.92); transform-origin: bottom right;
  z-index: 30; display: inline-flex; align-items: center; gap: 6px;
  padding: 8px 13px 8px 12px; border-radius: 999px; cursor: pointer;
  font-family: inherit; font-size: .76rem; font-weight: 700; letter-spacing: .2px;
  color: #eaf6ff; border: 1px solid rgba(56,189,248,.42);
  background: linear-gradient(135deg, rgba(14,26,48,.94), rgba(10,19,36,.94));
  box-shadow: 0 8px 22px rgba(0,0,0,.45), 0 0 18px rgba(56,189,248,.14);
  backdrop-filter: blur(12px); opacity: 0; pointer-events: none;
  transition: opacity .18s ease, transform .18s ease, background .18s ease;
}
.jump-end-btn.show { opacity: 1; pointer-events: auto; transform: translateY(0) scale(1); }
.jump-end-btn:hover { background: linear-gradient(135deg, rgba(20,40,72,.98), rgba(12,24,44,.98)); border-color: var(--cyan); }
.jump-end-btn .jump-badge {
  display: inline-flex; align-items: center; justify-content: center;
  min-width: 18px; height: 18px; padding: 0 5px; border-radius: 999px;
  background: rgba(56,189,248,.2); color: var(--cyan); font-size: .68rem;
}
.welcome-studio { margin: auto; max-width: 720px; width: 100%; text-align: center; padding: 18px 12px; }
.welcome-logo { width: 74px; height: 74px; margin: 0 auto 12px; animation: glowPulse 4s infinite ease-in-out; }
.welcome-studio h1 { margin: 0 0 8px; font-size: 1.55rem; font-weight: 800; }
.welcome-studio p { margin: 0 auto 20px; max-width: 560px; color: var(--muted); font-size: 0.88rem; line-height: 1.5; }
.starter-grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(215px, 1fr)); gap: 10px; text-align: left;
}
.starter-card {
  padding: 13px 14px; border-radius: 14px; border: 1px solid var(--line);
  background: rgba(18, 28, 50, 0.7); cursor: pointer; transition: all .16s ease;
}
.starter-card:hover { transform: translateY(-2px); border-color: var(--cyan); background: var(--card-hover); }
.starter-card .sc-tag { font-family: var(--mono); font-size: 0.65rem; font-weight: 700; margin-bottom: 4px; }
.starter-card strong { display: block; font-size: 0.84rem; color: #fff; margin-bottom: 3px; }
.starter-card span { font-size: 0.73rem; color: var(--muted); line-height: 1.38; display: block; }

.turn { max-width: var(--chat-column-width); min-width: 0; flex-shrink: 0; width: 100%; margin: 0 auto; display: flex; flex-direction: column; gap: 5px; }
.turn.user { align-items: flex-end; }
.turn.assistant { align-items: flex-start; }
.bubble { padding: 13px 17px; border-radius: 18px; line-height: 1.6; font-size: 0.92rem; overflow-wrap: anywhere; overflow: hidden; box-sizing: border-box; max-width: 100%; }
.bubble img { max-width: 100%; height: auto; border-radius: 11px; display: block; }
.turn.user .bubble {
  max-width: 86%; background: linear-gradient(135deg, #0284c7, #4f46e5);
  color: #fff; border-bottom-right-radius: 5px; box-shadow: 0 8px 24px rgba(37, 99, 235, 0.22);
}
.turn.assistant .bubble {
  width: 100%; background: var(--card); border: 1px solid var(--line);
  color: var(--text); border-bottom-left-radius: 5px;
}
.orch-details {
  margin-bottom: 10px; border-radius: 10px; border: 1px solid rgba(56,189,248,.22);
  background: rgba(5, 9, 18, 0.5); overflow: hidden;
}
.orch-details summary {
  padding: 7px 11px; font-family: var(--mono); font-size: 0.71rem; color: #bae6fd;
  cursor: pointer; user-select: none; font-weight: 600;
}
.orch-body {
  padding: 8px 11px; border-top: 1px solid rgba(56,189,248,.14);
  font-family: var(--mono); font-size: 0.7rem; color: var(--muted); line-height: 1.48;
}
.code-card { margin: 10px 0; border-radius: 12px; border: 1px solid var(--line); background: #050810; overflow: hidden; }
.code-card-head {
  padding: 6px 11px; background: rgba(255,255,255,.04); border-bottom: 1px solid var(--line);
  display: flex; align-items: center; justify-content: space-between;
  font-family: var(--mono); font-size: 0.7rem; color: var(--muted);
}
.code-card-acts { display: flex; gap: 5px; }
.code-card-acts button {
  padding: 3px 8px; border-radius: 6px; border: 1px solid var(--line);
  background: rgba(255,255,255,.05); color: #e2e8f0; font-family: var(--mono); font-size: 0.67rem; cursor: pointer;
}
.code-card pre { margin: 0; padding: 12px; overflow-x: auto; font-family: var(--mono); font-size: 0.8rem; color: #e2e8f0; }
/* Autorização de comandos: botão largo, por baixo do bloco, com o mesmo
   tratamento dos botões principais da app. */
.code-card-foot {
  padding: 10px 12px; border-top: 1px solid var(--line);
  background:
    radial-gradient(90% 130% at 12% 0%, color-mix(in srgb, var(--cyan) 10%, transparent), transparent 62%),
    rgba(255,255,255,.015);
  display: flex; justify-content: flex-end;
}
.code-authorize {
  min-height: 38px; padding: 9px 18px; border-radius: 10px;
  border: 1px solid color-mix(in srgb, var(--cyan) 78%, #fff);
  background: linear-gradient(135deg, var(--cyan), #6366f1 62%, var(--violet));
  color: #fff; font-family: var(--sans); font-size: .8rem; font-weight: 700;
  letter-spacing: .01em; line-height: 1.1; text-shadow: 0 1px 2px rgba(5,9,18,.45);
  display: inline-flex; align-items: center; justify-content: center; gap: 7px;
  cursor: pointer;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.28), 0 8px 22px color-mix(in srgb, var(--cyan) 28%, transparent);
  transition: transform .15s ease, filter .15s ease, box-shadow .15s ease;
}
.code-authorize:hover {
  transform: translateY(-1px); filter: brightness(1.08);
  box-shadow: inset 0 1px 0 rgba(255,255,255,.32), 0 10px 26px color-mix(in srgb, var(--cyan) 38%, transparent);
}
.code-authorize:active { transform: translateY(0) scale(.98); }
.code-authorize:focus-visible { outline: 2px solid #bae6fd; outline-offset: 2px; }
.code-authorize:disabled { opacity: .55; cursor: wait; transform: none; filter: none; }
.code-authorize .code-authorize-ico { font-size: .95rem; line-height: 1; }
@media (max-width: 640px) {
  .code-card-foot { padding: 9px 10px; }
  .code-authorize { width: 100%; }
}
code.inline-code {
  font-family: var(--mono); font-size: 0.84em; padding: 2px 5px;
  border-radius: 5px; background: rgba(56,189,248,.12); color: #bae6fd;
}
.turn-imgs { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 8px; max-width: 100%; }
.turn-imgs img {
  max-width: 100%; width: auto; max-height: 220px; height: auto;
  object-fit: contain; border-radius: 11px; border: 1px solid var(--line-strong);
  cursor: zoom-in; background: rgba(5, 8, 17, 0.55); display: block;
}
.turn-foot { display: flex; align-items: center; gap: 10px; font-size: 0.69rem; color: var(--muted); padding: 0 4px; }
.turn-foot button { background: none; border: 0; color: var(--muted); font-size: 0.69rem; cursor: pointer; }

/* LIVE STREAMING ACTIVITY BUBBLE (CLAUDE CODE / CODEX STYLE) */
.live-work-bubble {
  border-color: rgba(56, 189, 248, 0.42) !important;
  background: linear-gradient(145deg, rgba(14, 23, 42, 0.96), rgba(10, 17, 32, 0.96)) !important;
  box-shadow: 0 10px 30px rgba(2, 132, 199, 0.14);
}
/* O balão ao vivo muda de altura a cada passo. Sem âncora de scroll própria,
   o browser tentava compensar por dentro dele e o chat tremia. */
.live-turn { overflow-anchor: none; }
.live-work-head {
  display: flex; align-items: center; justify-content: space-between; gap: 10px;
  padding: 8px 12px; border-radius: 11px;
  background: rgba(56, 189, 248, 0.09); border: 1px solid rgba(56, 189, 248, 0.25);
  margin-bottom: 8px;
}
.live-work-status {
  display: flex; align-items: center; gap: 9px; font-family: var(--mono);
  font-size: 0.76rem; font-weight: 700; color: #e0f2fe; min-width: 0;
}
.live-work-status .step-txt {
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.typing-dots {
  display: inline-flex; align-items: center; gap: 3px; flex-shrink: 0;
}
.typing-dots span {
  width: 6px; height: 6px; border-radius: 50%; background: var(--cyan);
  box-shadow: 0 0 8px var(--cyan);
  animation: dotBounce 1.25s infinite ease-in-out;
}
.typing-dots span:nth-child(2) { animation-delay: 0.18s; background: var(--violet); box-shadow: 0 0 8px var(--violet); }
.typing-dots span:nth-child(3) { animation-delay: 0.36s; background: var(--emerald); box-shadow: 0 0 8px var(--emerald); }
@keyframes dotBounce {
  0%, 80%, 100% { transform: scale(0.55); opacity: 0.4; }
  40% { transform: scale(1.2); opacity: 1; }
}
.live-work-timer {
  font-family: var(--mono); font-size: 0.7rem; color: var(--cyan);
  padding: 2px 8px; border-radius: 999px; background: rgba(56,189,248,0.13);
  border: 1px solid rgba(56,189,248,0.28); flex-shrink: 0;
  font-variant-numeric: tabular-nums; min-width: 62px; text-align: center;
}
.orch-decision {
  margin: 2px 0 8px; padding: 7px 10px; border-radius: 9px;
  background: rgba(56, 189, 248, 0.06); border: 1px dashed rgba(56, 189, 248, 0.28);
  font-family: var(--mono); font-size: 0.67rem; color: #cbd5e1;
  display: flex; flex-direction: column; gap: 3px;
}
.orch-decision .od-head { color: var(--cyan); font-weight: 700; letter-spacing: .04em; }
.orch-decision .od-route { color: #e0f2fe; }
.orch-decision .od-why { color: var(--muted); }
.orch-decision .od-why[hidden] { display: none; }

.btn-dots { display: inline-flex; align-items: center; gap: 3px; margin-left: 7px; vertical-align: middle; }
.btn-dots i {
  width: 4px; height: 4px; border-radius: 50%; background: currentColor;
  opacity: .35; animation: btnDot 1.15s infinite ease-in-out;
}
.btn-dots i:nth-child(2) { animation-delay: .16s; }
.btn-dots i:nth-child(3) { animation-delay: .32s; }
@keyframes btnDot { 0%, 80%, 100% { opacity: .28; transform: translateY(0); } 40% { opacity: 1; transform: translateY(-2px); } }

.step-sub {
  margin: 3px 0 7px; font-family: var(--mono); font-size: 0.66rem;
  color: #7f93ad; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  /* Altura fixa: a linha «↳ última saída» aparece e desaparece a cada poll;
     sem isto o balão crescia/encolhia ~24 px e o chat subia e descia. */
  line-height: 1.6; min-height: 1.6em;
}
.step-sub[hidden] { display: none; }
.live-steps-feed {
  display: flex; flex-direction: column; gap: 3px;
  padding: 7px 10px; border-radius: 9px; background: rgba(4, 8, 16, 0.6);
  border: 1px solid rgba(148, 163, 184, 0.12);
  font-family: var(--mono); font-size: 0.69rem; color: var(--muted);
  max-height: 132px; overflow-y: auto;
}
.live-steps-feed .step-row {
  display: flex; align-items: center; gap: 6px; line-height: 1.38;
}
.live-steps-feed .step-row.latest { color: #bae6fd; font-weight: 600; }

/* MESSAGE QUEUE STRIP & STEER CONTROLS */
.queue-strip {
  display: flex; flex-direction: column; gap: 5px;
  padding: 6px 9px; border-radius: 11px;
  background: rgba(56, 189, 248, 0.07); border: 1px dashed rgba(56, 189, 248, 0.34);
}
.queue-strip-head {
  display: flex; align-items: center; justify-content: space-between;
  font-family: var(--mono); font-size: 0.68rem; color: #7dd3fc; font-weight: 700;
}
.queue-item {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  padding: 5px 8px; border-radius: 8px; background: rgba(5, 10, 20, 0.72);
  border: 1px solid rgba(148, 163, 184, 0.18); font-size: 0.76rem; color: #e2e8f0;
}
.queue-item-txt { flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.queue-item-acts { display: flex; align-items: center; gap: 4px; flex-shrink: 0; }
.queue-item-acts button {
  padding: 2px 7px; border-radius: 6px; border: 1px solid var(--line);
  background: rgba(255,255,255,0.06); color: #bae6fd; font-family: var(--mono);
  font-size: 0.65rem; cursor: pointer;
}
.queue-item-acts button:hover { border-color: var(--cyan); color: #fff; }

/* MODERN FLOATING GLASS HOVER TOOLTIP */
.modern-tooltip {
  position: fixed; z-index: 9999; pointer-events: none;
  max-width: 280px; padding: 8px 12px; border-radius: 12px;
  background: rgba(8, 14, 28, 0.95);
  backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px);
  border: 1px solid rgba(56, 189, 248, 0.42);
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.65), 0 0 18px rgba(56, 189, 248, 0.15);
  color: #f1f5f9; font-family: var(--sans); font-size: 0.74rem; line-height: 1.4;
  opacity: 0; transform: translateY(4px) scale(0.97);
  transition: opacity 0.14s ease, transform 0.14s ease;
}
.modern-tooltip.show {
  opacity: 1; transform: translateY(0) scale(1);
}
.modern-tooltip .tt-title {
  display: block; font-weight: 800; color: var(--cyan); font-size: 0.76rem; margin-bottom: 2px;
}
.modern-tooltip .tt-desc {
  display: block; color: #cbd5e1; font-size: 0.71rem;
}

/* COMPOSER DOCK */
.composer-dock {
  padding: 6px var(--chat-side-padding) calc(12px + env(safe-area-inset-bottom));
  overflow-y: auto; flex-shrink: 0;
  background: linear-gradient(180deg, transparent, var(--bg) 35%); z-index: 20;
}
.composer-box {
  width: var(--composer-column-size, 100%); max-width: var(--chat-column-width);
  margin: 0; margin-left: var(--composer-column-offset, auto); margin-right: auto; border-radius: 18px;
  background: var(--panel); border: 1px solid rgba(56,189,248,.34);
  box-shadow: 0 12px 34px rgba(0,0,0,.45); padding: 9px 11px;
  display: flex; flex-direction: column; gap: 7px;
}
.attach-strip { display: flex; gap: 8px; flex-wrap: wrap; }
.attach-thumb {
  position: relative; width: 54px; height: 54px; border-radius: 9px; overflow: hidden; border: 1px solid var(--cyan);
}
.attach-thumb img { width: 100%; height: 100%; object-fit: cover; }
.attach-thumb button {
  position: absolute; top: 2px; right: 2px; width: 17px; height: 17px;
  border-radius: 50%; border: 0; background: rgba(0,0,0,.78); color: #fff; font-size: 0.62rem; cursor: pointer;
}
/* Texto grande colado => vira anexo/ficheiro, não invade o prompt */
.doc-chip {
  display: inline-flex; align-items: center; gap: 6px; max-width: 100%;
  padding: 5px 10px; border-radius: 9px; font-family: var(--mono); font-size: .72rem;
  color: #d8ecff; border: 1px solid rgba(56,189,248,.34);
  background: linear-gradient(135deg, rgba(14,26,48,.92), rgba(10,19,36,.92));
}
.doc-chip .doc-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 210px; }
.doc-chip button {
  border: 0; background: rgba(255,255,255,.1); color: #cfe8ff; cursor: pointer;
  border-radius: 6px; font-size: .66rem; padding: 1px 5px; font-family: inherit;
}
.doc-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
.composer-textarea {
  width: 100%; background: transparent; border: 0; color: #fff;
  font-family: var(--sans); font-size: 0.94rem; line-height: 1.45;
  resize: none; max-height: 160px; padding: 3px 4px; outline: none;
}
.composer-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 6px; flex-wrap: wrap; }
.tool-group { display: flex; align-items: center; gap: 5px; flex-wrap: wrap; }
.round-tool-btn {
  height: 33px; padding: 0 11px; border-radius: 999px; border: 1px solid rgba(255,255,255,.14);
  background: rgba(255,255,255,.07); box-shadow: inset 0 1px 0 rgba(255,255,255,.08);
  color: #e5edf7; font-family: var(--sans); font-size: 0.75rem; font-weight: 600;
  line-height: 1; text-decoration: none; white-space: nowrap;
  display: inline-flex; align-items: center; justify-content: center; gap: 6px;
  cursor: pointer; transition: all .15s ease;
}
.round-tool-btn:hover {
  background: rgba(255,255,255,.09); border-color: rgba(56,189,248,.42);
  color: #fff; transform: translateY(-1px);
}
.round-tool-btn:active { transform: translateY(0) scale(0.98); }
.round-tool-btn.magic {
  border-color: rgba(167,139,250,.48); background: rgba(167,139,250,.15); color: #e9d5ff;
}
.round-tool-btn.magic:hover {
  border-color: var(--violet); background: rgba(167,139,250,.24); color: #fff;
}
.round-tool-btn.magic.active {
  background: linear-gradient(135deg, rgba(167,139,250,.34), rgba(56,189,248,.26));
  border-color: var(--cyan); color: #fff;
}
.send-circle-btn {
  height: 35px; padding: 0 16px; border-radius: 999px; border: 1px solid rgba(255,255,255,.16);
  background: linear-gradient(135deg, var(--cyan), #6366f1); color: #fff;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.22), 0 6px 16px rgba(56,189,248,.24);
  font-family: var(--sans); font-weight: 700; font-size: 0.8rem; line-height: 1;
  white-space: nowrap; display: inline-flex; align-items: center; justify-content: center; gap: 6px;
  cursor: pointer; transition: all .15s ease;
}
.send-circle-btn:hover { transform: translateY(-1px); filter: brightness(1.08); }
.send-circle-btn:active { transform: translateY(0) scale(0.98); }
.send-circle-btn:disabled { opacity: 0.45; cursor: wait; transform: none; }

/* SPLIT ARTIFACT / CODE / PREVIEW CANVAS */
.artifact-canvas {
  width: 460px; background: #070b14; border-left: 1px solid var(--line);
  display: flex; flex-direction: column; flex-shrink: 0; z-index: 35;
}
.artifact-canvas[hidden] { display: none !important; }
.canvas-head {
  height: 56px; padding: 0 14px; border-bottom: 1px solid var(--line);
  display: flex; align-items: center; justify-content: space-between; gap: 8px; background: var(--panel);
}
.canvas-title {
  font-family: var(--mono); font-size: 0.77rem; font-weight: 700; color: var(--cyan);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.canvas-body {
  flex: 1; overflow: auto; padding: 14px; font-family: var(--mono);
  font-size: 0.78rem; line-height: 1.5; color: #e2e8f0; white-space: pre;
}
.canvas-iframe { width: 100%; height: 100%; border: 0; background: #fff; }

/* RIGHT STUDIO INSPECTOR */
.inspector-right {
  width: 360px; container-type: inline-size; background: var(--panel); border-left: 1px solid var(--line);
  display: flex; flex-direction: column; flex-shrink: 0; z-index: 45;
  transition: transform 0.28s cubic-bezier(.22,1,.36,1), width 0.22s ease;
}
.inspector-right.collapsed { width: 0; overflow: hidden; border-left: 0; }
.insp-tabs {
  height: 56px; padding: 0 8px; border-bottom: 1px solid var(--line);
  display: flex; align-items: center; gap: 4px;
}
.insp-tab {
  flex: 1; height: 35px; border-radius: 9px; border: 1px solid rgba(255,255,255,.08); background: rgba(255,255,255,.045);
  color: #cbd5e1; font-family: var(--sans); font-size: 0.74rem; font-weight: 700;
  line-height: 1; white-space: nowrap;
  display: inline-flex; align-items: center; justify-content: center; gap: 5px;
  cursor: pointer; transition: all .15s ease;
}
.insp-tab:hover { color: #fff; background: rgba(255,255,255,.09); border-color: rgba(56,189,248,.35); }
.insp-tab.active {
  background: linear-gradient(135deg, rgba(56,189,248,.26), rgba(99,102,241,.2));
  color: #fff; border-color: rgba(56,189,248,.65);
  box-shadow: inset 0 1px 0 rgba(255,255,255,.12), 0 4px 12px rgba(56,189,248,.16);
}
.insp-body { flex: 1; overflow-y: auto; padding: 12px; display: flex; flex-direction: column; gap: 10px; }
.insp-body > * { flex: 0 0 auto; }

.stat-card {
  padding: 12px 13px; border-radius: 13px; border: 1px solid var(--line);
  background: var(--card); display: flex; flex-direction: column; gap: 7px;
}
.stat-head { display: flex; align-items: center; justify-content: space-between; font-size: 0.79rem; font-weight: 700; }
.stat-sub { font-size: 0.7rem; color: var(--muted); line-height: 1.4; }
.meter-track { width: 100%; height: 7px; border-radius: 999px; background: rgba(255,255,255,.07); overflow: hidden; }
.meter-fill { height: 100%; border-radius: 999px; transition: width .35s ease; }
.meter-fill.ok { background: linear-gradient(90deg, #38bdf8, #34d399); }
.meter-fill.warn { background: linear-gradient(90deg, #fbbf24, #f97316); }
.meter-fill.bad { background: linear-gradient(90deg, #f87171, #ef4444); }

/* QUOTAS: estado esgotado, notas e botão de refresh */
.stat-card.quota-exhausted {
  border-color: rgba(248,113,113,.52);
  background: linear-gradient(180deg, rgba(248,113,113,.10), var(--card));
  box-shadow: 0 0 0 1px rgba(248,113,113,.08) inset;
}
.quota-chip {
  font-family: var(--mono); font-size: 0.63rem; font-weight: 800; letter-spacing: .04em;
  padding: 2px 7px; border-radius: 999px; border: 1px solid var(--line);
  background: rgba(255,255,255,.06); color: #e2e8f0;
}
.quota-chip.bad { color: #fecaca; border-color: rgba(248,113,113,.55); background: rgba(248,113,113,.18); }
.quota-chip.ok { color: #bbf7d0; border-color: rgba(52,211,153,.5); background: rgba(52,211,153,.14); }
.quota-chip.warn { color: #fde68a; border-color: rgba(251,191,36,.5); background: rgba(251,191,36,.14); }
.maestro-name { font-size: 0.9rem; font-weight: 800; color: var(--cyan); line-height: 1.25; }
.fleet-summary { font-size: 0.74rem; font-weight: 700; color: #cbd5e1; padding: 2px 2px 0; }
.fleet-card { gap: 5px; }
.fleet-quota { font-family: var(--mono); font-size: 0.66rem; color: #93c5fd; }
.fleet-task {
  font-size: 0.66rem; color: #fde68a; background: rgba(251,191,36,.08);
  border: 1px solid rgba(251,191,36,.22); border-radius: 7px; padding: 4px 7px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.rules-details { border: 1px solid var(--line); border-radius: 12px; background: var(--card); padding: 8px 10px; }
.rules-details summary {
  cursor: pointer; font-size: 0.74rem; font-weight: 700; color: var(--cyan);
  list-style: none; padding: 2px 0;
}
.rules-details summary::-webkit-details-marker { display: none; }
.rules-details summary::before { content: "▸ "; color: var(--cyan); }
.rules-details[open] summary::before { content: "▾ "; }
.rules-details .rich-table-wrap { margin-top: 8px; }
.quota-note {
  font-size: 0.68rem; line-height: 1.45; color: #fde68a;
  background: rgba(251,191,36,.09); border: 1px solid rgba(251,191,36,.28);
  border-radius: 8px; padding: 6px 8px;
}
.quota-src {
  font-family: var(--mono); font-size: 0.6rem; color: #94a3b8;
  text-transform: uppercase; letter-spacing: .05em;
}
.insp-refresh-btn {
  height: 34px; border-radius: 10px; border: 1px solid rgba(56,189,248,.4);
  background: rgba(56,189,248,.12); color: #e0f2fe; font-family: var(--sans);
  font-size: 0.74rem; font-weight: 700; cursor: pointer; transition: all .15s ease;
}
.insp-refresh-btn:hover { background: rgba(56,189,248,.22); border-color: var(--cyan); color: #fff; }
.insp-refresh-btn:disabled { opacity: .65; cursor: progress; }

/* QUOTAS EM FORMA DE BATERIA: uma só leitura em todo o painel.
   As barras enchem com o que RESTA (100% = cheio), não com o que foi gasto. */
.meter-track.battery {
  height: 9px; border-radius: 4px;
  background-color: rgba(255,255,255,.06);
  background-image: repeating-linear-gradient(90deg, transparent 0 15px, rgba(5,9,18,.62) 15px 17px);
  border: 1px solid rgba(255,255,255,.07);
}
.meter-track.battery .meter-fill { border-radius: 3px; }
.quota-batt {
  display: inline-flex; align-items: center; gap: 4px; white-space: nowrap;
  font-family: var(--mono); font-size: 0.65rem; font-weight: 700; color: #cbd5e1;
}
.quota-batt b { font-size: 0.8rem; font-weight: 800; }
.quota-batt .batt-ico { font-size: 0.72rem; opacity: .9; }
.quota-batt.ok b { color: var(--green); }
.quota-batt.warn b { color: var(--amber); }
.quota-batt.bad b { color: #fca5a5; }
.quota-legenda {
  display: flex; gap: 6px; align-items: flex-start;
  font-size: 0.67rem; line-height: 1.45; color: var(--muted); padding: 2px 2px 0;
}
.quota-legenda b { color: #e2e8f0; }
.quota-legenda .quota-updated { font-family: var(--mono); font-size: 0.61rem; opacity: .85; }
.quota-stale {
  border-color: rgba(251,191,36,.45) !important;
  background: linear-gradient(180deg, rgba(251,191,36,.08), var(--card)) !important;
}

.pulse-card {
  padding: 11px; border-radius: 11px; border: 1px solid var(--line);
  background: rgba(18, 28, 50, 0.75); display: flex; flex-direction: column; gap: 6px;
}
.pulse-card.err { border-left: 3px solid var(--rose); }
.pulse-card.warn { border-left: 3px solid var(--amber); }
.pulse-card.idea { border-left: 3px solid var(--violet); }
.pulse-card.ok { border-left: 3px solid var(--green); }
.pulse-card.info { border-left: 3px solid var(--cyan); }
.pulse-card-head { display: flex; align-items: center; gap: 7px; }
.pulse-card-head strong { flex: 1; }
.pulse-sel { display: inline-flex; align-items: center; gap: 5px; font-family: var(--mono); font-size: 0.6rem; color: var(--muted); cursor: pointer; }
.pulse-sel input { accent-color: var(--cyan); cursor: pointer; }
.pulse-cmd {
  border: 1px solid rgba(56,189,248,.3); border-radius: 12px; padding: 10px;
  background: linear-gradient(180deg, rgba(56,189,248,.09), rgba(5,9,18,.5));
  display: flex; flex-direction: column; gap: 7px;
}
.pulse-cmd-info { font-size: 0.72rem; color: #cbd5e1; }
.pulse-urgent-n { color: #fca5a5; font-weight: 700; }
.pulse-cmd-acts { display: flex; flex-wrap: wrap; gap: 5px; }
.pulse-act {
  height: 30px; padding: 0 9px; border-radius: 8px; border: 1px solid var(--line-strong);
  background: rgba(255,255,255,.06); color: #e2e8f0; font-family: var(--sans);
  font-size: 0.68rem; font-weight: 700; cursor: pointer; transition: all .15s ease;
}
.pulse-act:hover { border-color: var(--cyan); color: #fff; background: rgba(56,189,248,.14); }
.pulse-act.urgent { border-color: rgba(248,113,113,.45); color: #fecaca; background: rgba(248,113,113,.13); }
.pulse-act.urgent:disabled { opacity: .45; cursor: not-allowed; }
.pulse-act.analyze { border-color: rgba(167,139,250,.45); color: #e9d5ff; background: rgba(167,139,250,.12); }
.pulse-cmd-hint { font-size: 0.62rem; color: var(--muted); }
.file-row {
  padding: 7px 10px; border-radius: 8px; border: 1px solid var(--line);
  background: rgba(255,255,255,.025); display: flex; align-items: center;
  justify-content: space-between; gap: 8px; cursor: pointer; font-family: var(--mono); font-size: 0.71rem;
}
.file-row:hover { border-color: var(--cyan); }

/* MOBILE / TABLET BOTTOM NAVIGATION BAR */
.mobile-bottom-nav {
  display: none; position: fixed; left: 0; right: 0; bottom: 0;
  height: calc(58px + env(safe-area-inset-bottom));
  padding-bottom: env(safe-area-inset-bottom);
  background: rgba(10, 16, 30, 0.94); backdrop-filter: blur(16px);
  border-top: 1px solid var(--line); z-index: 32;
  grid-template-columns: repeat(5, 1fr);
}
.mob-nav-item {
  border: 0; background: transparent; color: var(--muted);
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 2px; font-size: 0.64rem; font-weight: 700; cursor: pointer;
}
.mob-nav-item span.ico { font-size: 1.15rem; }
.mob-nav-item.active { color: var(--cyan); }

/* MODALS (FOLDER CREATOR, LIGHTBOX, COMMAND PALETTE) */
.modal-backdrop {
  position: fixed; inset: 0; background: rgba(3, 6, 14, 0.76);
  backdrop-filter: blur(6px); z-index: 100;
  display: flex; align-items: center; justify-content: center; padding: 16px;
}
.modal-backdrop[hidden] { display: none !important; }
.modal-card {
  max-width: 520px; width: 100%; background: var(--panel);
  border: 1px solid var(--line-strong); border-radius: 16px;
  padding: 18px; display: flex; flex-direction: column; gap: 12px;
  box-shadow: 0 24px 60px rgba(0,0,0,.65);
}
.form-input {
  width: 100%; padding: 9px 12px; border-radius: 10px;
  border: 1px solid var(--line); background: rgba(0,0,0,.35);
  color: #fff; font-size: 0.85rem; outline: none;
}
.form-input:focus { border-color: var(--cyan); }

/* A gaveta fecha ao tocar fora. A opacidade acompanha o dedo durante o
   arrasto (ver o bloco DRAWERS no JavaScript), por isso a transição é só
   para o toque/toque-fora, não para o movimento em curso. */
.mobile-backdrop {
  display: block; position: fixed; inset: 0; background: rgba(0,0,0,.6); z-index: 40;
  opacity: 0; visibility: hidden; pointer-events: none;
  transition: opacity .22s ease, visibility 0s linear .22s;
}
.mobile-backdrop.show {
  opacity: 1; visibility: visible; pointer-events: auto;
  transition: opacity .22s ease;
}
.drawer-handle { display: none; }

/* UNICOLOR / DUOTONE SVG ICON SYSTEM */
.svg-ico {
  width: 17px; height: 17px; stroke: currentColor; stroke-width: 1.85;
  stroke-linecap: round; stroke-linejoin: round; fill: none;
  flex-shrink: 0; display: inline-block; vertical-align: -3px;
  transition: color 0.18s ease, filter 0.18s ease, transform 0.18s ease;
}
.nav-mod-btn .svg-ico, .mob-nav-item .svg-ico { width: 19px; height: 19px; vertical-align: middle; }
html[data-icon-style="unicolor"] .folder-pill span:first-child,
html[data-icon-style="unicolor"] .starter-card .st-title,
html[data-icon-style="unicolor"] .hub-hero h2,
html[data-icon-style="unicolor"] .insp-card-title {
  filter: saturate(0.12) brightness(1.15);
}
html[data-icon-style="unicolor"] .svg-ico {
  color: currentColor;
}
html[data-icon-style="duotone"] .svg-ico {
  color: var(--cyan);
  filter: drop-shadow(0 0 6px rgba(56,189,248,0.38));
}
html[data-icon-style="duotone"] .folder-pill span:first-child {
  filter: grayscale(1) sepia(1) hue-rotate(165deg) saturate(2.5);
}

/* LIVE AUTO-UPDATER TOAST BANNER
   Fica no fluxo da página, por baixo do header. Nunca tapa o menu, as
   mensagens ou a caixa de texto — em ecrãs estreitos o botão passa para
   a linha de baixo em vez de ser cortado. */
.update-toast {
  position: relative; z-index: 1;
  flex: 0 0 auto;
  width: min(920px, calc(100% - 24px));
  max-width: calc(100% - 24px);
  margin: 8px auto;
  padding: 9px 12px;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px 12px;
  border: 1px solid rgba(56, 189, 248, 0.55);
  border-radius: 14px;
  background: linear-gradient(135deg, rgba(13, 24, 44, 0.98), rgba(9, 16, 30, 0.98));
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35), 0 0 18px rgba(56, 189, 248, 0.14);
  font-size: 0.78rem; font-weight: 600; line-height: 1.35; color: #eaf6ff;
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
}
.update-toast[hidden] { display: none !important; }
.update-toast-icon {
  display: inline-flex; align-items: center; justify-content: center;
  width: 28px; height: 28px; border-radius: 9px; flex: 0 0 auto;
  background: rgba(56, 189, 248, 0.14); border: 1px solid rgba(56, 189, 248, 0.32);
  font-size: 0.95rem;
}
.update-toast-text {
  min-width: 0; overflow-wrap: anywhere; word-break: break-word;
}
.update-toast-actions {
  display: flex; align-items: center; justify-content: flex-end;
  gap: 6px; flex-wrap: nowrap;
}
.update-toast-actions .card-btn {
  white-space: nowrap; padding: 5px 11px; border-radius: 9px; font-size: 0.74rem;
}
.update-toast-dismiss {
  width: 28px; height: 28px; padding: 0; flex: 0 0 auto;
  border-radius: 8px; border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.05); color: var(--muted);
  font-size: 1rem; line-height: 1; cursor: pointer;
}
.update-toast-dismiss:hover { color: #fff; border-color: var(--line-strong); }

/* ---- ATUALIZAR A APP: botão permanente + puxar para atualizar ---- */
.refresh-btn {
  position: relative;
  width: 35px; padding: 0;
  font-size: 1rem;
}
.refresh-btn .refresh-ico {
  display: inline-block; font-size: 1.02rem; line-height: 1;
  transition: transform .2s ease;
}
.refresh-btn.busy { pointer-events: none; opacity: .72; }
.refresh-btn.busy .refresh-ico { animation: maestroSpin .9s linear infinite; }
.refresh-btn.has-update {
  border-color: rgba(52, 211, 153, .65);
  background: rgba(52, 211, 153, .14);
  color: #d1fae5;
  box-shadow: 0 0 0 2px rgba(52, 211, 153, .12), 0 0 14px rgba(52, 211, 153, .22);
}
.refresh-btn.has-update::after {
  content: ""; position: absolute; top: 5px; right: 5px;
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--green); box-shadow: 0 0 8px var(--green);
  animation: updateDotPulse 1.6s ease-in-out infinite;
}
@keyframes maestroSpin { to { transform: rotate(360deg); } }
@keyframes updateDotPulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: .55; transform: scale(.82); }
}

.sidebar-left .drawer-update {
  display: flex; align-items: center; gap: 9px;
  margin: 0 12px 10px; padding: 0 11px; min-height: 40px;
  border: 1px solid var(--line); border-radius: 10px;
  background: rgba(255, 255, 255, .035); color: var(--text);
  font: inherit; font-size: .76rem; font-weight: 600;
  text-align: left; cursor: pointer;
  transition: border-color .15s ease, background .15s ease, color .15s ease;
}
.sidebar-left .drawer-update:hover {
  border-color: rgba(56, 189, 248, .48); background: rgba(56, 189, 248, .09);
}
.sidebar-left .drawer-update .du-ico { font-size: 1rem; line-height: 1; flex: 0 0 auto; }
.sidebar-left .drawer-update .du-text {
  flex: 1 1 auto; min-width: 0; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis;
}
.sidebar-left .drawer-update .du-version {
  margin-left: auto; color: var(--muted);
  font-family: var(--mono); font-size: .62rem; font-weight: 500;
  white-space: nowrap; max-width: 47%;
  overflow: hidden; text-overflow: ellipsis;
}
.sidebar-left .drawer-update.busy { pointer-events: none; opacity: .7; }
.sidebar-left .drawer-update.busy .du-ico {
  display: inline-block; animation: maestroSpin .9s linear infinite;
}
.sidebar-left .drawer-update.has-update {
  border-color: rgba(52, 211, 153, .6);
  background: rgba(52, 211, 153, .12);
  color: #d1fae5;
}
.sidebar-left .drawer-update.has-update .du-version { color: var(--green); }

/* Indicador que acompanha o dedo no «puxar para atualizar». */
#pullRefresh {
  position: absolute; top: 62px; left: 50%; z-index: 25;
  transform: translate(-50%, -6px);
  display: inline-flex; align-items: center; gap: 7px;
  padding: 6px 12px; border-radius: 999px;
  border: 1px solid var(--line-strong);
  background: rgba(9, 16, 30, .94); color: var(--muted);
  font-size: .7rem; font-weight: 600; letter-spacing: .01em;
  box-shadow: 0 8px 20px rgba(0, 0, 0, .35);
  opacity: 0; pointer-events: none;
  transition: opacity .12s ease, color .12s ease, border-color .12s ease;
  backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);
}
#pullRefresh.visible { opacity: 1; }
#pullRefresh.ready { color: #d1fae5; border-color: rgba(52, 211, 153, .65); }
#pullRefresh .pr-ico {
  display: inline-block; font-size: .9rem; line-height: 1;
  transition: transform .15s ease;
}
#pullRefresh.ready .pr-ico { transform: rotate(180deg); }
#pullRefresh.loading .pr-ico { animation: maestroSpin .9s linear infinite; }

@media (max-width: 640px) {
  .update-toast {
    width: calc(100% - 20px); max-width: calc(100% - 20px);
    grid-template-columns: auto minmax(0, 1fr);
    margin: 6px auto;
  }
  .update-toast-actions {
    grid-column: 1 / -1;
    justify-content: stretch;
  }
  .update-toast-actions .card-btn.primary { flex: 1; }
}

/* ---- MOBILE / ANDROID: densidade, toque e nada a estourar para fora ---- */
@media (max-width: 640px) {
  html, body { overflow-x: hidden; }
  .insp-tab { font-size: 0.66rem; gap: 2px; padding: 0 2px; }
  .studio-header .header-select { min-width: 0; max-width: 42vw; text-overflow: ellipsis; }
  .studio-header .header-spacer { display: none; }
  .studio-header #btnToggleRight span { display: none; }
  .studio-header #btnRefreshApp { display: none; }
  .composer-toolbar, .composer-actions, .attach-strip { flex-wrap: nowrap; overflow-x: auto; -webkit-overflow-scrolling: touch; scrollbar-width: none; }
  .composer-toolbar::-webkit-scrollbar, .composer-actions::-webkit-scrollbar { display: none; }
  .composer-toolbar .round-tool-btn, .composer-toolbar button { flex: 0 0 auto; white-space: nowrap; padding: 7px 11px; font-size: 0.72rem; }
  .bubble { max-width: 92vw; }
  .composer-box, .composer { padding-bottom: calc(6px + env(safe-area-inset-bottom)); }
  .turn-foot { font-size: 0.62rem; }
  .chat-group-head { margin-top: 10px; }
}

@media (max-width: 1080px) {
  .sidebar-left, .inspector-right { z-index: 80; height: 100dvh; }
  /* Puxador discreto: só a barrinha, sem botão gigante. O arrasto é feito
     sobre a gaveta ou a partir da margem do ecrã. */
  .drawer-handle {
    display: flex; align-items: center; justify-content: center;
    flex: 0 0 auto; height: calc(18px + env(safe-area-inset-top));
    padding-top: env(safe-area-inset-top);
    touch-action: none; user-select: none; -webkit-user-select: none;
  }
  .drawer-handle span {
    width: 38px; height: 4px; border-radius: 999px;
    background: rgba(255,255,255,.22);
    box-shadow: 0 0 0 1px rgba(255,255,255,.04);
    transition: width .18s ease, background .18s ease;
  }
  .sidebar-left.dragging .drawer-handle span,
  .inspector-right.dragging .drawer-handle span {
    width: 48px; background: var(--cyan);
  }
  .sidebar-left.dragging, .inspector-right.dragging {
    transition: none !important; will-change: transform;
  }
  .insp-tabs { flex-shrink: 0; }
  .insp-body, .chat-list { min-height: 0; overscroll-behavior: contain; }
  .sidebar-left {
    position: fixed; inset: 0 auto 0 0; transform: translateX(-102%);
    width: min(310px, 92vw) !important; box-shadow: 20px 0 50px rgba(0,0,0,.78);
  }
  .sidebar-left.mobile-open { transform: translateX(0); }
  .inspector-right {
    position: fixed; inset: 0 0 0 auto; transform: translateX(102%);
    width: min(365px, 92vw) !important; box-shadow: -20px 0 50px rgba(0,0,0,.78);
  }
  .inspector-right.mobile-open { transform: translateX(0); }
  .artifact-canvas { position: fixed; inset: 0; width: 100vw !important; z-index: 70; }
  .mobile-bottom-nav { display: grid; }
  .composer-dock { padding-bottom: calc(66px + env(safe-area-inset-bottom)); }
  .kbd-hint { display: none; }
}

.composer-toolbar { flex-wrap: nowrap; overflow: visible; }
.composer-primary { display: flex; align-items: center; gap: 6px; min-width: 0; }
.composer-more { width: 36px; padding: 0; font-size: 1.5rem; letter-spacing: 1px; }
.composer-more[aria-expanded="true"] { color: var(--cyan); border-color: var(--cyan); }
.composer-menu {
  position: fixed; z-index: 1200; width: 296px; max-width: calc(100vw - 24px);
  overflow-y: auto; padding: 8px; border-radius: 16px; background: var(--panel);
  border: 1px solid rgba(148,163,184,.28); box-shadow: 0 18px 56px rgba(0,0,0,.5);
}
.composer-menu-heading { padding: 8px 10px 5px; color: var(--muted); font-size: .63rem; text-transform: uppercase; letter-spacing: .07em; }
.composer-menu .round-tool-btn { display: flex; width: 100%; justify-content: flex-start; height: 38px; border-radius: 9px; border-color: transparent; background: transparent; box-shadow: none; font-size: .8rem; }
.composer-menu .round-tool-btn:hover { background: rgba(148,163,184,.12); transform: none; }
.composer-menu .round-tool-btn:focus-visible, .composer-more:focus-visible { outline: 2px solid var(--cyan); outline-offset: 2px; }
.composer-menu [hidden] { display: none !important; }

/* ============================================================
   MAESTRO WORLDS · homepage aberta pelo logótipo
   Um palco animado por tema. A escolha aplica o mundo à app toda.
   ============================================================ */
.maestro-home {
  --mh-a: #38bdf8; --mh-b: #a78bfa; --mh-bg: #050912;
  position: fixed; inset: 0; z-index: 3000; overflow-y: auto; overscroll-behavior: contain;
  background: var(--mh-bg); color: var(--text); font-family: var(--sans);
  animation: mhFade .22s ease both;
}
.maestro-home[hidden] { display: none !important; }
@keyframes mhFade { from { opacity: 0; } to { opacity: 1; } }

.maestro-home[data-home-world="matrix"] { --mh-a: #00ff41; --mh-b: #10b981; --mh-bg: #020804; }
.maestro-home[data-home-world="scifi"] { --mh-a: #00e5ff; --mh-b: #38bdf8; --mh-bg: #020813; }
.maestro-home[data-home-world="cyberpunk"] { --mh-a: #ff0055; --mh-b: #fcee0a; --mh-bg: #090314; }
.maestro-home[data-home-world="hesperia"] { --mh-a: #d4af37; --mh-b: #a78bfa; --mh-bg: #08070d; }
.maestro-home[data-home-world="dune"] { --mh-a: #e07a2e; --mh-b: #38bdf8; --mh-bg: #0c0704; }
.maestro-home[data-home-world="dungen"] { --mh-a: #d6d0c4; --mh-b: #a6a6a6; --mh-bg: #141414; }

.mh-bg { position: fixed; inset: 0; pointer-events: none; overflow: hidden; }
.mh-bg::before {
  content: ""; position: absolute; inset: -20%;
  background:
    radial-gradient(circle at 22% 18%, var(--mh-a), transparent 46%),
    radial-gradient(circle at 78% 74%, var(--mh-b), transparent 44%);
  opacity: .16; filter: blur(46px);
  animation: mhDrift 16s ease-in-out infinite alternate;
}
.mh-bg::after {
  content: ""; position: absolute; inset: 0;
  background-image:
    linear-gradient(color-mix(in srgb, var(--mh-a) 9%, transparent) 1px, transparent 1px),
    linear-gradient(90deg, color-mix(in srgb, var(--mh-a) 9%, transparent) 1px, transparent 1px);
  background-size: 54px 54px, 54px 54px;
  -webkit-mask-image: radial-gradient(ellipse at 50% 34%, #000 8%, transparent 72%);
  mask-image: radial-gradient(ellipse at 50% 34%, #000 8%, transparent 72%);
  opacity: .55; animation: mhGridPan 22s linear infinite;
}
.mh-planet {
  display: none; position: absolute; right: 4%; top: 12%; width: min(340px, 34vw); aspect-ratio: 1;
  border-radius: 50%;
  background: radial-gradient(circle at 34% 30%, #f6c98a, #c2703a 40%, #7b3d1d 72%, #2a1207 100%);
  box-shadow: 0 0 120px color-mix(in srgb, var(--mh-a) 26%, transparent), inset -24px -20px 80px rgba(0,0,0,.7);
  opacity: .5; animation: duneSpin 120s linear infinite;
}
.maestro-home[data-home-world="dune"] .mh-planet { display: block; }
@keyframes mhDrift { from { transform: translate3d(-2%, -1%, 0) scale(1); } to { transform: translate3d(3%, 2%, 0) scale(1.08); } }
@keyframes mhGridPan { from { background-position: 0 0, 0 0; } to { background-position: 0 54px, 54px 0; } }

.mh-shell { position: relative; z-index: 2; width: min(1180px, 100%); min-height: 100dvh; margin: 0 auto; padding: 22px 26px 30px; display: flex; flex-direction: column; }
.mh-main { flex: 1 1 auto; }
.mh-top { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.mh-brand { display: flex; align-items: center; gap: 11px; }
.mh-brand img { width: 38px; height: 38px; animation: glowPulse 4s infinite ease-in-out; }
.mh-brand b { display: block; font-size: .95rem; letter-spacing: .04em; }
.mh-brand small { display: block; color: var(--muted); font-size: .64rem; letter-spacing: .1em; text-transform: uppercase; }
.mh-enter, .mh-primary, .mh-ghost {
  border: 1px solid color-mix(in srgb, var(--mh-a) 55%, transparent); border-radius: 999px;
  background: color-mix(in srgb, var(--mh-a) 16%, transparent); color: #fff;
  font-family: var(--sans); font-weight: 800; font-size: .78rem; cursor: pointer;
  padding: 10px 16px; transition: transform .16s ease, background .16s ease;
}
.mh-enter:hover, .mh-primary:hover, .mh-ghost:hover { transform: translateY(-1px); background: color-mix(in srgb, var(--mh-a) 28%, transparent); }
.mh-ghost { border-color: var(--line); background: rgba(255,255,255,.05); }
.mh-primary { background: linear-gradient(135deg, var(--mh-a), var(--mh-b)); border: 0; color: #071018; padding: 12px 20px; }
.maestro-home[data-home-world="cyberpunk"] .mh-primary,
.maestro-home[data-home-world="matrix"] .mh-primary { color: #001b08; }

.mh-main { display: grid; grid-template-columns: minmax(0, .92fr) minmax(0, 1.08fr); gap: 34px; align-items: center; padding: 46px 0 26px; }
.mh-kicker { margin: 0 0 10px; color: var(--mh-a); font-family: var(--mono); font-size: .68rem; letter-spacing: .18em; text-transform: uppercase; }
.mh-copy h1 { margin: 0; font-family: var(--display); font-size: clamp(2.3rem, 5vw, 4.3rem); line-height: .95; letter-spacing: -.02em; }
.mh-copy h1 em { font-style: normal; color: var(--mh-a); text-shadow: 0 0 30px color-mix(in srgb, var(--mh-a) 45%, transparent); }
.mh-lead { margin: 18px 0 0; max-width: 46ch; color: var(--muted); font-size: .92rem; line-height: 1.6; }
.mh-cta { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-top: 22px; }
.mh-hint { margin: 14px 0 0; color: var(--muted); font-size: .7rem; opacity: .8; }

.mh-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.mh-world {
  position: relative; text-align: left; padding: 8px; border-radius: 14px; cursor: pointer;
  border: 1px solid var(--line); background: rgba(8, 13, 24, .62); color: var(--text);
  font-family: var(--sans); transition: transform .16s ease, border-color .16s ease, background .16s ease;
  backdrop-filter: blur(8px); -webkit-backdrop-filter: blur(8px);
}
.mh-world:hover { transform: translateY(-2px); border-color: color-mix(in srgb, var(--mh-a) 55%, transparent); }
.mh-world.active { border-color: var(--mh-a); background: color-mix(in srgb, var(--mh-a) 9%, rgba(8,13,24,.72)); box-shadow: 0 0 0 1px color-mix(in srgb, var(--mh-a) 28%, transparent) inset; }
.mh-art { display: block; height: 96px; border-radius: 9px; position: relative; overflow: hidden; border: 1px solid rgba(255,255,255,.08); background: #050912; }
.mh-art::before, .mh-art::after { content: ""; position: absolute; inset: 0; }
.mh-meta { display: grid; gap: 2px; padding: 8px 4px 3px; }
.mh-name { font-size: .8rem; font-weight: 800; }
.mh-tag { color: var(--mh-a); font-family: var(--mono); font-size: .58rem; text-transform: uppercase; letter-spacing: .09em; }
.mh-desc { color: var(--muted); font-size: .66rem; line-height: 1.4; }
.mh-active-chip {
  display: none; position: absolute; top: 14px; right: 14px; padding: 2px 7px; border-radius: 999px;
  background: var(--mh-a); color: #04070d; font-family: var(--mono); font-size: .55rem; font-weight: 800; letter-spacing: .06em;
}
.mh-world.active .mh-active-chip { display: inline-flex; }

/* miniaturas animadas por mundo */
.mh-art-matrix { background: #020804; }
.mh-art-matrix::before { background: repeating-linear-gradient(90deg, transparent 0 9px, rgba(0,255,65,.22) 9px 10px); animation: mhRain 2.6s linear infinite; }
.mh-art-matrix::after { background: linear-gradient(180deg, rgba(0,255,65,.22), transparent 65%); }
@keyframes mhRain { from { background-position: 0 0; } to { background-position: 0 60px; } }
.mh-art-scifi { background: radial-gradient(circle at 50% 60%, rgba(0,229,255,.22), transparent 58%), #020813; }
.mh-art-scifi::before { background: repeating-linear-gradient(0deg, rgba(0,229,255,.14) 0 1px, transparent 1px 12px), repeating-linear-gradient(90deg, rgba(0,229,255,.14) 0 1px, transparent 1px 12px); }
.mh-art-scifi::after { inset: 22% 34%; border: 1px solid rgba(0,229,255,.55); border-radius: 50%; animation: mhRadar 3.2s linear infinite; }
@keyframes mhRadar { from { transform: rotate(0); box-shadow: 0 0 0 0 rgba(0,229,255,.25); } to { transform: rotate(360deg); box-shadow: 0 0 0 22px rgba(0,229,255,0); } }
.mh-art-cyberpunk { background: linear-gradient(180deg, #090314 30%, #2b0a3d 62%, #090314); }
.mh-art-cyberpunk::before { inset: 18% 26%; border-radius: 50%; background: linear-gradient(180deg, #fcee0a, #ff0055); filter: blur(1px); box-shadow: 0 0 28px rgba(255,0,85,.55); }
.mh-art-cyberpunk::after { top: 58%; background: repeating-linear-gradient(90deg, rgba(255,0,153,.55) 0 1px, transparent 1px 14px); transform: perspective(120px) rotateX(58deg); transform-origin: top center; }
.mh-art-hesperia { background: radial-gradient(circle at 50% 100%, rgba(212,175,55,.26), transparent 60%), #08070d; }
.mh-art-hesperia::before { background: repeating-linear-gradient(90deg, rgba(212,175,55,.5) 0 2px, transparent 2px 18px); -webkit-mask-image: linear-gradient(180deg, transparent, #000 35%, #000 80%, transparent); mask-image: linear-gradient(180deg, transparent, #000 35%, #000 80%, transparent); }
.mh-art-hesperia::after { inset: 20% 30%; border: 2px solid rgba(212,175,55,.55); border-bottom: 0; border-radius: 50% 50% 0 0; }
.mh-art-dune { background: radial-gradient(circle at 50% 100%, rgba(224,122,46,.35), transparent 62%), #0c0704; }
.mh-art-dune::before { inset: 24% 34%; border-radius: 50%; background: radial-gradient(circle at 34% 30%, #f6c98a, #c2703a 45%, #2a1207); animation: duneSpin 90s linear infinite; }
.mh-art-dune::after { background: repeating-linear-gradient(0deg, rgba(224,122,46,.16) 0 1px, transparent 1px 9px); }
.mh-art-dungen { background: linear-gradient(135deg, #1b1b1a, #141414); }
.mh-art-dungen::before { background: radial-gradient(circle at 50% 52%, transparent 28%, rgba(214,208,196,.55) 29%, transparent 30%), repeating-linear-gradient(45deg, rgba(214,208,196,.08) 0 1px, transparent 1px 7px); }
.mh-art-dungen::after { inset: auto -10% -20% -10%; height: 55%; background: radial-gradient(ellipse at 50% 0%, rgba(214,208,196,.22), transparent 65%); }

.mh-foot { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; border-top: 1px solid var(--line); padding-top: 14px; color: var(--muted); font-size: .68rem; }
.mh-foot b { color: var(--mh-a); }

@media (max-width: 900px) {
  .mh-main { grid-template-columns: 1fr; gap: 22px; padding-top: 30px; }
  .mh-copy h1 { font-size: clamp(2.4rem, 11vw, 3.6rem); }
  .mh-planet { opacity: .22; right: -8%; top: 4%; }
}
@media (max-width: 520px) {
  .mh-shell { padding: 14px 14px 22px; }
  .mh-enter { display: none; }
  .mh-main { padding-top: 22px; }
  .mh-grid { gap: 8px; }
  .mh-art { height: 74px; }
  .mh-desc { display: none; }
  .mh-lead { font-size: .84rem; }
  .mh-primary, .mh-ghost { flex: 1; justify-content: center; }
}

</style>
</head>
<body>
<canvas class="world-bg-canvas" aria-hidden="true"></canvas>
<div class="world-fx-overlay" aria-hidden="true"></div>

<!-- HOMEPAGE DO MAESTRO · aberta pelo logótipo -->
<section class="maestro-home" id="maestroHome" hidden role="dialog" aria-modal="true" aria-labelledby="mhTitle">
  <div class="mh-bg" aria-hidden="true"><span class="mh-planet"></span></div>
  <div class="mh-shell">
    <header class="mh-top">
      <div class="mh-brand">
        <img src="/icon.svg" alt="">
        <div><b>MAESTRO AI</b><small>Worlds · Studio 3.0</small></div>
      </div>
      <button type="button" class="mh-enter" id="btnHomeEnter">Entrar no Studio <span aria-hidden="true">→</span></button>
    </header>

    <main class="mh-main">
      <div class="mh-copy">
        <p class="mh-kicker">Pavilion-NAS · DungenLabs</p>
        <h1 id="mhTitle">Um estúdio.<br><em>Seis mundos.</em></h1>
        <p class="mh-lead">O Maestro orquestra os teus agentes, ficheiros e serviços. Cada mundo muda a luz, a tipografia e os efeitos da app inteira — escolhe um e entra.</p>
        <div class="mh-cta">
          <button type="button" class="mh-primary" id="btnHomeStart">Entrar no Studio</button>
          <button type="button" class="mh-ghost" id="btnHomeInstall">📲 Instalar app</button>
        </div>
        <p class="mh-hint">Clica num mundo para o experimentar. A escolha fica guardada neste dispositivo.</p>
      </div>
      <div class="mh-grid" id="mhWorlds" role="group" aria-label="Mundos visuais"></div>
    </main>

    <footer class="mh-foot">
      <span>✨ Fundos, tipografia e efeitos animados por tema</span>
      <span id="mhActiveWrap">Mundo ativo: <b id="mhActive">—</b></span>
    </footer>
  </div>
</section>

<div class="studio">
  <div class="mobile-backdrop" id="mobileBackdrop"></div>

  <!-- COLUNA 1: SIDEBAR ESQUERDA (MÓDULOS + PASTAS COLORIDAS + CONVERSAS) -->
  <aside class="sidebar-left" id="sidebarLeft" aria-label="Conversas e pastas">
    <div class="drawer-handle" aria-hidden="true"><span></span></div>
    <button type="button" class="brand-bar" id="btnBrandHome" data-tip="🏠 Homepage do Maestro|Abre os seis mundos visuais, com fundos e efeitos animados por tema." aria-label="Abrir a homepage do Maestro">
      <img src="/icon.svg" alt="">
      <span>
        <span class="brand-title">MAESTRO AI</span>
        <span class="brand-sub">Studio 3.0 · NAS, Media, 3D &amp; Dev</span>
      </span>
    </button>

    <button type="button" class="drawer-update" id="btnRefreshDrawer" aria-label="Verificar e instalar atualização da app">
      <span class="du-ico" aria-hidden="true">↻</span>
      <span class="du-text" data-i18n="refresh_app">Atualizar app</span>
      <span class="du-version" id="drawerVersion">v3.3</span>
    </button>

    <div class="nav-modules" id="sidebarModNav">
      <button class="nav-mod-btn active" data-mod="chat"><span class="ico">💬</span><span>Chat</span></button>
      <button class="nav-mod-btn" data-mod="studio"><span class="ico">🎨</span><span>Galeria</span></button>
      <button class="nav-mod-btn" data-mod="media"><span class="ico">🎬</span><span>Filmes</span></button>
      <button class="nav-mod-btn" data-mod="casa"><span class="ico">🏠</span><span>Casa/HA</span></button>
      <button class="nav-mod-btn" data-mod="artifacts"><span class="ico">🧩</span><span>Artifacts</span></button>
    </div>

    <button class="new-chat-cta" id="btnNewChat">
      <span>＋ Nova Conversa</span>
      <span class="kbd-hint">Ctrl+N</span>
    </button>

    <div class="sec-label-row">
      <span>PASTAS & PROJETOS</span>
      <button type="button" id="btnNewFolder">＋ Pasta</button>
    </div>
    <div class="colored-folders" id="folderStrip"></div>

    <div class="search-wrap">
      <input type="search" class="search-input" id="searchBox" placeholder="Procurar conversas ou pastas…">
    </div>

    <div class="chat-list" id="chatList"></div>

    <div class="sb-footer">
      <div class="sb-footer-row">
        <button type="button" id="btnOpenSettings" style="font-weight:700;color:var(--cyan);border-color:rgba(56,189,248,0.35)">⚙️ <span data-i18n="settings_btn">Opções</span></button>
        <button type="button" id="btnCleanup" title="Cleanup">🧹 <span data-i18n="clean_btn">Limpar</span></button>
        <a href="http://pavilion-nas:3000/#terminais" target="_blank">NAS ↗</a>
      </div>
    </div>
  </aside>

  <!-- COLUNA 2: PALCO CENTRAL (5 MÓDULOS ANIMADOS) -->
  <main class="center-stage">
    <header class="studio-header">
      <button class="icon-pill-btn" id="btnToggleLeft" title="Menu / Pastas">☰</button>
      <select class="header-select" id="selProject" title="Pasta / Projeto Ativo"></select>
      <select class="header-select" id="selEngine" title="Agente / Motor">
        <option value="auto">⚡ Auto (Maestro Orquestra)</option>
        <option value="agy">🧠 Antigravity (Gemini 4 Argon)</option>
        <option value="sonnet">🎭 Claude Sonnet (0.2x)</option>
        <option value="opus">👑 Claude Opus</option>
        <option value="haiku">Claude Haiku</option>
        <option value="fable">Claude Fable</option>
        <option value="codex">⚙️ Codex (gpt-6.1-sol)</option>
        <option value="deepseek">🔮 DeepSeek V3.2 (API)</option>
      </select>
      <div class="header-spacer"></div>
      <button class="icon-pill-btn" id="btnHeaderSettings" title="Opções, Idioma, Cores & Instaladores">⚙️</button>
      <button class="icon-pill-btn refresh-btn" id="btnRefreshApp" title="Atualizar app" aria-label="Atualizar app"><span class="refresh-ico" aria-hidden="true">↻</span></button>
      <button class="icon-pill-btn" id="btnNotif" title="Notificações Nativas">🔔</button>
      <button class="icon-pill-btn" id="btnToggleRight" title="Painel Studio">📊 <span data-i18n="panel_btn">Painel</span> <span id="headerPulseBadge"></span></button>
    </header>
    <div id="pullRefresh" role="status" aria-live="polite">
      <span class="pr-ico" aria-hidden="true">↓</span>
      <span class="pr-text" data-i18n="pull_refresh">Puxa para atualizar</span>
    </div>
<div class="update-toast anim-in" id="updateToast" role="status" aria-live="polite" hidden>
  <span class="update-toast-icon" aria-hidden="true">⚡</span>
  <span class="update-toast-text" id="updateToastText">Nova atualização do Maestro AI pronta!</span>
  <span class="update-toast-actions">
    <button type="button" class="card-btn primary" id="btnApplyUpdateNow">Atualizar agora</button>
    <button type="button" class="update-toast-dismiss" id="btnDismissUpdate" aria-label="Fechar aviso de atualização" title="Fechar aviso">✕</button>
  </span>
</div>

    <!-- MÓDULO 1: CHAT & ORQUESTRAÇÃO -->
    <div class="mod-pane" id="paneChat">
      <section class="chat-scroll" id="chatScroll"></section>
      <button type="button" class="jump-end-btn" id="btnJumpEnd" title="Ir para o fim da conversa">
        <span data-chat-label="0">⬇ Ir para o fim</span><span class="jump-badge" id="btnJumpEndBadge">↓</span>
      </button>
      <footer class="composer-dock">
        <form class="composer-box" id="composerForm">
          <div class="queue-strip" id="queueStrip" hidden></div>
          <div class="attach-strip" id="attachStrip"></div>
          <textarea class="composer-textarea" id="promptInput" rows="1" placeholder="Diz ao Maestro o que queres fazer (ex: 'mete o filme Dune a sacar', 'cria script C#', 'estado da Qidi')…"></textarea>
          <div class="composer-toolbar">
            <div class="tool-group">
              <button type="button" class="round-tool-btn composer-more" id="btnComposerMore" aria-label="Mais opções do pedido" aria-expanded="false" aria-controls="composerMenu" title="Mais opções">⋯</button>
              <div class="composer-menu" id="composerMenu" role="dialog" aria-label="Opções do pedido" hidden>
                <div class="composer-menu-heading" data-chat-label="1">Ferramentas do pedido</div>
              <input type="file" id="fileInput" accept="image/*" multiple hidden>
              <button type="button" class="round-tool-btn" id="btnAttach" data-tip="📷 Anexar Imagem|Adiciona imagens/screenshots ao pedido (também funciona com Ctrl+V direto).">📷 Imagem</button>
              <button type="button" class="round-tool-btn" id="btnVoice" data-tip="🎤 Ditado por Voz|Fala em português (pt-PT) para preencher o pedido mãos-livres.">🎤 Voz</button>
              <button type="button" class="round-tool-btn magic" id="btnEnhance" data-tip="✨ Melhorar Prompt|Enriquece o teu texto com contexto técnico da pasta/projeto atual.">✨ Melhorar</button>
                <div class="composer-menu-heading" data-chat-label="2">Preferências e ações</div>
              <button type="button" class="round-tool-btn" id="btnAutoEnhance" data-tip="Auto-✨ Ao Enviar|Melhora automaticamente pedidos curtos sempre que clicas em Enviar.">Auto-✨</button>
              <button type="button" class="round-tool-btn" id="btnBusyMode" data-tip="Modo durante Execução (Predefinido)|Alterna o comportamento predefinido quando envias mensagem enquanto o Maestro está ocupado: 📥 Meter em Fila vs 🧭 Orientar em Tempo Real.">📥 Modo: Fila</button>
              <button type="button" class="round-tool-btn" id="btnChatStyle" data-tip="Aspecto do chat|Auto deteta se a conversa é técnica (terminal/código) ou trivial e adapta a apresentação. Clica para forçar: Auto → ⌘ Código → 📖 Prosa.">⌘ Auto</button>
              <button type="button" class="round-tool-btn" id="btnAltBusy" hidden data-tip="Ação Alternativa Imediata|Executa a ação oposta ao modo predefinido (Orientar já ou Meter em fila).">🧭 Orientar</button>
              </div>
            </div>
            <div class="composer-primary">
              <button type="button" class="round-tool-btn" id="btnStopJob" hidden aria-label="Parar tarefa" title="Parar tarefa" style="border-color:rgba(248,113,113,.45);background:rgba(248,113,113,.12);color:#fecaca">■</button>
              <button type="submit" class="send-circle-btn" id="btnSend" data-tip="Enviar Pedido (Enter)|Envia ao Maestro (ou mete em fila / orienta se já estiver a trabalhar).">Enviar ↗</button>
            </div>
          </div>
        </form>
      </footer>
    </div>

    <!-- MÓDULO 2: GERADOR DE IMAGENS & GIFS + GALERIA VISUAL -->
    <div class="mod-pane" id="paneStudio" hidden>
      <div class="hub-scroll anim-in">
        <div class="hub-hero">
          <div>
            <h2>🎨 Studio de Imagens AI, GIFs Animados & Galeria</h2>
            <p>Gera arte HD (Flux), animações GIF89a procedurais, prints colados e logos do NAS com paginação e zoom.</p>
          </div>
          <span class="badge-chip" id="galTotalBadge">0 imagens</span>
        </div>
        <div class="feature-card">
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <input type="text" class="form-input" id="genMediaPrompt" placeholder="Descreve a imagem ou animação GIF que queres gerar (ex: 'Cyberpunk space station hud', 'Radar neon pulso')…" style="flex:1;min-width:220px">
            <select class="header-select" id="genAspect">
              <option value="1:1">Quadrado 1:1</option>
              <option value="16:9">Widescreen 16:9</option>
              <option value="9:16">Vertical 9:16 (Mobile)</option>
            </select>
            <button type="button" class="send-circle-btn" id="btnGenImage">🖼 Gerar Imagem AI</button>
            <button type="button" class="round-tool-btn magic" id="btnGenGif" style="height:35px">🎞 Gerar GIF Animado</button>
          </div>
        </div>

        <!-- BARRA DE FILTROS, PESQUISA, SLIDER DE ZOOM & ITENS POR PÁGINA -->
        <div class="feature-card" style="padding:11px 14px">
          <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px">
            <div style="display:flex;gap:6px;flex-wrap:wrap" id="galFilterTabs">
              <button type="button" class="card-btn primary" data-gkind="all">🌟 Todas</button>
              <button type="button" class="card-btn" data-gkind="ai">🎨 AI & GIFs</button>
              <button type="button" class="card-btn" data-gkind="paste">📋 Prints & Pastes</button>
              <button type="button" class="card-btn" data-gkind="nas_asset">🖼️ Logos & NAS</button>
            </div>
            <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;flex:1;justify-content:flex-end">
              <input type="search" class="form-input" id="galSearchInput" placeholder="Procurar na galeria…" style="max-width:210px;height:34px;padding:0 11px">
              <label style="display:inline-flex;align-items:center;gap:7px;font-size:0.74rem;color:var(--muted);white-space:nowrap">
                <span>🔎 Zoom</span>
                <input type="range" class="ui-slider" id="galZoomSlider" min="130" max="320" value="200" style="width:95px">
              </label>
              <select class="header-select" id="galPerPage" title="Imagens por página">
                <option value="12">12 / pág</option>
                <option value="24" selected>24 / pág</option>
                <option value="48">48 / pág</option>
                <option value="999">Todas</option>
              </select>
            </div>
          </div>
        </div>

        <div class="gallery-grid" id="galleryGrid"></div>
        <div class="pagination-bar" id="galleryPagination"></div>
      </div>
    </div>

    <!-- MÓDULO 3: MEDIA (SONARR / RADARR) & CONTAINERS DOCKER -->
    <div class="mod-pane" id="paneMedia" hidden>
      <div class="hub-scroll anim-in">
        <div class="hub-hero">
          <div>
            <h2>🎬 Cinema, Séries (Radarr & Sonarr) & Containers Docker</h2>
            <p>Pesquisa e coloca filmes ou séries a sacar com 1 clique, ou executa ações diretas nos containers do Pavilion-NAS.</p>
          </div>
        </div>

        <div class="feature-card">
          <div style="font-weight:700;font-size:0.88rem">🍿 Pedir Filme (Radarr :7878) ou Série (Sonarr :8989)</div>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <select class="header-select" id="selMediaKind">
              <option value="movie">🎬 Filme (Radarr)</option>
              <option value="series">📺 Série (Sonarr)</option>
            </select>
            <input type="search" class="form-input" id="mediaSearchInput" placeholder="Escreve o nome do filme ou série (ex: Severance, Dune, Andor)…" style="flex:1;min-width:200px">
            <button type="button" class="send-circle-btn" id="btnSearchMedia">🔍 Procurar</button>
          </div>
          <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap">
            <strong>⬇️ Estado dos downloads e pedidos recentes</strong>
            <button type="button" class="round-tool-btn" id="btnRefreshMediaQueue">🔄 Atualizar</button>
          </div>
          <div id="mediaQueueBox" aria-live="polite" style="font-size:0.82rem;color:var(--muted)">A consultar Radarr e Sonarr…</div>
          <div class="cards-grid" id="mediaResultsGrid"></div>
        </div>

        <div class="sec-label-row" style="padding:0"><span>🐳 CONTAINERS DOCKER & AÇÕES PREDEFINIDAS</span></div>
        <div class="cards-grid" id="containersGrid"></div>
      </div>
    </div>

    <!-- MÓDULO 4: CASA, IMPRESSÃO 3D (HOME ASSISTANT) & SCHEDULERS -->
    <div class="mod-pane" id="paneCasa" hidden>
      <div class="hub-scroll anim-in">
        <div class="hub-hero">
          <div>
            <h2>🏠 Casa, Impressão 3D (Home Assistant) & Schedulers</h2>
            <p>Controla as impressoras Centauri Carbon 2 e Qidi Plus 4, tomadas inteligentes e tarefas agendadas do Maestro.</p>
          </div>
          <button type="button" class="icon-pill-btn" id="btnRefreshHa">🔄 Atualizar HA</button>
        </div>

        <div class="sec-label-row" style="padding:0"><span>🖨️ IMPRESSORAS 3D EM TEMPO REAL</span></div>
        <div class="cards-grid" id="haPrintersGrid"></div>

        <div class="sec-label-row" style="padding:0"><span>🔌 TOMADAS INTELIGENTES & LUZES (1-TOQUE)</span></div>
        <div class="cards-grid" id="haSwitchesGrid"></div>

        <div class="sec-label-row" style="padding:0"><span>⏰ SCHEDULERS & AUTOMAÇÃO MAESTRO + HA</span></div>
        <div class="feature-card">
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <input type="text" class="form-input" id="schedName" placeholder="Nome da automação (ex: Relatório Noturno)" style="flex:1;min-width:150px">
            <input type="time" class="form-input" id="schedTime" value="09:00" style="width:120px">
            <select class="header-select" id="schedKind">
              <option value="maestro_prompt">🧠 Pedido ao Maestro AI</option>
              <option value="ha_toggle">🔌 Ligar/Desligar Entidade HA</option>
              <option value="docker_restart">🐳 Reiniciar Container Docker</option>
            </select>
          </div>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <input type="text" class="form-input" id="schedTarget" placeholder="Prompt Maestro, entity_id (ex: light.centauri_carbon_2_chamber_light) ou container…" style="flex:1">
            <button type="button" class="send-circle-btn" id="btnAddSchedule">＋ Agendar</button>
          </div>
          <div class="cards-grid" id="schedulesGrid"></div>
        </div>
      </div>
    </div>

    <!-- MÓDULO 5: ARTIFACTS & PROJETOS -->
    <div class="mod-pane" id="paneArtifacts" hidden>
      <div class="hub-scroll anim-in">
        <div class="hub-hero">
          <div>
            <h2>🧩 Biblioteca de Artifacts & Previews</h2>
            <p>Snippets de código, interfaces HTML/SVG e configurações geradas ou guardadas nas tuas pastas coloridas.</p>
          </div>
        </div>
        <div class="cards-grid" id="artifactsGrid"></div>
      </div>
    </div>
  </main>

  <!-- COLUNA 3: SPLIT ARTIFACT / CODE / LIVE PREVIEW CANVAS -->
  <section class="artifact-canvas" id="artifactCanvas" hidden>
    <div class="canvas-head">
      <span class="canvas-title" id="canvasTitle">Artifact</span>
      <div style="display:flex;gap:6px">
        <button type="button" class="icon-pill-btn" id="btnPreviewCanvas" hidden>👁 Preview</button>
        <button type="button" class="icon-pill-btn" id="btnCopyCanvas">Copiar</button>
        <button type="button" class="icon-pill-btn" id="btnCloseCanvas">✕</button>
      </div>
    </div>
    <div class="canvas-body" id="canvasBody"></div>
    <iframe class="canvas-iframe" id="canvasIframe" hidden sandbox="allow-scripts"></iframe>
  </section>

  <!-- COLUNA 4: STUDIO INSPECTOR DIREITO -->
  <aside class="inspector-right" id="inspectorRight" aria-label="Painel do Maestro">
    <div class="drawer-handle" aria-hidden="true"><span></span></div>
    <div class="insp-tabs" role="tablist" aria-label="Painel do Maestro">
      <button class="insp-tab active" id="itab-quotas" role="tab" aria-controls="inspBody" aria-selected="true" tabindex="0" data-itab="quotas">📊 Quotas</button>
      <button class="insp-tab" id="itab-pulso" role="tab" aria-controls="inspBody" aria-selected="false" tabindex="-1" data-itab="pulso">💓 Pulso</button>
      <button class="insp-tab" id="itab-git" role="tab" aria-controls="inspBody" aria-selected="false" tabindex="-1" data-itab="git">📁 Git/Files</button>
      <button class="insp-tab" id="itab-fleet" role="tab" aria-controls="inspBody" aria-selected="false" tabindex="-1" data-itab="fleet">🤖 Frota</button>
    </div>
    <div class="insp-body" id="inspBody" role="tabpanel" aria-labelledby="itab-quotas" tabindex="0"></div>
  </aside>

  <!-- BARRA INFERIOR MOBILE / TABLET -->
  <nav class="mobile-bottom-nav" id="mobileBottomNav">
    <button class="mob-nav-item active" data-mod="chat"><span class="ico">💬</span><span>Chat</span></button>
    <button class="mob-nav-item" data-mod="studio"><span class="ico">🎨</span><span>Galeria</span></button>
    <button class="mob-nav-item" data-mod="media"><span class="ico">🎬</span><span>Filmes</span></button>
    <button class="mob-nav-item" data-mod="casa"><span class="ico">🏠</span><span>Casa/HA</span></button>
    <button class="mob-nav-item" data-mod="artifacts"><span class="ico">🧩</span><span>Artifacts</span></button>
  </nav>
</div>

<!-- MODAL: CRIAR / EDITAR PASTA COLORIDA -->
<div class="modal-backdrop" id="agentModal" hidden>
  <div class="modal-card anim-in" style="max-width:560px">
    <div style="display:flex;justify-content:space-between;align-items:center">
      <strong id="agentModalTitle" style="font-size:1.02rem">🤖 Agente</strong>
      <button type="button" class="icon-pill-btn" id="btnCloseAgentModal">✕</button>
    </div>
    <div id="agentModalBody" style="display:flex;flex-direction:column;gap:8px;margin-top:10px"></div>
  </div>
</div>

<div class="modal-backdrop" id="folderModal" hidden>
  <div class="modal-card anim-in">
    <div style="display:flex;justify-content:space-between;align-items:center">
      <strong style="font-size:1rem" data-i18n="modal_folder_title">🎨 Nova Pasta / Projeto Colorido</strong>
      <button type="button" class="icon-pill-btn" id="btnCloseFolderModal">✕</button>
    </div>
    <input type="text" class="form-input" id="fName" placeholder="Nome da pasta (ex: Mods Starfield, Finanças, IA)">
    <div style="display:flex;gap:8px">
      <input type="text" class="form-input" id="fIcon" value="🚀" placeholder="Ícone Emoji" style="width:90px">
      <input type="color" class="form-input" id="fColor" value="#38bdf8" style="width:90px;padding:3px;height:40px">
      <input type="text" class="form-input" id="fCwd" value="/home/hubert" placeholder="Caminho no servidor (cwd)" style="flex:1">
    </div>
    <input type="text" class="form-input" id="fDesc" placeholder="Descrição / contexto para o Maestro AI">
    <button type="button" class="send-circle-btn" id="btnSaveFolder" style="justify-content:center;height:40px" data-i18n="save_folder_btn">Guardar Pasta Colorida</button>
  </div>
</div>

<!-- MODAL: OPÇÕES, IDIOMA (PT/EN), PERSONALIZADOR DE CORES & INSTALADORES -->
<div class="modal-backdrop" id="settingsModal" hidden>
  <div class="modal-card anim-in" style="max-width:540px;max-height:90dvh;overflow-y:auto">
    <div style="display:flex;justify-content:space-between;align-items:center">
      <strong style="font-size:1.05rem" data-i18n="settings_title">⚙️ Opções, Idioma, Cores & Instaladores</strong>
      <button type="button" class="icon-pill-btn" id="btnCloseSettingsModal">✕</button>
    </div>

    <!-- 1. IDIOMA / LANGUAGE -->
    <div class="feature-card" style="padding:12px">
      <div style="font-size:0.78rem;font-weight:700;color:var(--cyan)" data-i18n="sec_lang">🌐 IDIOMA DA INTERFACE / LANGUAGE</div>
      <div style="display:flex;gap:8px">
        <button type="button" class="icon-pill-btn" id="btnLangPt" style="flex:1;justify-content:center;padding:9px">🇵🇹 Português (PT)</button>
        <button type="button" class="icon-pill-btn" id="btnLangEn" style="flex:1;justify-content:center;padding:9px">🇬🇧 English (EN)</button>
      </div>
    </div>

    <!-- 2. ESTILO DE ÍCONES (UNICOLOR SVG / DUOTONE / COLORIDOS) -->
    <div class="feature-card" style="padding:12px">
      <div style="font-size:0.78rem;font-weight:700;color:var(--cyan)" data-i18n="sec_icons">✨ ESTILO DE ÍCONES (UNICOLOR / DUOTONE / COLORIDO)</div>
      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:6px" id="iconStyleGrid">
        <button type="button" class="card-btn" data-icostyle="unicolor" id="btnIcoUnicolor">✨ Unicolor SVG</button>
        <button type="button" class="card-btn" data-icostyle="duotone" id="btnIcoDuotone">💎 Duotone Neon</button>
        <button type="button" class="card-btn" data-icostyle="color" id="btnIcoColor">🎨 Coloridos</button>
      </div>
    </div>

    <!-- 2b. MUNDOS VISUAIS -->
    <div class="feature-card" style="padding:12px">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <span style="font-size:0.78rem;font-weight:700;color:var(--cyan)">🌍 MUNDOS VISUAIS (ESTILO GLOBAL)</span>
        <button type="button" class="card-btn" id="btnWorldReset" style="padding:3px 9px;font-size:0.7rem">↺ Padrão</button>
      </div>
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:6px" id="worldGrid">
        <button type="button" class="card-btn" data-world-pick="matrix">🟩 Matrix</button>
        <button type="button" class="card-btn" data-world-pick="scifi">🛰 Sci-Fi HUD</button>
        <button type="button" class="card-btn" data-world-pick="cyberpunk">🌆 Cyberpunk</button>
        <button type="button" class="card-btn" data-world-pick="hesperia">🏛 Hesperia</button>
        <button type="button" class="card-btn" data-world-pick="dune">🏜 Dune · Arrakis</button>
        <button type="button" class="card-btn" data-world-pick="dungen">🪨 Hubert Dungen</button>
      </div>
      <div style="font-size:0.68rem;color:var(--muted);margin-top:2px">Muda fontes, cores e texturas da app inteira. As cores finas continuam nos temas abaixo.</div>
    </div>

    <!-- 3. PERSONALIZADOR DE TEMAS & CORES -->
    <div class="feature-card" style="padding:12px">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <span style="font-size:0.78rem;font-weight:700;color:var(--cyan)" data-i18n="sec_theme">🎨 TEMAS & PERSONALIZADOR DE CORES</span>
        <button type="button" class="card-btn" id="btnResetTheme" style="padding:3px 9px;font-size:0.7rem" data-i18n="reset_theme">↺ Repor Padrão</button>
      </div>
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(145px,1fr));gap:6px" id="themePresetsGrid">
        <button type="button" class="card-btn" data-preset="cyber">🌌 Cyber Nebula</button>
        <button type="button" class="card-btn" data-preset="emerald">🌲 Emerald Matrix</button>
        <button type="button" class="card-btn" data-preset="solar">🔥 Solar Polistrat</button>
        <button type="button" class="card-btn" data-preset="opus">👑 Royal Opus</button>
        <button type="button" class="card-btn" data-preset="oled">🖤 Pure OLED Black</button>
        <button type="button" class="card-btn" data-preset="crimson">⚡ Crimson Synth</button>
      </div>
      <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin-top:4px">
        <label style="display:flex;align-items:center;justify-content:space-between;font-size:0.76rem;color:var(--muted);background:var(--bg);padding:6px 10px;border-radius:10px;border:1px solid var(--line)">
          <span data-i18n="col_primary">Cor Principal</span>
          <input type="color" id="colCyan" value="#38bdf8" style="border:none;background:none;width:32px;height:26px;cursor:pointer">
        </label>
        <label style="display:flex;align-items:center;justify-content:space-between;font-size:0.76rem;color:var(--muted);background:var(--bg);padding:6px 10px;border-radius:10px;border:1px solid var(--line)">
          <span data-i18n="col_secondary">Brilho / Glow</span>
          <input type="color" id="colViolet" value="#a855f7" style="border:none;background:none;width:32px;height:26px;cursor:pointer">
        </label>
        <label style="display:flex;align-items:center;justify-content:space-between;font-size:0.76rem;color:var(--muted);background:var(--bg);padding:6px 10px;border-radius:10px;border:1px solid var(--line)">
          <span data-i18n="col_bg">Fundo App</span>
          <input type="color" id="colBg" value="#050811" style="border:none;background:none;width:32px;height:26px;cursor:pointer">
        </label>
        <label style="display:flex;align-items:center;justify-content:space-between;font-size:0.76rem;color:var(--muted);background:var(--bg);padding:6px 10px;border-radius:10px;border:1px solid var(--line)">
          <span data-i18n="col_panel">Painéis / Cartões</span>
          <input type="color" id="colPanel" value="#0a0f1d" style="border:none;background:none;width:32px;height:26px;cursor:pointer">
        </label>
      </div>
      <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;margin-top:4px;flex-wrap:wrap">
        <span style="font-size:0.76rem;color:var(--muted)" data-i18n="ui_scale_lbl">Escala da Interface / Texto:</span>
        <div style="display:flex;align-items:center;gap:8px">
          <input type="range" class="ui-slider" id="uiScaleSlider" min="13" max="18" step="0.5" value="15.5" style="width:110px">
          <button type="button" class="card-btn" data-scale="14px">90%</button>
          <button type="button" class="card-btn" data-scale="15.5px">100%</button>
          <button type="button" class="card-btn" data-scale="17px">110%</button>
        </div>
      </div>
    </div>

    <!-- 4. AUTO-UPDATER AO VIVO & INSTALADORES (WINDOWS .EXE + ANDROID/TABLET PWA) -->
    <div class="feature-card" style="padding:12px">
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:6px">
        <span style="font-size:0.78rem;font-weight:700;color:var(--cyan)" data-i18n="sec_updater">🔄 AUTO-UPDATER & INSTALADORES DA APP</span>
        <span class="badge-chip" id="versionBadge">v3.2 · Live</span>
      </div>
      <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;background:var(--bg);padding:8px 10px;border-radius:10px;border:1px solid var(--line)">
        <div style="font-size:0.74rem;color:var(--muted)" data-i18n="updater_desc">
          Atualiza automaticamente a interface e o .exe sempre que o Maestro evolui no servidor.
        </div>
        <div style="display:flex;gap:6px;flex-shrink:0">
          <button type="button" class="card-btn primary" id="btnToggleAutoUpdate">🔄 Auto: ON</button>
          <button type="button" class="card-btn" id="btnCheckUpdateNow" data-i18n="check_upd_btn">Verificar</button>
        </div>
      </div>
      <p style="font-size:0.75rem;color:var(--muted);line-height:1.4;margin-top:2px" data-i18n="install_desc">
        Instala o Maestro AI Studio como aplicação nativa no teu Windows (ecrãs grandes) ou no telemóvel/tablet Android & iOS.
      </p>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <a class="exe-download-btn" href="/Maestro-AI.exe" download="Maestro-AI.exe" style="flex:1;min-width:210px">
          💻 <span data-i18n="dl_exe_btn">Instalar no Windows (.exe)</span>
        </a>
        <button type="button" class="card-btn primary" id="btnInstallPwa" style="flex:1;min-width:180px;padding:10px">
          📲 <span data-i18n="dl_pwa_btn">Instalar App Android / Tablet</span>
        </button>
      </div>
    </div>
  </div>
</div>

<script>
(function(){
  var state = {
    mod: "chat",
    folders: [],
    chats: [],
    currentChat: null,
    filterProj: "all",
    search: "",
    pendingImages: [],
    pendingDocs: [],
    status: null,
    pulse: null,
    projectInfo: null,
    inspTab: "quotas",
    autoEnhance: false,
    notifEnabled: (typeof Notification !== "undefined" && Notification.permission === "granted"),
    lastPulseErrCount: null,
    deferredPrompt: null,
    recognition: null,
    recording: false,
    canvasRawCode: "",
    canvasPreviewMode: false,
    activeJob: null,
    jobPollTimer: null,
    busyMode: localStorage.getItem("maestro_busy_mode") || "queue",
    messageQueue: [],
    foldersOpen: localStorage.getItem("maestro_folders_open") === "1",
    reloading: false,
    updateTimer: null,
    /* Quotas: guardar a última leitura boa e a hora. Se a API falhar, o painel
       NÃO pode mostrar 0% usado (parecia "tudo cheio") nem inventar valores. */
    statusAt: 0,
    statusError: false,
    statusLoading: false,
    pulseError: false,
    quotaRefreshing: false
  };

  function isStandaloneApp() {
    try {
      return (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches) ||
             window.navigator.standalone === true;
    } catch (e) { return false; }
  }

  function syncInstallButton() {
    var b = document.getElementById("btnInstallPwa");
    if (!b) return;
    var installed = isStandaloneApp();
    b.classList.toggle("installed", installed);
    b.disabled = false;
    if (installed) {
      b.innerHTML = "✅ " + (state.lang === "en" ? "App already installed" : "App já instalada");
    } else if (!window.isSecureContext) {
      b.textContent = state.lang === "en" ? "Open HTTPS to install" : "Abrir HTTPS para instalar";
    } else {
      b.innerHTML = "📲 " + (state.lang === "en" ? "Install app" : "Instalar app");
    }
  }

  function installPromptFailed(error) {
    syncInstallButton();
    console.warn("Maestro PWA install prompt failed", error);
    alert(state.lang === "en"
      ? "Chrome could not open the installation prompt. Reload this HTTPS page and try again. If it still fails, check Chrome's installation error."
      : "O Chrome não conseguiu abrir a instalação. Recarrega esta página HTTPS e tenta novamente. Se voltar a falhar, verifica a mensagem de erro do Chrome.");
  }

  window.addEventListener("beforeinstallprompt", function(e){
    e.preventDefault();
    state.deferredPrompt = e;
    syncInstallButton();
  });

  window.addEventListener("appinstalled", function(){
    state.deferredPrompt = null;
    syncInstallButton();
    notifyUser("📲 Maestro AI", state.lang === "en" ? "App installed successfully." : "App instalada com sucesso.");
  });

  if (window.matchMedia) {
    try {
      window.matchMedia("(display-mode: standalone)").addEventListener("change", syncInstallButton);
    } catch (e) {}
  }

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js", { scope: "/", updateViaCache: "none" }).catch(function(){});
  }

  function api(path, body) {
    var opts = body === undefined ? { cache: "no-store" } : {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Maestro-Action": "1" },
      body: JSON.stringify(body)
    };
    /* Um pedido pendurado deixava o painel em "a ler…" para sempre. */
    var ctrl = (typeof AbortController !== "undefined") ? new AbortController() : null;
    var timer = null;
    if (ctrl) {
      opts.signal = ctrl.signal;
      timer = setTimeout(function(){ try { ctrl.abort(); } catch (e) {} }, 20000);
    }
    return fetch(path, opts).then(function(r){
      if (!r.ok) throw new Error("HTTP " + r.status + " em " + path);
      return r.json();
    }).then(function(data){
      if (timer) clearTimeout(timer);
      return data;
    }, function(err){
      if (timer) clearTimeout(timer);
      throw err;
    });
  }

  function esc(s) {
    return String(s || "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
  }

  /* ---- MODULE NAVIGATION (CHAT / STUDIO / MEDIA / CASA / ARTIFACTS) ---- */
  function setModule(m) {
    state.mod = m;
    try { var _p = loadPrefs(); _p.mod = m; savePrefs(_p); } catch (e) {}
    document.querySelectorAll("[data-mod]").forEach(function(b){
      b.classList.toggle("active", b.getAttribute("data-mod") === m);
    });
    document.getElementById("paneChat").hidden = (m !== "chat");
    document.getElementById("paneStudio").hidden = (m !== "studio");
    document.getElementById("paneMedia").hidden = (m !== "media");
    document.getElementById("paneCasa").hidden = (m !== "casa");
    document.getElementById("paneArtifacts").hidden = (m !== "artifacts");
    closeMobileDrawers();

    if (m === "studio") loadGallery();
    if (m === "media") { loadMediaQueue(); loadContainers(); }
    if (m === "casa") { loadHaDashboard(); loadSchedules(); }
    if (m === "artifacts") loadArtifacts();
  }

  document.querySelectorAll("[data-mod]").forEach(function(b){
    b.onclick = function(){ setModule(b.getAttribute("data-mod")); };
  });

  /* ---- NOTIFICATIONS + SOUND + VIBRATION ---- */
  function playChime() {
    try {
      var ctx = new (window.AudioContext || window.webkitAudioContext)();
      var o = ctx.createOscillator(), g = ctx.createGain();
      o.type = "sine";
      o.frequency.setValueAtTime(587.33, ctx.currentTime);
      o.frequency.setValueAtTime(880, ctx.currentTime + 0.11);
      g.gain.setValueAtTime(0.07, ctx.currentTime);
      g.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.34);
      o.connect(g); g.connect(ctx.destination);
      o.start(); o.stop(ctx.currentTime + 0.34);
    } catch (e) {}
    try { if (navigator.vibrate) navigator.vibrate([90, 45, 110]); } catch (e) {}
  }

  function syncNotifButton() {
    var b = document.getElementById("btnNotif");
    var ok = (typeof Notification !== "undefined" && Notification.permission === "granted" && state.notifEnabled);
    b.classList.toggle("active", ok);
    b.textContent = ok ? "🔔 ON" : "🔕";
  }

  function notifyUser(title, bodyText) {
    playChime();
    if (!state.notifEnabled || typeof Notification === "undefined" || Notification.permission !== "granted") return;
    var cleanBody = String(bodyText || "").replace(/[#*`]/g, "").slice(0, 160);
    if (navigator.serviceWorker && navigator.serviceWorker.ready) {
      navigator.serviceWorker.ready.then(function(reg){
        reg.showNotification(title, {
          body: cleanBody,
          icon: "/icon.svg",
          badge: "/icon.svg",
          vibrate: [110, 50, 110],
          tag: "maestro-" + Date.now()
        });
      }).catch(function(){
        try { new Notification(title, { body: cleanBody, icon: "/icon.svg" }); } catch(e){}
      });
    }
  }

  document.getElementById("btnNotif").onclick = function(){
    if (typeof Notification === "undefined") return;
    if (Notification.permission === "granted") {
      state.notifEnabled = !state.notifEnabled;
      syncNotifButton();
      if (state.notifEnabled) notifyUser("👑 Maestro AI Studio", "Notificações ativas!");
      return;
    }
    Notification.requestPermission().then(function(p){
      state.notifEnabled = (p === "granted");
      syncNotifButton();
      if (state.notifEnabled) notifyUser("👑 Maestro AI Studio", "Notificações ativadas!");
    });
  };
  syncNotifButton();

  /* ---- DRAWERS & MOBILE SWIPE GESTURES ----
     Sem botão gigante: a gaveta segue o dedo. Puxar da margem esquerda /
     direita abre; arrastar a gaveta para a sua margem fecha. Tocar fora
     (backdrop) e Escape continuam a fechar, e o foco volta ao botão que abriu. */
  var leftEl = document.getElementById("sidebarLeft");
  var rightEl = document.getElementById("inspectorRight");
  var backdropEl = document.getElementById("mobileBackdrop");

  var drawerTrigger = null;
  var drawerDrag = null;
  var suppressClickUntil = 0;
  var DRAG_AXIS_PX = 8;
  var EDGE_OPEN_PX = 24;
  var CLOSE_RATIO = 0.32;
  var OPEN_RATIO = 0.24;
  var FLING_VELOCITY = 0.38;
  var FLING_MIN_PX = 28;

  function isMobileLayout() { return window.innerWidth <= 1080; }
  function drawerIsOpen(el) { return el.classList.contains("mobile-open"); }
  function drawerWidth(el) { return el.getBoundingClientRect().width || (el === leftEl ? 310 : 365); }

  function clearDrawerInline(el) {
    el.classList.remove("dragging");
    el.style.transition = "";
    el.style.transform = "";
  }

  function syncMobileDrawers() {
    var mobile = isMobileLayout();
    [leftEl, rightEl].forEach(function(el, i){
      var open = mobile && drawerIsOpen(el);
      el.inert = mobile && !open;
      if (mobile) el.setAttribute("aria-hidden", String(!open));
      else el.removeAttribute("aria-hidden");
      var btn = document.getElementById(i ? "btnToggleRight" : "btnToggleLeft");
      btn.setAttribute("aria-controls", el.id);
      btn.setAttribute("aria-expanded", String(mobile ? open : !el.classList.contains("collapsed")));
    });
    var anyOpen = mobile && (drawerIsOpen(leftEl) || drawerIsOpen(rightEl));
    backdropEl.classList.toggle("show", anyOpen);
    if (!anyOpen && !drawerDrag) {
      backdropEl.style.opacity = "";
      backdropEl.style.pointerEvents = "";
    }
  }

  function focusDrawer(el) {
    if (!el.hasAttribute("tabindex")) el.setAttribute("tabindex", "-1");
    el.focus({preventScroll:true});
  }

  function closeMobileDrawers(immediate, skipEl) {
    var hadOpen = drawerIsOpen(leftEl) || drawerIsOpen(rightEl);
    [leftEl, rightEl].forEach(function(el){
      if (el === skipEl) return;
      el.classList.remove("mobile-open");
      clearDrawerInline(el);
    });
    backdropEl.style.opacity = "";
    backdropEl.style.pointerEvents = "";
    // Tirar o foco de dentro da gaveta antes de a marcar aria-hidden/inert.
    if (!immediate && hadOpen && drawerTrigger) drawerTrigger.focus({preventScroll:true});
    syncMobileDrawers();
    if (!immediate) drawerTrigger = null;
  }

  function openMobileDrawer(el, fromDrag) {
    closeMobileDrawers(true, el);
    drawerTrigger = document.getElementById(el === leftEl ? "btnToggleLeft" : "btnToggleRight");
    el.classList.add("mobile-open");
    syncMobileDrawers();
    backdropEl.style.opacity = "";
    // Se veio de um arrasto, parte da posição actual do dedo e deixa a
    // transição CSS levar a gaveta até 0. O reflow força o ponto de partida.
    void el.offsetWidth;
    clearDrawerInline(el);
    focusDrawer(el);
    if (el === rightEl) renderInspector();
  }

  function settleDrawer(d, open) {
    var el = d.el;
    if (open) {
      if (!drawerIsOpen(el)) {
        openMobileDrawer(el, true);
      } else {
        void el.offsetWidth;
        clearDrawerInline(el);
        backdropEl.style.opacity = "";
        syncMobileDrawers();
      }
    } else if (drawerIsOpen(el)) {
      closeMobileDrawers();
    } else {
      void el.offsetWidth;
      clearDrawerInline(el);
      backdropEl.style.opacity = "";
      syncMobileDrawers();
    }
  }

  function abortDrawerDrag() {
    document.removeEventListener("touchmove", onDrawerTouchMove);
    var d = drawerDrag;
    drawerDrag = null;
    backdropEl.style.pointerEvents = "";
    if (!d) return;
    d.el.classList.remove("dragging");
    d.el.style.transition = "";
    d.el.style.transform = "";
    backdropEl.style.opacity = "";
    syncMobileDrawers();
  }

  function onDrawerTouchStart(e) {
    if (!isMobileLayout() || drawerDrag || !e.touches || e.touches.length !== 1) return;
    if (document.querySelector('.modal-backdrop:not([hidden])')) return;
    if (document.querySelector('.artifact-canvas:not([hidden])')) return;
    // Não roubar o gesto ao compositor, à barra inferior ou a campos de texto.
    if (e.target.closest && e.target.closest('input, textarea, select, [contenteditable="true"], .composer-dock, .mobile-bottom-nav')) return;
    var t = e.touches[0];
    var openEl = drawerIsOpen(leftEl) ? leftEl : (drawerIsOpen(rightEl) ? rightEl : null);
    var el = null, fromOpen = false;
    if (openEl) {
      if (!openEl.contains(e.target)) return; // toque no backdrop: o click fecha
      if (e.target.closest && e.target.closest('input, textarea, select, [contenteditable="true"]')) return;
      el = openEl; fromOpen = true;
    } else {
      if (t.clientX <= EDGE_OPEN_PX) el = leftEl;
      else if (t.clientX >= window.innerWidth - EDGE_OPEN_PX) el = rightEl;
      else return;
    }
    var width = drawerWidth(el);
    drawerDrag = {
      el: el, fromOpen: fromOpen, width: width,
      startX: t.clientX, startY: t.clientY,
      offset: fromOpen ? 0 : (el === leftEl ? -width : width),
      axis: null, moved: false,
      lastX: t.clientX, lastTime: Date.now(), velocity: 0
    };
    document.addEventListener("touchmove", onDrawerTouchMove, { passive: false });
  }

  function onDrawerTouchMove(e) {
    if (!drawerDrag) return;
    var d = drawerDrag;
    var t = e.touches && e.touches[0];
    if (!t) return;
    var dx = t.clientX - d.startX;
    var dy = t.clientY - d.startY;
    if (!d.axis) {
      if (Math.abs(dx) < DRAG_AXIS_PX && Math.abs(dy) < DRAG_AXIS_PX) return;
      if (Math.abs(dy) > Math.abs(dx) * 1.15) { abortDrawerDrag(); return; }
      d.axis = "x";
      d.moved = true;
      d.el.classList.add("dragging");
      backdropEl.classList.add("show");
      backdropEl.style.pointerEvents = "none";
    }
    if (d.axis !== "x") return;
    if (e.cancelable) e.preventDefault();
    var now = Date.now();
    var dt = now - d.lastTime;
    if (dt >= 12) {
      d.velocity = (t.clientX - d.lastX) / dt;
      d.lastX = t.clientX;
      d.lastTime = now;
    }
    var min = d.el === leftEl ? -d.width : 0;
    var max = d.el === leftEl ? 0 : d.width;
    var off = Math.max(min, Math.min(max, d.offset + dx));
    d.offset = off;
    d.el.style.transform = "translate3d(" + off + "px,0,0)";
    var progress = 1 - Math.abs(off) / d.width;
    backdropEl.style.opacity = String(Math.max(0, Math.min(1, progress)));
  }

  function onDrawerTouchEnd(e) {
    document.removeEventListener("touchmove", onDrawerTouchMove);
    var d = drawerDrag;
    drawerDrag = null;
    backdropEl.style.pointerEvents = "";
    if (!d) return;
    if (d.axis !== "x") {
      // Foi um toque ou um scroll vertical: a gaveta fica como estava.
      d.el.classList.remove("dragging");
      d.el.style.transition = "";
      d.el.style.transform = "";
      backdropEl.style.opacity = "";
      syncMobileDrawers();
      return;
    }
    var t = e.changedTouches && e.changedTouches[0];
    var dx = t ? t.clientX - d.startX : 0;
    var v = d.velocity || 0;
    var open;
    if (d.fromOpen) {
      var towardEdge = d.el === leftEl ? -1 : 1;
      var fling = Math.abs(dx) > Math.max(FLING_MIN_PX, d.width * 0.12);
      open = !(Math.abs(d.offset) > d.width * CLOSE_RATIO || (v * towardEdge > FLING_VELOCITY && fling));
    } else {
      var inward = d.el === leftEl ? 1 : -1;
      var flingIn = Math.abs(dx) > Math.max(FLING_MIN_PX, d.width * 0.12);
      open = Math.abs(dx) > d.width * OPEN_RATIO || (v * inward > FLING_VELOCITY && flingIn);
    }
    if (d.moved) suppressClickUntil = Date.now() + 350;
    settleDrawer(d, open);
  }

  function onDrawerTouchCancel() {
    document.removeEventListener("touchmove", onDrawerTouchMove);
    var d = drawerDrag;
    drawerDrag = null;
    backdropEl.style.pointerEvents = "";
    if (!d) return;
    if (d.moved) suppressClickUntil = Date.now() + 350;
    settleDrawer(d, d.fromOpen);
  }

  /* Um arrasto não deve transformar-se num click no que estava por baixo. */
  document.addEventListener("click", function(e){
    if (Date.now() < suppressClickUntil) {
      suppressClickUntil = 0;
      e.preventDefault();
      e.stopPropagation();
    }
  }, true);

  backdropEl.onclick = function(){ closeMobileDrawers(); };
  [leftEl, rightEl].forEach(function(el, i){
    document.getElementById(i ? "btnToggleRight" : "btnToggleLeft").onclick = function(){
      if (window.innerWidth <= 1080) {
        if (drawerIsOpen(el)) closeMobileDrawers();
        else openMobileDrawer(el);
      } else {
        el.classList.toggle("collapsed");
        syncMobileDrawers();
      }
    };
  });
  window.addEventListener("resize", function(){
    if (window.innerWidth > 1080) {
      drawerDrag = null;
      closeMobileDrawers(true);
    }
    syncMobileDrawers();
  });
  document.addEventListener("keydown", function(e){
    var drawer = window.innerWidth <= 1080 && document.querySelector(".mobile-open");
    if (!drawer || document.querySelector('.modal-backdrop:not([hidden])')) return;
    if (e.key === "Escape") { e.preventDefault(); closeMobileDrawers(); }
    if (e.key === "Tab") {
      var items = Array.from(drawer.querySelectorAll('button, a[href], input, select, [tabindex="0"]')).filter(function(el){ return !el.disabled && el.getClientRects().length; });
      var first = items[0], last = items[items.length - 1];
      if (e.shiftKey && (document.activeElement === first || document.activeElement === drawer || !drawer.contains(document.activeElement))) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && (document.activeElement === last || !drawer.contains(document.activeElement))) { e.preventDefault(); first.focus(); }
    }
  });
  document.addEventListener("touchstart", onDrawerTouchStart, { passive: true });
  document.addEventListener("touchend", onDrawerTouchEnd, { passive: true });
  document.addEventListener("touchcancel", onDrawerTouchCancel, { passive: true });
  syncMobileDrawers();

  /* ---- COLORED FOLDERS ---- */
  function loadFolders() {
    return api("/api/folders").then(function(d){
      state.folders = d.folders || [];
      renderFolderStripAndSelect();
    });
  }

  function renderFolderStripAndSelect() {
    var strip = document.getElementById("folderStrip");
    var sel = document.getElementById("selProject");
    var curVal = sel.value || "nas";

    var fmap = {};
    state.folders.forEach(function(f){ fmap[f.id] = f; });
    var activeF = fmap[state.filterProj];
    var activeName = state.filterProj === "all" ? "Todas as pastas" : (activeF ? activeF.name : "Todas as pastas");
    var activeIcon = state.filterProj === "all" ? "🗂️" : (activeF ? activeF.icon : "🗂️");
    var activeColor = activeF ? activeF.color : "#38bdf8";
    var activeCount = state.chats.filter(function(c){
      return state.filterProj === "all" || c.project === state.filterProj;
    }).length;

    function cnt(fid) {
      return state.chats.filter(function(c){ return fid === "all" || c.project === fid; }).length;
    }

    var html =
      '<button type="button" class="folder-acc-head' + (state.foldersOpen ? ' open' : '') + '" id="folderAccHead" data-tip="Pastas & Projetos|Abre/fecha a lista de pastas. Cada pasta guarda o cwd e o agente predefinido.">' +
        '<span class="folder-acc-cur"><span class="dot" style="width:9px;height:9px;border-radius:50%;background:' + esc(activeColor) + '"></span>' +
          '<span class="txt">' + esc(activeIcon) + ' ' + esc(activeName) + '</span></span>' +
        '<span class="folder-acc-meta"><span class="folder-acc-cnt">' + activeCount + '</span><span class="folder-acc-chev">▾</span></span>' +
      '</button>';

    if (state.foldersOpen) {
      var rows = '<button type="button" class="folder-acc-row ' + (state.filterProj === "all" ? "active" : "") + '" data-fid="all">' +
        '<span class="folder-acc-row-left"><span class="dot" style="background:#38bdf8"></span><span class="fname">🗂️ Todas as pastas</span></span>' +
        '<span class="folder-acc-cnt">' + cnt("all") + '</span></button>';
      state.folders.forEach(function(f){
        rows += '<button type="button" class="folder-acc-row ' + (state.filterProj === f.id ? "active" : "") + '" data-fid="' + esc(f.id) + '">' +
          '<span class="folder-acc-row-left"><span class="dot" style="background:' + esc(f.color) + '"></span><span class="fname">' + esc(f.icon) + ' ' + esc(f.name) + '</span></span>' +
          '<span class="folder-acc-cnt">' + cnt(f.id) + '</span></button>';
      });
      html += '<div class="folder-acc-list" id="folderAccList">' + rows + '</div>';
    }
    strip.innerHTML = html;

    var head = document.getElementById("folderAccHead");
    if (head) head.onclick = function(){
      state.foldersOpen = !state.foldersOpen;
      try { localStorage.setItem("maestro_folders_open", state.foldersOpen ? "1" : "0"); } catch(e){}
      renderFolderStripAndSelect();
    };
    strip.querySelectorAll("[data-fid]").forEach(function(btn){
      btn.onclick = function(ev){
        ev.stopPropagation();
        state.filterProj = btn.getAttribute("data-fid");
        state.foldersOpen = false;
        try { localStorage.setItem("maestro_folders_open", "0"); } catch(e){}
        if (state.filterProj !== "all") {
          sel.value = state.filterProj;
          loadProjectInspector(state.filterProj);
        }
        renderFolderStripAndSelect();
        renderChatList();
      };
    });

    sel.innerHTML = state.folders.map(function(f){
      return '<option value="' + esc(f.id) + '">' + esc(f.icon) + ' ' + esc(f.name) + '</option>';
    }).join("");
    if (state.folders.some(function(f){ return f.id === curVal; })) sel.value = curVal;
  }

  document.getElementById("btnNewFolder").onclick = function(){
    document.getElementById("folderModal").hidden = false;
  };
  document.getElementById("btnCloseFolderModal").onclick = function(){
    document.getElementById("folderModal").hidden = true;
  };
  document.getElementById("btnSaveFolder").onclick = function(){
    var name = document.getElementById("fName").value.trim();
    if (!name) return;
    api("/api/folders/save", {
      name: name,
      icon: document.getElementById("fIcon").value || "📁",
      color: document.getElementById("fColor").value || "#38bdf8",
      cwd: document.getElementById("fCwd").value || "/home/hubert",
      desc: document.getElementById("fDesc").value || ""
    }).then(function(d){
      state.folders = d.folders || [];
      document.getElementById("folderModal").hidden = true;
      renderFolderStripAndSelect();
    });
  };

  /* ---- SPLIT ARTIFACT / CODE / HTML PREVIEW CANVAS ---- */
  function openCanvas(title, content, lang) {
    var cv = document.getElementById("artifactCanvas");
    var body = document.getElementById("canvasBody");
    var ifr = document.getElementById("canvasIframe");
    var btnPrev = document.getElementById("btnPreviewCanvas");

    state.canvasRawCode = content || "";
    state.canvasPreviewMode = false;
    document.getElementById("canvasTitle").textContent = title || "Artifact";
    body.textContent = state.canvasRawCode;
    body.hidden = false;
    ifr.hidden = true;

    var canPreview = (lang === "html" || lang === "svg" || (content && (content.indexOf("<html") !== -1 || content.indexOf("<svg") !== -1)));
    btnPrev.hidden = !canPreview;
    btnPrev.textContent = "👁 Preview";
    cv.hidden = false;
  }

  document.getElementById("btnPreviewCanvas").onclick = function(){
    var body = document.getElementById("canvasBody");
    var ifr = document.getElementById("canvasIframe");
    var btn = document.getElementById("btnPreviewCanvas");
    state.canvasPreviewMode = !state.canvasPreviewMode;
    if (state.canvasPreviewMode) {
      body.hidden = true;
      ifr.hidden = false;
      ifr.srcdoc = state.canvasRawCode;
      btn.textContent = "💻 Código";
    } else {
      body.hidden = false;
      ifr.hidden = true;
      btn.textContent = "👁 Preview";
    }
  };
  document.getElementById("btnCloseCanvas").onclick = function(){
    document.getElementById("artifactCanvas").hidden = true;
  };
  document.getElementById("btnCopyCanvas").onclick = function(){
    navigator.clipboard && navigator.clipboard.writeText(state.canvasRawCode || "");
    var b = document.getElementById("btnCopyCanvas");
    b.textContent = "✓ Copiado";
    setTimeout(function(){ b.textContent = "Copiar"; }, 1400);
  };

  /* ---- MODULE 2: GALLERY & IMAGE / GIF GENERATOR (WITH PAGINATION, FILTERS & SLIDER) ---- */
  state.galleryAll = [];
  state.galFilter = "all";
  state.galSearch = "";
  state.galPage = 1;
  state.galPerPage = 24;

  function loadGallery() {
    var grid = document.getElementById("galleryGrid");
    grid.innerHTML = '<div class="stat-sub">A carregar galeria…</div>';
    api("/api/gallery").then(function(d){
      state.galleryAll = d.items || [];
      renderGalleryView();
    });
  }

  function renderGalleryView() {
    var grid = document.getElementById("galleryGrid");
    var pag = document.getElementById("galleryPagination");
    var q = (state.galSearch || "").toLowerCase().trim();

    var filtered = state.galleryAll.filter(function(it){
      if (state.galFilter === "ai" && it.kind !== "ai_image" && it.kind !== "gif") return false;
      if (state.galFilter === "paste" && it.kind !== "paste") return false;
      if (state.galFilter === "nas_asset" && it.kind !== "nas_asset") return false;
      if (q && (String(it.prompt || "") + " " + String(it.name || "")).toLowerCase().indexOf(q) === -1) return false;
      return true;
    });

    var total = filtered.length;
    var badge = document.getElementById("galTotalBadge");
    if (badge) badge.textContent = total + (total === 1 ? " imagem" : " imagens");

    if (!total) {
      grid.innerHTML = '<div class="feature-card">Nenhuma imagem encontrada para este filtro.</div>';
      pag.innerHTML = "";
      return;
    }

    var perPage = state.galPerPage || 24;
    var totalPages = Math.max(1, Math.ceil(total / perPage));
    if (state.galPage > totalPages) state.galPage = totalPages;
    if (state.galPage < 1) state.galPage = 1;

    var startIdx = (state.galPage - 1) * perPage;
    var pageItems = filtered.slice(startIdx, startIdx + perPage);

    grid.innerHTML = "";
    pageItems.forEach(function(it){
      var url = "/api/image?path=" + encodeURIComponent(it.path);
      var canDel = (it.kind === "ai_image" || it.kind === "gif" || it.kind === "paste");
      var badgeIcon = it.kind === "gif" ? "🎞 GIF · " : (it.kind === "nas_asset" ? "🖼️ NAS · " : (it.kind === "paste" ? "📋 Print · " : "🎨 AI · "));
      var card = document.createElement("div");
      card.className = "gallery-item anim-in";
      card.innerHTML =
        '<img src="' + url + '" alt="' + esc(it.prompt) + '" loading="lazy">' +
        '<div class="gallery-item-foot">' +
          '<span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="' + esc(it.name) + '">' + badgeIcon + esc(it.prompt || it.name) + '</span>' +
          '<div style="display:flex;gap:4px;flex-shrink:0">' +
            '<button type="button" class="round-tool-btn" data-use-img="' + esc(it.path) + '" title="Usar no Chat">💬</button>' +
            '<a class="round-tool-btn" href="' + url + '" download="' + esc(it.name) + '" title="Download">⬇</a>' +
            (canDel ? '<button type="button" class="round-tool-btn" data-del-img="' + esc(it.path) + '" title="Apagar">🗑</button>' : '') +
          '</div>' +
        '</div>';
      card.querySelector("img").onclick = function(){ window.open(url, "_blank"); };
      card.querySelector("[data-use-img]").onclick = function(){
        state.pendingImages.push(it.path);
        renderAttachStrip();
        setModule("chat");
      };
      var delBtn = card.querySelector("[data-del-img]");
      if (delBtn) {
        delBtn.onclick = function(){
          if (!confirm("Apagar imagem «" + it.name + "»?")) return;
          api("/api/gallery/delete", { path: it.path }).then(function(r){
            if (r.items) { state.galleryAll = r.items; renderGalleryView(); }
          });
        };
      }
      grid.appendChild(card);
    });

    // Render pagination bar
    var pHtml = '<button type="button" class="card-btn" id="galPrevBtn" ' + (state.galPage <= 1 ? 'disabled style="opacity:0.4"' : '') + '>« Anterior</button>';
    for (var p = 1; p <= totalPages; p++) {
      pHtml += '<button type="button" class="card-btn ' + (p === state.galPage ? 'primary' : '') + '" data-gpage="' + p + '">' + p + '</button>';
    }
    pHtml += '<button type="button" class="card-btn" id="galNextBtn" ' + (state.galPage >= totalPages ? 'disabled style="opacity:0.4"' : '') + '>Seguinte »</button>';
    pHtml += '<span style="font-size:0.74rem;color:var(--muted);margin-left:8px">Página <b>' + state.galPage + '</b> de <b>' + totalPages + '</b> (' + total + ' itens)</span>';
    pag.innerHTML = pHtml;

    var prevB = document.getElementById("galPrevBtn");
    var nextB = document.getElementById("galNextBtn");
    if (prevB) prevB.onclick = function(){ if (state.galPage > 1) { state.galPage--; renderGalleryView(); } };
    if (nextB) nextB.onclick = function(){ if (state.galPage < totalPages) { state.galPage++; renderGalleryView(); } };
    pag.querySelectorAll("[data-gpage]").forEach(function(b){
      b.onclick = function(){
        state.galPage = Number(b.getAttribute("data-gpage")) || 1;
        renderGalleryView();
      };
    });
  }

  document.querySelectorAll("#galFilterTabs [data-gkind]").forEach(function(btn){
    btn.onclick = function(){
      document.querySelectorAll("#galFilterTabs [data-gkind]").forEach(function(b){ b.classList.remove("primary"); });
      btn.classList.add("primary");
      state.galFilter = btn.getAttribute("data-gkind");
      state.galPage = 1;
      renderGalleryView();
    };
  });
  document.getElementById("galSearchInput").oninput = function(e){
    state.galSearch = e.target.value || "";
    state.galPage = 1;
    renderGalleryView();
  };
  document.getElementById("galPerPage").onchange = function(e){
    state.galPerPage = Number(e.target.value) || 24;
    state.galPage = 1;
    renderGalleryView();
  };
  document.getElementById("galZoomSlider").oninput = function(e){
    document.documentElement.style.setProperty("--gal-col", e.target.value + "px");
  };

  document.getElementById("btnGenImage").onclick = function(){
    var p = document.getElementById("genMediaPrompt").value.trim() || "Futuristic AI指挥 Maestro neon emblem";
    var asp = document.getElementById("genAspect").value;
    var btn = document.getElementById("btnGenImage");
    btn.disabled = true; btn.textContent = "⏳ A gerar HD…";
    api("/api/media/generate-image", { prompt: p, aspect: asp }).then(function(){
      loadGallery();
      notifyUser("🎨 Imagem AI Criada", p);
    }).finally(function(){
      btn.disabled = false; btn.textContent = "🖼 Gerar Imagem AI";
    });
  };

  document.getElementById("btnGenGif").onclick = function(){
    var p = document.getElementById("genMediaPrompt").value.trim() || "Cyber radar pulse animation";
    var btn = document.getElementById("btnGenGif");
    btn.disabled = true; btn.textContent = "⏳ A animar…";
    api("/api/media/generate-gif", { prompt: p }).then(function(){
      loadGallery();
      notifyUser("🎞 GIF Animado Criado", p);
    }).finally(function(){
      btn.disabled = false; btn.textContent = "🎞 Gerar GIF Animado";
    });
  };

  /* ---- MODULE 3: SONARR / RADARR MEDIA & DOCKER CONTAINERS ---- */
  var mediaQueueLoading = false;
  function loadMediaQueue() {
    if (mediaQueueLoading) return;
    mediaQueueLoading = true;
    var box = document.getElementById("mediaQueueBox");
    api("/api/media/queue").then(function(d){
      var q = d.queue || [];
      var recent = d.recent || [];
      var errors = d.errors || [];
      var recentDetails = box.querySelector('details');
      var recentOpen = !recentDetails || recentDetails.open;
      box.innerHTML = '<div style="margin-bottom:10px">' + q.length + ' na fila · atualização automática a cada 15 s</div>' +
        errors.map(function(e){ return '<div style="color:var(--yellow);margin-bottom:8px">⚠️ ' + esc(e) + '</div>'; }).join('') +
        q.map(function(x){
          var pct = Math.max(0, Math.min(100, Number(x.progress) || 0));
          return '<div style="padding:10px 0;border-bottom:1px solid var(--line-strong)">' +
            '<div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap"><strong style="color:var(--text);overflow-wrap:anywhere">' + esc(x.title) + '</strong>' +
            '<span>' + esc(x.source) + '</span></div>' +
            '<div style="margin:5px 0;color:' + (x.warning ? 'var(--yellow)' : 'var(--muted)') + '">' + esc(x.status_label) + ' · <b>' + pct + '%</b>' +
            (x.timeleft && x.timeleft !== '—' ? ' · falta ' + esc(x.timeleft) : '') + '</div>' +
            '<progress max="100" value="' + pct + '" aria-label="Progresso de ' + esc(x.title) + '" style="width:100%;height:8px;accent-color:#38bdf8">' + pct + '%</progress>' +
            (x.details ? '<div style="margin-top:4px;overflow-wrap:anywhere">⚠️ ' + esc(x.details) + '</div>' : '') + '</div>';
        }).join('') +
        (!q.length ? '<div style="margin:8px 0">' + (errors.length ? 'Não há downloads ativos confirmados.' : 'Sem downloads ativos neste momento.') + '</div>' : '') +
        (recent.length ? '<details ' + (recentOpen ? 'open ' : '') + 'style="margin-top:12px"><summary style="cursor:pointer">Pedidos recentes no Radarr / Sonarr</summary>' +
          recent.map(function(x){
            return '<div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap;padding:7px 0;border-bottom:1px solid var(--line-strong)">' +
              '<span style="overflow-wrap:anywhere">' + esc(x.title) + (x.year ? ' (' + esc(x.year) + ')' : '') + '</span>' +
              '<span>' + (x.available ? '✅ ' : '⏳ ') + esc(x.status_label) + ' · ' + esc(x.source) + '</span></div>';
          }).join('') + '</details>' : '') +
        '<div style="font-size:0.72rem;margin-top:10px">Atualizado às ' + new Date((d.updated_at || Date.now()/1000) * 1000).toLocaleTimeString('pt-PT') + '</div>';
    }).catch(function(){
      box.textContent = '⚠️ Não foi possível atualizar o estado dos downloads. Volta a tentar em Atualizar.';
    }).finally(function(){
      mediaQueueLoading = false;
    });
  }
  document.getElementById("btnRefreshMediaQueue").onclick = loadMediaQueue;
  setInterval(function(){ if (state.mod === "media" && !document.hidden) loadMediaQueue(); }, 15000);

  function searchMediaNow() {
    var q = document.getElementById("mediaSearchInput").value.trim();
    var kind = document.getElementById("selMediaKind").value;
    if (!q) return;
    var grid = document.getElementById("mediaResultsGrid");
    grid.innerHTML = '<div class="stat-sub">A pesquisar em ' + (kind === "series" ? "Sonarr (:8989)" : "Radarr (:7878)") + '…</div>';
    api("/api/media/search?kind=" + encodeURIComponent(kind) + "&q=" + encodeURIComponent(q)).then(function(d){
      var res = d.results || [];
      if (!res.length) {
        grid.innerHTML = '<div class="stat-sub">Sem resultados encontrados.</div>';
        return;
      }
      grid.innerHTML = "";
      res.forEach(function(it){
        var idVal = it.kind === "series" ? it.tvdbId : it.tmdbId;
        var card = document.createElement("div");
        card.className = "poster-card anim-in";
        card.innerHTML =
          (it.poster ? '<img src="' + esc(it.poster) + '" alt="Poster">' : '<div style="width:78px;height:116px;border-radius:9px;background:#080d18;display:grid;place-items:center">🎬</div>') +
          '<div style="flex:1;min-width:0;display:flex;flex-direction:column;justify-content:space-between">' +
            '<div>' +
              '<strong style="font-size:0.88rem">' + esc(it.title) + ' (' + esc(it.year || "—") + ')</strong>' +
              '<div style="font-size:0.72rem;color:var(--muted);margin-top:4px;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden">' + esc(it.overview || "") + '</div>' +
            '</div>' +
            '<div style="margin-top:8px">' +
              (it.already_added
                ? '<span style="color:var(--green);font-size:0.75rem;font-weight:700">✓ Já adicionado</span>'
                : '<button type="button" class="send-circle-btn" style="height:30px;font-size:0.74rem">⬇ Sacar Agora</button>') +
            '</div>' +
          '</div>';
        var btn = card.querySelector("button");
        if (btn) {
          btn.onclick = function(){
            btn.disabled = true; btn.textContent = "A enviar…";
            api("/api/media/request", { kind: it.kind, id: idVal, title: it.title }).then(function(r){
              if (r.ok) {
                btn.textContent = "✓ Pedido adicionado";
                notifyUser("🍿 " + it.title, r.message || "Adicionado com sucesso!");
                loadMediaQueue();
              } else {
                btn.disabled = false; btn.textContent = "⬇ Sacar Agora";
                alert(r.error || "Erro ao adicionar.");
              }
            });
          };
        }
        grid.appendChild(card);
      });
    });
  }
  document.getElementById("btnSearchMedia").onclick = searchMediaNow;
  document.getElementById("mediaSearchInput").addEventListener("keydown", function(e){
    if (e.key === "Enter") { e.preventDefault(); searchMediaNow(); }
  });

  function loadContainers() {
    var grid = document.getElementById("containersGrid");
    api("/api/containers").then(function(d){
      var list = d.containers || [];
      grid.innerHTML = "";
      list.forEach(function(c){
        var isUp = c.state === "running";
        var card = document.createElement("div");
        card.className = "feature-card anim-in";
        card.innerHTML =
          '<div style="display:flex;justify-content:space-between;align-items:center">' +
            '<strong style="font-size:0.86rem">' + esc(c.icon) + ' ' + esc(c.name) + '</strong>' +
            '<span style="font-family:var(--mono);font-size:0.68rem;color:' + (isUp ? 'var(--green)' : 'var(--rose)') + '">' + esc(c.status) + '</span>' +
          '</div>' +
          '<div style="display:flex;gap:5px;flex-wrap:wrap">' +
            (c.prompts || []).map(function(p){
              return '<button type="button" class="round-tool-btn magic" data-cprompt="' + esc(p) + '">✨ ' + esc(p.slice(0, 34)) + '…</button>';
            }).join("") +
          '</div>' +
          '<div style="display:flex;gap:6px;margin-top:auto">' +
            '<button type="button" class="round-tool-btn" data-clogs="' + esc(c.name) + '">📜 Logs</button>' +
            '<button type="button" class="round-tool-btn" data-crestart="' + esc(c.name) + '">🔄 Restart</button>' +
            (c.port ? '<a class="round-tool-btn" href="http://' + window.location.hostname + ':' + c.port + '" target="_blank" style="text-decoration:none">↗ Abrir</a>' : '') +
          '</div>';

        card.querySelectorAll("[data-cprompt]").forEach(function(b){
          b.onclick = function(){
            setModule("chat");
            var ta = document.getElementById("promptInput");
            ta.value = b.getAttribute("data-cprompt");
            ta.focus();
          };
        });
        card.querySelector("[data-clogs]").onclick = function(){
          api("/api/container/action", { name: c.name, action: "logs" }).then(function(r){
            openCanvas("Logs · " + c.name, r.output || r.error || "");
          });
        };
        card.querySelector("[data-crestart]").onclick = function(){
          if (!confirm("Reiniciar container " + c.name + "?")) return;
          api("/api/container/action", { name: c.name, action: "restart" }).then(function(r){
            notifyUser("🐳 Docker", r.output || "Reiniciado");
            loadContainers();
          });
        };
        grid.appendChild(card);
      });
    });
  }

  /* ---- MODULE 4: HOME ASSISTANT (3D PRINTERS, PLUGS) & SCHEDULERS ---- */
  function loadHaDashboard() {
    api("/api/ha/dashboard").then(function(d){
      var pGrid = document.getElementById("haPrintersGrid");
      var sGrid = document.getElementById("haSwitchesGrid");
      pGrid.innerHTML = "";
      (d.printers || []).forEach(function(p){
        var pct = parseFloat(p.progress) || 0;
        var card = document.createElement("div");
        card.className = "feature-card anim-in";
        card.innerHTML =
          '<div class="stat-head"><span>' + esc(p.icon) + ' ' + esc(p.name) + '</span><span style="color:var(--cyan)">' + esc(p.status) + '</span></div>' +
          '<div class="meter-track"><div class="meter-fill ok" style="width:' + Math.min(100, pct) + '%"></div></div>' +
          '<div class="stat-sub">Progresso: <b>' + pct + '%</b> · Bico/Cama: <b>' + esc(p.nozzle) + '° / ' + esc(p.bed) + '°</b> · Tomada: <b>' + esc(p.plug_state) + '</b></div>' +
          '<div style="display:flex;gap:6px">' +
            '<button type="button" class="round-tool-btn" data-ha-ent="' + esc(p.plug_entity) + '">⏻ Tomada (' + esc(p.plug_state) + ')</button>' +
            (p.light_entity ? '<button type="button" class="round-tool-btn" data-ha-ent="' + esc(p.light_entity) + '">💡 Luz (' + esc(p.light_state) + ')</button>' : '') +
          '</div>';
        card.querySelectorAll("[data-ha-ent]").forEach(function(b){
          b.onclick = function(){
            api("/api/ha/action", { entity_id: b.getAttribute("data-ha-ent"), action: "toggle" }).then(loadHaDashboard);
          };
        });
        pGrid.appendChild(card);
      });

      sGrid.innerHTML = "";
      (d.switches || []).forEach(function(sw){
        var on = sw.state === "on";
        var card = document.createElement("div");
        card.className = "feature-card anim-in";
        card.style.borderColor = on ? "rgba(52,211,153,.45)" : "var(--line)";
        card.innerHTML =
          '<div style="display:flex;justify-content:space-between;align-items:center">' +
            '<strong>' + esc(sw.icon) + ' ' + esc(sw.name) + '</strong>' +
            '<button type="button" class="icon-pill-btn ' + (on ? 'active' : '') + '">' + (on ? 'ON' : 'OFF') + '</button>' +
          '</div>';
        card.querySelector("button").onclick = function(){
          api("/api/ha/action", { entity_id: sw.entity_id, action: "toggle" }).then(loadHaDashboard);
        };
        sGrid.appendChild(card);
      });
    });
  }
  document.getElementById("btnRefreshHa").onclick = loadHaDashboard;

  function loadSchedules() {
    var grid = document.getElementById("schedulesGrid");
    api("/api/schedules").then(function(d){
      var list = d.schedules || [];
      grid.innerHTML = "";
      list.forEach(function(s){
        var card = document.createElement("div");
        card.className = "stat-card anim-in";
        card.innerHTML =
          '<div class="stat-head">' +
            '<span>⏰ ' + esc(s.time_hhmm) + ' · ' + esc(s.name) + '</span>' +
            '<span style="color:' + (s.enabled ? 'var(--green)' : 'var(--muted)') + '">' + (s.enabled ? 'ATIVO' : 'PAUSA') + '</span>' +
          '</div>' +
          '<div class="stat-sub">' + esc(s.target) + '<br>Última: ' + esc(s.last_run || "Nunca") + ' (' + esc(s.last_result || "") + ')</div>' +
          '<div style="display:flex;gap:6px">' +
            '<button type="button" class="round-tool-btn magic" data-srun="' + esc(s.id) + '">▶ Correr Agora</button>' +
            '<button type="button" class="round-tool-btn" data-stog="' + esc(s.id) + '">' + (s.enabled ? '⏸ Pausar' : '▶ Ativar') + '</button>' +
            '<button type="button" class="round-tool-btn" data-sdel="' + esc(s.id) + '">🗑</button>' +
          '</div>';
        card.querySelector("[data-srun]").onclick = function(){
          api("/api/schedules/run", { id: s.id }).then(function(r){
            notifyUser("⏰ Automação Executada", r.result || "Concluído");
            loadSchedules();
          });
        };
        card.querySelector("[data-stog]").onclick = function(){
          api("/api/schedules/toggle", { id: s.id }).then(loadSchedules);
        };
        card.querySelector("[data-sdel]").onclick = function(){
          api("/api/schedules/toggle", { id: s.id, delete: true }).then(loadSchedules);
        };
        grid.appendChild(card);
      });
    });
  }

  document.getElementById("btnAddSchedule").onclick = function(){
    var name = document.getElementById("schedName").value.trim();
    var target = document.getElementById("schedTarget").value.trim();
    if (!name || !target) return;
    api("/api/schedules/save", {
      name: name,
      time_hhmm: document.getElementById("schedTime").value || "09:00",
      kind: document.getElementById("schedKind").value,
      target: target,
      enabled: true
    }).then(function(){
      document.getElementById("schedName").value = "";
      document.getElementById("schedTarget").value = "";
      loadSchedules();
    });
  };

  /* ---- MODULE 5: ARTIFACTS HUB ---- */
  function loadArtifacts() {
    var grid = document.getElementById("artifactsGrid");
    api("/api/artifacts").then(function(d){
      var list = d.artifacts || [];
      if (!list.length) {
        grid.innerHTML = '<div class="feature-card">Os blocos de código e interfaces gerados pelo Maestro aparecem aqui automaticamente!</div>';
        return;
      }
      grid.innerHTML = "";
      list.forEach(function(a){
        var card = document.createElement("div");
        card.className = "feature-card anim-in";
        card.innerHTML =
          '<div class="stat-head"><span>🧩 ' + esc(a.title) + '</span><span style="font-family:var(--mono);font-size:0.68rem;color:var(--cyan)">' + esc(a.lang) + '</span></div>' +
          '<pre style="margin:0;padding:8px;border-radius:8px;background:#050810;font-family:var(--mono);font-size:0.7rem;max-height:110px;overflow:hidden;color:#cbd5e1">' + esc((a.content || "").slice(0, 320)) + '</pre>' +
          '<div style="display:flex;gap:6px;margin-top:auto">' +
            '<button type="button" class="round-tool-btn magic" data-aopen="' + esc(a.id) + '">🪟 Abrir no Canvas</button>' +
            '<button type="button" class="round-tool-btn" data-adel="' + esc(a.id) + '">🗑</button>' +
          '</div>';
        card.querySelector("[data-aopen]").onclick = function(){
          openCanvas(a.title, a.content, a.lang);
        };
        card.querySelector("[data-adel]").onclick = function(){
          api("/api/artifacts/delete", { id: a.id }).then(loadArtifacts);
        };
        grid.appendChild(card);
      });
    });
  }

  /* ---- CHAT STREAM & MARKDOWN ---- */
  /* Registo persistente de snippets: os botões Copiar/Canvas guardam um id estável,
     para que o render incremental (que já não recria o chat inteiro) não os parta. */
  var codeStore = {};
  var codeSeq = 0;
  var codeOrder = [];
  function newCodeId(lang, code) {
    var cid = "cb" + (++codeSeq);
    codeStore[cid] = { lang: lang, code: code };
    codeOrder.push(cid);
    while (codeOrder.length > 800) { delete codeStore[codeOrder.shift()]; }
    return cid;
  }
  function mdInline(text) {
    var s = esc(text);
    s = s.replace(/`([^`\n]+)`/g, '<code class="inline-code">$1</code>');
    s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,;:!?]|$)/g, '$1<em>$2</em>');
    s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
    return s;
  }
  function mdCells(line) {
    var t = String(line || "").trim();
    if (t.charAt(0) === "|") t = t.slice(1);
    if (t.charAt(t.length - 1) === "|") t = t.slice(0, -1);
    return t.split("|").map(function(c){ return c.trim(); });
  }
  function renderRichMarkdown(md) {
    var blocks = [];
    var raw = String(md || "").replace(/\r\n/g, "\n");
    raw = raw.replace(/```([a-zA-Z0-9_+-]*)\n([\s\S]*?)```/g, function(_, lang, code){
      var idx = newCodeId(lang || "code", code);
      var token = "@@CODEBLOCK_" + blocks.length + "@@";
      blocks.push(
        '<div class="code-card">' +
          '<div class="code-card-head">' +
            '<span>' + esc(lang || "código") + '</span>' +
            '<div class="code-card-acts">' +
              '<button type="button" data-code-copy="' + idx + '">📋 Copiar</button>' +
              '<button type="button" data-code-canvas="' + idx + '">🪟 Canvas</button>' +
            '</div>' +
          '</div>' +
          '<pre><code>' + esc(code) + '</code></pre>' +
          (/^(bash|sh|shell)$/i.test(lang) ? '<div class="code-card-foot"><button type="button" class="code-authorize" data-code-action="' + idx + '"><span class="code-authorize-ico" aria-hidden="true">⚡</span><span>' + ui('Autorizar e avançar', 'Authorize and proceed') + '</span></button></div>' : '') +
        '</div>'
      );
      return "\n" + token + "\n";
    });

    var lines = raw.split("\n");
    var out = [];
    var i = 0;
    function flushParagraph(buf) {
      if (!buf.length) return;
      out.push('<div class="rich-p">' + buf.map(mdInline).join("<br>") + '</div>');
      buf.length = 0;
    }
    var para = [];

    while (i < lines.length) {
      var line = lines[i];
      var t = line.trim();

      if (!t) { flushParagraph(para); i++; continue; }

      var cb = t.match(/^@@CODEBLOCK_(\d+)@@$/);
      if (cb) {
        flushParagraph(para);
        out.push(blocks[Number(cb[1])] || "");
        i++; continue;
      }

      var hm = t.match(/^(#{1,4})\s+(.+)$/);
      if (hm) {
        flushParagraph(para);
        var lvl = hm[1].length;
        out.push('<div class="rich-h' + lvl + '">' + mdInline(hm[2]) + '</div>');
        i++; continue;
      }

      if (/^(---+|\*\*\*+|___+)$/.test(t)) {
        flushParagraph(para);
        out.push('<hr class="rich-hr">');
        i++; continue;
      }

      // Tabela markdown: | a | b | + |---|:--:|
      if (t.charAt(0) === "|" && i + 1 < lines.length && /^\s*\|?[\s:|-]*-[\s:|-]*\|?[\s:|-]*$/.test(lines[i + 1].trim())) {
        flushParagraph(para);
        var headers = mdCells(t);
        var rows = [];
        i += 2;
        while (i < lines.length && lines[i].trim().charAt(0) === "|") {
          rows.push(mdCells(lines[i]));
          i++;
        }
        var thead = headers.map(function(h){ return '<th>' + mdInline(h) + '</th>'; }).join("");
        var tbody = rows.map(function(r){
          return '<tr>' + headers.map(function(_, ci){ return '<td>' + mdInline(r[ci] || "") + '</td>'; }).join("") + '</tr>';
        }).join("");
        out.push('<div class="rich-table-wrap"><table class="rich-table"><thead><tr>' + thead + '</tr></thead><tbody>' + tbody + '</tbody></table></div>');
        continue;
      }

      // Callout / blockquote
      if (/^>\s?/.test(t)) {
        flushParagraph(para);
        var quote = [];
        while (i < lines.length && /^>\s?/.test(lines[i].trim())) {
          quote.push(lines[i].trim().replace(/^>\s?/, ""));
          i++;
        }
        var qtxt = quote.join(" ");
        var qcls = /(erro|crítico|critico|falha|❌|🚨)/i.test(qtxt) ? "err"
                 : /(aviso|atenção|atencao|⚠️|warn)/i.test(qtxt) ? "warn"
                 : /(ok|resolvido|sucesso|✅|concluído|concluido)/i.test(qtxt) ? "ok" : "";
        out.push('<div class="rich-callout ' + qcls + '">' + quote.map(mdInline).join("<br>") + '</div>');
        continue;
      }

      // Checklists
      if (/^\s*[-*]\s+\[[ xX]\]\s+/.test(line)) {
        flushParagraph(para);
        var items = [];
        while (i < lines.length && /^\s*[-*]\s+\[[ xX]\]\s+/.test(lines[i])) {
          var lm = lines[i].match(/^\s*[-*]\s+\[([ xX])\]\s+(.*)$/);
          var done = lm[1].toLowerCase() === "x";
          items.push('<div class="rich-list-item rich-check' + (done ? ' done' : '') + '"><span class="rich-check-box">' + (done ? "☑" : "☐") + '</span><span>' + mdInline(lm[2]) + '</span></div>');
          i++;
        }
        out.push('<div class="rich-list">' + items.join("") + '</div>');
        continue;
      }

      // Listas com marcadores
      if (/^\s*[-*•]\s+/.test(line)) {
        flushParagraph(para);
        var litems = [];
        while (i < lines.length && /^\s*[-*•]\s+/.test(lines[i]) && !/^\s*[-*]\s+\[[ xX]\]\s+/.test(lines[i])) {
          var li = lines[i].replace(/^\s*[-*•]\s+/, "");
          litems.push('<div class="rich-list-item"><span class="rich-list-bullet">▸</span><span>' + mdInline(li) + '</span></div>');
          i++;
        }
        out.push('<div class="rich-list">' + litems.join("") + '</div>');
        continue;
      }

      // Listas numeradas
      if (/^\s*\d+[.)]\s+/.test(line)) {
        flushParagraph(para);
        var nitems = [];
        while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) {
          var nm = lines[i].match(/^\s*(\d+)[.)]\s+(.*)$/);
          nitems.push('<div class="rich-list-item"><span class="rich-list-bullet">' + nm[1] + '.</span><span>' + mdInline(nm[2]) + '</span></div>');
          i++;
        }
        out.push('<div class="rich-list">' + nitems.join("") + '</div>');
        continue;
      }

      para.push(line);
      i++;
    }
    flushParagraph(para);
    return out.join("");
  }

  /* ---- RENDER INCREMENTAL DO CHAT (sem "flash" a cada polling) ----
     Antes: cada atualização fazia box.innerHTML = "" e recriava todos os turnos,
     o que reiniciava a animação .anim-in => o chat "piscava" a cada 1,8s.
     Agora: cada turno tem uma assinatura (hash). Só é criado/substituído quando
     o conteúdo muda; o turno "ao vivo" é atualizado em sítio (textContent/innerHTML
     de blocos pequenos). Nada é destruído enquanto o agente pensa. */
  var _turnCache = {};   // idx -> { sig, el }
  var _liveEl = null;

  function hash32(str) {
    var h = 5381, i, s = String(str || "");
    for (i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
    return h;
  }

  function fmtChars(n) {
    n = Number(n) || 0;
    if (n >= 1000) return (n / 1000).toFixed(n >= 10000 ? 0 : 1) + "k chars";
    return n + " chars";
  }

  function msgSig(m, idx) {
    return (state.lang || "pt") + "§" + idx + "§" + (m.role || "") + "§" + (m.agent_used || "") + "§" +
      ((m.text || "").length) + "§" + hash32(m.text) + "§" +
      ((m.images || []).length) + "§" + ((m.images || []).join(",")) + "§" +
      ((m.docs || []).map(function(d){ return d.path; }).join(",")) + "§" +
      (m.elapsed_s || "") + "§" +
      (m.plan ? "P" + (m.plan.domain || "") + (m.plan.dimension || "") + (m.plan.agents || []).join(",") + (m.tools || []).join(",") : "");
  }

  function buildTurnNode(m, animate) {
    var turn = document.createElement("div");
    turn.className = "turn" + (animate === false ? "" : " anim-in") + " " + (m.role === "user" ? "user" : "assistant");
    var bubble = document.createElement("div");
    bubble.className = "bubble";
    var inner = "";

    if (m.role === "assistant" && m.plan) {
      inner +=
        '<details class="orch-details">' +
          '<summary>🧠 ' + ui('Orquestração', 'Orchestration') + ' · ' + esc(routeText(m.plan.domain)) + ' (' + esc(routeText(m.plan.dimension)) + ')' +
            (m.plan.agents && m.plan.agents.length ? ' → ' + esc(m.plan.agents.join(" ➔ ")) : '') +
            (m.elapsed_s ? ' · ' + m.elapsed_s + 's' : '') +
          '</summary>' +
          '<div class="orch-body">' +
            '<div><b>Pipeline:</b> ' + esc((m.plan.agents || []).join(" ➔ ")) + '</div>' +
            (m.plan.reason ? '<div><b>' + ui('Porquê:', 'Why:') + '</b> ' + esc(m.plan.reason) + '</div>' : '') +
            (m.tools && m.tools.length ? '<div><b>' + ui('Execução:', 'Execution:') + '</b> ' + esc(m.tools.join(" · ")) + '</div>' : '') +
          '</div>' +
        '</details>';
    }
    if (m.plan && m.plan.workflow) inner += renderWorkflow(m.plan.workflow);
    inner += renderRichMarkdown(m.text || "");
    if (m.docs && m.docs.length) {
      inner += '<div class="doc-chips">' + m.docs.map(function(d){
        return '<span class="doc-chip" title="' + esc(d.path || "") + '">📄 ' + esc(d.name || "colado") +
               ' · ' + fmtChars(d.chars) + '</span>';
      }).join("") + '</div>';
    }
    if (m.images && m.images.length) {
      inner += '<div class="turn-imgs">' + m.images.map(function(p){
        var url = '/api/image?path=' + encodeURIComponent(p);
        return '<img src="' + url + '" alt="Anexo" onclick="window.open(\'' + url + '\',\'_blank\')">';
      }).join("") + '</div>';
    }
    bubble.innerHTML = inner;
    turn.appendChild(bubble);

    var foot = document.createElement("div");
    foot.className = "turn-foot";
    foot.textContent = m.role === "assistant" ? statusText(m.agent_used || "Maestro AI") : ui("Tu", "You");
    turn.appendChild(foot);
    return turn;
  }


  function ui(pt, en) { return state.lang === "en" ? en : pt; }

  // Only server-owned status text goes through here; conversation prose stays intact.
  function statusText(value) {
    if (["evaluating", "dependency", "capacity", "quota", "engine_busy", "interrupted"].indexOf(value) !== -1) return queueReason({reason:value});
    var text = String(value || "");
    if (state.lang !== "en") return text;
    var labels = {
      "Em curso": "In progress", "Interrompido": "Stopped",
      "Última resposta": "Last reply", "Último pedido": "Last request",
      "Nova conversa": "New chat", "À espera do primeiro pedido": "Waiting for the first request",
      "A processar anexos": "Processing attachments"
    };
    if (labels[text]) return labels[text];
    return text
      .replace(/A iniciar orquestração Maestro/, "Starting Maestro orchestration")
      .replace(/Contexto de passagem carregado do turno anterior/, "Handoff context loaded from the previous turn")
      .replace(/A executar com /, "Running with ")
      .replace(/A orquestrar/, "Orchestrating")
      .replace(/A processar (\d+) imagem\(ns\) anexada\(s\)/, "Processing $1 attached image(s)")
      .replace(/A ler ficheiro: /, "Reading file: ")
      .replace(/A editar ficheiro: /, "Editing file: ")
      .replace(/A criar\/escrever: /, "Creating/writing: ")
      .replace(/A executar: /, "Executing: ")
      .replace(/Pesquisa Web: /, "Web search: ")
      .replace(/A inspecionar código: /, "Inspecting code: ")
      .replace(/ a analisar o pedido/, " analyzing the request")
      .replace(/Tempo limite atingido — a terminar o agente/, "Time limit reached — stopping the agent")
      .replace(/Sessão iniciada · A inspecionar workspace e ferramentas/, "Session started · Inspecting workspace and tools")
      .replace(/A redigir resposta final/, "Writing the final reply")
      .replace(/Failover automático para /, "Automatic failover to ")
      .replace(/Plano: /, "Plan: ")
      .replace(/· motor: /, "· engine: ")
      .replace(/· suplente disponível: /, "· available substitute: ")
      .replace(/Motor · /, "Engine · ")
      .replace(/Maestro Substituto/g, "Substitute Maestro");
  }

  function routeText(value) {
    if (state.lang !== "en") return String(value || "");
    return String(value || "").replace(/Sistema \/ Pulso/, "System / Pulse")
      .replace(/Média \(Execução Cirúrgica\)/, "Medium (Targeted execution)")
      .replace(/Média/g, "Medium").replace(/Baixa/g, "Low").replace(/Alta/g, "High");
  }

  function stepsFeedHtml(steps) {
    return (steps || []).slice(-6).map(function(st, idx, arr){
      var isLast = (idx === arr.length - 1);
      return '<div class="step-row' + (isLast ? ' latest' : '') + '">' +
        '<span>' + (isLast ? '▸' : '✓') + '</span>' +
        '<span>' + esc(routeText(statusText(st))) + '</span>' +
      '</div>';
    }).join("");
  }

  function buildLiveTurn(j) {
    var el = document.createElement("div");
    el.className = "turn assistant live-turn";
    el.innerHTML =
      '<div class="bubble live-work-bubble">' +
        '<div class="live-work-head">' +
          '<div class="live-work-status">' +
            '<span class="typing-dots"><span></span><span></span><span></span></span>' +
            '<span class="step-txt"></span>' +
          '</div>' +
          '<span class="live-work-timer"></span>' +
        '</div>' +
        '<div class="orch-decision" hidden>' +
          '<div class="od-head">🧭 ' + ui("DECISÃO DO ORQUESTRADOR", "ORCHESTRATOR DECISION") + '</div>' +
          '<div class="od-route"></div>' +
          '<div class="od-why" hidden></div>' +
        '</div>' +
        '<div class="step-sub" hidden></div>' +
        '<div class="live-workflow"></div>' +
        '<div class="live-steps-feed"></div>' +
        '<div class="live-partial" hidden></div>' +
      '</div>' +
      '<div class="turn-foot"></div>';
    updateLiveTurn(el, j, true);
    return el;
  }

  /* Atualiza o turno ao vivo SEM o recriar: só mexe no que mudou. */
  function updateLiveTurn(el, j, force) {
    if (!el || !j) return;
    var waiting = j.status === "queued";
    var txt = el.querySelector(".step-txt");
    if (txt) txt.textContent = statusText(j.current_step || "🧠 A orquestrar…");
    var timer = el.querySelector(".live-work-timer");
    if (timer) {
      var secs = Math.max(0, Math.round(j.elapsed_s || 0));
      timer.textContent = waiting ? ui("Em fila", "Queued") : "⏱ " + (secs >= 60 ? Math.floor(secs / 60) + "m" + String(secs % 60).padStart(2, "0") + "s" : secs + "s");
    }
    var dots = el.querySelector(".typing-dots");
    if (dots) dots.hidden = waiting;

    var od = el.querySelector(".orch-decision");
    if (od) {
      var pl = j.plan || {};
      var route = [routeText(pl.domain), routeText(pl.dimension)].filter(Boolean).join(" · ");
      var agents = (pl.agents || []).join(" ➔ ");
      var why = pl.reason || "";
      var has = !!(route || agents || why);
      od.hidden = !has;
      if (has) {
        var rl = od.querySelector(".od-route");
        var txt = (route ? route : "") + (agents ? (route ? "   →   " : "") + agents : "");
        if (rl.textContent !== txt) rl.textContent = txt;
        var wl = od.querySelector(".od-why");
        wl.hidden = !why;
        if (why) wl.textContent = ui("porquê: ", "why: ") + why;
      }
    }

    var wf = el.querySelector(".live-workflow");
    if (wf) updateWorkflowPanel(wf, j.workflow);
    var sub = el.querySelector(".step-sub");
    if (sub) {
      var bits = [];
      if (j.last_line) bits.push("↳ " + (j.last_line.length > 150 ? j.last_line.slice(0, 150) + "…" : j.last_line));
      if (j.cpu_s > 0) bits.push("cpu " + j.cpu_s + "s");
      if (j.idle_s > 20) bits.push(ui("sem saída há ", "no output for ") + Math.round(j.idle_s) + "s");
      var subTxt = bits.join(" · ");
      if (sub.textContent !== subTxt) sub.textContent = subTxt;
      /* Nunca esconder: a linha tem altura reservada em CSS, por isso aparecer
         ou desaparecer texto já não faz o balão (e o chat) saltar. */
      if (sub.hidden) sub.hidden = false;
    }

    var sSig = (j.steps || []).join("|");
    if (force || el.__stepsSig !== sSig) {
      el.__stepsSig = sSig;
      var feed = el.querySelector(".live-steps-feed");
      if (feed) feed.innerHTML = stepsFeedHtml(j.steps);
    }

    var ptext = j.partial_text || "";
    var pSig = ptext.length + "§" + hash32(ptext);
    if (force || el.__partSig !== pSig) {
      el.__partSig = pSig;
      var pb = el.querySelector(".live-partial");
      if (pb) {
        pb.hidden = !ptext;
        if (ptext) pb.innerHTML = renderRichMarkdown(ptext);
      }
    }

    var foot = el.querySelector(".turn-foot");
    if (foot) {
      var _maestro = j.maestro_label || (j.plan || {}).maestro_name || "";
      foot.textContent = (_maestro ? ui("Maestro disponível: ", "Available Maestro: ") + statusText(_maestro) + " · " : "") + statusText(j.agent_label || "Maestro AI") +
        (waiting ? ui(" · Em fila · aguarda execução", " · Queued · waiting to start") : ui(" · Em execução ao vivo (podes escrever abaixo para meter em fila ou orientar)", " · Running live (type below to queue a message or steer)")) +
        ((j.parallel_count || 0) > 1 ? ui(" · Agentes ativos: ", " · Active agents: ") + (j.active_agents || []).map(function(a){ return a.name; }).join(" + ") : "");
    }
    stickToBottom();
  }

  /* Cria o turno ao vivo se ainda não existir; nunca toca nos turnos já renderizados. */
  function ensureLiveTurn(j) {
    var box = document.getElementById("chatScroll");
    if (!box) return;
    if (!_liveEl || _liveEl.parentNode !== box) {
      _liveEl = buildLiveTurn(j);
      placeLiveTurn(box, j);
      stickToBottom();
      return;
    }
    placeLiveTurn(box, j);
    updateLiveTurn(_liveEl, j);
  }

  function placeLiveTurn(box, job) {
    var messages = (state.currentChat || {}).messages || [];
    var idx = messages.findIndex(function(m){ return m.role === 'user' && m.turn_id && m.turn_id === job.task_id; });
    var request = idx >= 0 && _turnCache[idx] && _turnCache[idx].el;
    if (request && request.parentNode === box) {
      if (request.nextSibling !== _liveEl) box.insertBefore(_liveEl, request.nextSibling);
    } else if (_liveEl.parentNode !== box || _liveEl.nextSibling) box.appendChild(_liveEl);
  }

  /* ---- SEGUIMENTO DO FIM DO CHAT (sem "yank" enquanto lês) ----
     O erro antigo: cada poll (950 ms) fazia scroll para o fim sempre que o
     chat estava a menos de 170 px do fundo. Bastava o Hubert subir um pouco
     para ler e era puxado outra vez para baixo — e, como o balão ao vivo
     cresce e encolhe, o chat parecia andar para cima e para baixo.

     Agora o seguimento é uma intenção explícita:
       - liga-se apenas quando o utilizador está mesmo no fim (<= 4 px);
       - desliga-se no primeiro gesto para cima (roda, toque ou scroll);
       - só volta a ligar quando ele regressa ao fim ou carrega em «Ir para o fim»;
       - o scroll automático é instantâneo, coalescido num frame, e o evento
         que ele próprio dispara é ignorado (não conta como gesto do utilizador). */
  var SCROLL_BOTTOM_EPS = 4;
  var _autoScrollAt = 0;
  var _autoScrollTop = -1;
  var _autoScrollRaf = 0;
  var _smoothUntil = 0;

  function chatScrollBox() { return document.getElementById("chatScroll"); }

  function distanceToBottom(box) {
    if (!box) return 0;
    return Math.max(0, box.scrollHeight - box.scrollTop - box.clientHeight);
  }

  function setChatFollow(on) {
    var box = chatScrollBox();
    if (!box) return;
    box.__follow = !!on;
    if (on) {
      var btn = document.getElementById("btnJumpEnd");
      if (btn) btn.classList.remove("show");
    }
  }

  function noteChatIntent() {
    var box = chatScrollBox();
    if (box) box.__userIntentAt = Date.now();
  }

  function scrollChatToBottom(force, smooth) {
    var box = chatScrollBox();
    if (!box) return;
    if (!force && box.__follow === false) return;
    if (document.hidden) return;
    var top = Math.max(0, box.scrollHeight - box.clientHeight);
    if (smooth) {
      box.__follow = true;
      _autoScrollAt = Date.now();
      _autoScrollTop = top;
      _smoothUntil = Date.now() + 900;
      box.scrollTo({ top: top, behavior: "smooth" });
      return;
    }
    if (Math.abs(box.scrollTop - top) < 1) return;
    if (_autoScrollRaf) return;
    var mustScroll = !!force;
    _autoScrollRaf = window.requestAnimationFrame(function () {
      _autoScrollRaf = 0;
      var b = chatScrollBox();
      if (!b || (!mustScroll && b.__follow === false)) return;
      var target = Math.max(0, b.scrollHeight - b.clientHeight);
      _autoScrollAt = Date.now();
      _autoScrollTop = target;
      b.scrollTop = target;
      updateJumpEndBtn();
    });
  }

  function onChatScroll() {
    var box = chatScrollBox();
    if (!box) return;
    var d = distanceToBottom(box);
    /* Eco do nosso próprio scroll (ou de um «Ir para o fim» suave): não é o
       utilizador a mexer, por isso não pode desligar o seguimento. */
    var isAuto = (Date.now() < _smoothUntil) ||
      ((Date.now() - _autoScrollAt) < 300 &&
       _autoScrollTop >= 0 && Math.abs(box.scrollTop - _autoScrollTop) < 3);
    if (!isAuto) {
      if (d <= SCROLL_BOTTOM_EPS) box.__follow = true;
      /* Só um gesto recente do utilizador desliga o seguimento. Assim, uma
         imagem que termina de carregar acima (e ajusta o scrollHeight) já não
         é confundida com «o Hubert quis subir». */
      else if ((Date.now() - (box.__userIntentAt || 0)) < 1500) box.__follow = false;
    }
    updateJumpEndBtn();
  }

  function updateJumpEndBtn() {
    var box = chatScrollBox();
    var btn = document.getElementById("btnJumpEnd");
    if (!box || !btn) return;
    /* Só decide se o botão aparece. Nunca mexe no seguimento: essa decisão é
       do gesto do utilizador (onChatScroll) e não da posição no momento.
       Durante o «salto suave» fica escondido para não piscar, e um crescimento
       transitório do balão (seguimento ainda ligado) também não o mostra. */
    if (Date.now() < _smoothUntil) { btn.classList.remove("show"); return; }
    btn.classList.toggle("show", box.__follow === false && distanceToBottom(box) > 8);
  }

  function stickToBottom(force) {
    scrollChatToBottom(!!force);
  }

  /* FAB sempre ~20px acima do composer, alinhado à direita da coluna de chat. */
  function placeJumpEndBtn() {
    var btn = document.getElementById("btnJumpEnd");
    var pane = document.getElementById("paneChat");
    if (!btn || !pane) return;
    var dock = pane.querySelector(".composer-dock");
    var h = dock ? dock.getBoundingClientRect().height : 96;
    btn.style.bottom = Math.round(h + 20) + "px";
    updateJumpEndBtn();
  }

  function renderMessages() {
    var box = document.getElementById("chatScroll");
    var c = state.currentChat;
    if (!box) return;

    var switchedChat = (state.renderedChatId !== (c && c.id));
    state.renderedChatId = c && c.id;
    applyChatStyle();

    if (switchedChat) {
      var _sc = document.getElementById("chatScroll");
      if (_sc) {
        _sc.__follow = true;
        setTimeout(function () { restoreScrollFor(c && c.id); }, 30);
      }
    }

    if (!c || !c.messages || !c.messages.length) {
      _turnCache = {};
      _liveEl = null;
      if (!box.querySelector(".welcome-studio")) {
        box.innerHTML =
          '<div class="welcome-studio anim-in">' +
            '<img class="welcome-logo" src="/icon.svg" alt="Maestro AI">' +
            '<h1>Maestro AI Studio 3.0</h1>' +
            '<p>Comanda os teus projetos, <b>Filmes & Séries (Radarr/Sonarr)</b>, <b>Impressoras 3D (Home Assistant)</b>, <b>Gerador de Imagens/GIFs</b> e <b>Polistrat / SpaceBourne 2</b> num único sítio.</p>' +
            '<div class="starter-grid">' +
              '<div class="starter-card" data-go-mod="media">' +
                '<div class="sc-tag" style="color:var(--rose)">🎬 RADARR & SONARR</div>' +
                '<strong>Sacar Filmes, Séries & Containers</strong>' +
                '<span>Pesquisa filmes/séries com poster e mete a sacar com 1 clique.</span>' +
              '</div>' +
              '<div class="starter-card" data-go-mod="studio">' +
                '<div class="sc-tag" style="color:var(--violet)">🎨 IMAGENS & GIFS</div>' +
                '<strong>Gerador AI, GIFs & Galeria</strong>' +
                '<span>Gera imagens Flux HD, animações GIF89a e gere os teus prints.</span>' +
              '</div>' +
              '<div class="starter-card" data-go-mod="casa">' +
                '<div class="sc-tag" style="color:var(--amber)">🏠 HOME ASSISTANT & 3D</div>' +
                '<strong>Impressoras 3D, Tomadas & Schedulers</strong>' +
                '<span>Centauri Carbon 2, Qidi Plus 4, tomadas e automações agendadas.</span>' +
              '</div>' +
              '<div class="starter-card" data-proj="polistrat" data-q="Inspeciona os scripts C# recentes do Polistrat e resume o estado atual.">' +
                '<div class="sc-tag" style="color:var(--cyan)">🎮 POLISTRAT & MODS</div>' +
                '<strong>Código, Git & Artifacts</strong>' +
                '<span>Scout em Antigravity + delegação cirúrgica com Canvas lateral.</span>' +
              '</div>' +
            '</div>' +
          '</div>';
        box.querySelectorAll("[data-go-mod]").forEach(function(card){
          card.onclick = function(){ setModule(card.getAttribute("data-go-mod")); };
        });
        box.querySelectorAll("[data-q]").forEach(function(card){
          card.onclick = function(){
            var pj = card.getAttribute("data-proj");
            if (pj) document.getElementById("selProject").value = pj;
            var ta = document.getElementById("promptInput");
            ta.value = card.getAttribute("data-q");
            ta.focus();
          };
        });
      }
      placeJumpEndBtn();
      return;
    }

    var welcome = box.querySelector(".welcome-studio");
    if (welcome && welcome.parentNode) welcome.parentNode.removeChild(welcome);

    if (switchedChat) {
      box.innerHTML = "";
      _turnCache = {};
      _liveEl = null;
    }

    var prevTop = box.scrollTop;
    /* Segue apenas se já estava a seguir. A posição a 160 px do fundo deixou
       de contar: estar perto não é o mesmo que querer ser puxado. */
    var wasNearBottom = switchedChat || box.__follow !== false;

    var msgs = c.messages;
    var seen = {};

    // 1) Upsert: cria só turnos novos ou cujo conteúdo mudou.
    msgs.forEach(function(m, idx){
      var sig = msgSig(m, idx);
      seen[idx] = 1;
      var cached = _turnCache[idx];
      if (cached && cached.sig === sig && cached.el.parentNode === box) return;
      /* Substituir um turno já visível não deve repetir a animação de entrada:
         era isso que fazia o bloco inteiro «mexer-se» a cada atualização. */
      var el = buildTurnNode(m, !cached);
      if (cached && cached.el.parentNode === box) box.replaceChild(el, cached.el);
      _turnCache[idx] = { sig: sig, el: el };
    });

    // 2) Remove turnos que já não existem.
    Object.keys(_turnCache).forEach(function(k){
      if (!seen[k]) {
        var node = _turnCache[k].el;
        if (node.parentNode) node.parentNode.removeChild(node);
        delete _turnCache[k];
      }
    });

    // 3) Ordena movendo apenas os nós fora de sítio (nunca recria).
    var prevNode = null;
    msgs.forEach(function(_, idx){
      var el = _turnCache[idx].el;
      var expected = prevNode ? prevNode.nextSibling : box.firstChild;
      if (expected !== el) box.insertBefore(el, expected || null);
      prevNode = el;
    });

    // State belongs to a request id, never to whichever message is last.
    msgs.forEach(function(m, idx){
      if (m.role !== 'user') return;
      var queued = state.messageQueue.find(function(t){ return t.id === m.turn_id; });
      var active = state.activeJob && state.activeJob.status === 'running' && state.activeJob.task_id === m.turn_id;
      var label = queued ? (queued.status === 'interrupted' ? queueReason(queued) : ui('Em fila', 'Queued') + ' · ' + queueReason(queued)) : active ? ui('Em execução', 'Running') : '';
      _turnCache[idx].el.querySelector('.turn-foot').textContent = ui('Tu', 'You') + (label ? ' · ' + label : '');
    });

    // 4) Turno ao vivo (atualizado em sítio).
    var live = (state.activeJob && ["running", "queued"].indexOf(state.activeJob.status) !== -1 && state.activeJob.chat_id === c.id)
      ? state.activeJob : null;
    if (live) {
      if (!_liveEl || _liveEl.parentNode !== box) {
        _liveEl = buildLiveTurn(live);
        placeLiveTurn(box, live);
      } else {
        placeLiveTurn(box, live);
        updateLiveTurn(_liveEl, live);
      }
    } else if (_liveEl) {
      if (_liveEl.parentNode) _liveEl.parentNode.removeChild(_liveEl);
      _liveEl = null;
    }

    if (wasNearBottom) {
      box.__follow = true;
      scrollChatToBottom(false);
    } else {
      box.scrollTop = prevTop;
    }
    placeJumpEndBtn();
  }

  // Botão flutuante «Ir para o fim»: aparece quando o scroll sobe.
  (function initJumpEndButton() {
    var box = document.getElementById("chatScroll");
    var btn = document.getElementById("btnJumpEnd");
    if (!box || !btn) return;
    box.addEventListener("scroll", onChatScroll, { passive: true });
    /* Gesto para cima desliga o seguimento ANTES de o scroll suavizar; assim
       nenhum poll de 950 ms volta a puxar o chat para baixo no meio da leitura. */
    box.addEventListener("wheel", function(e){
      noteChatIntent();
      if (e.deltaY < 0) setChatFollow(false);
      else if (distanceToBottom(box) <= SCROLL_BOTTOM_EPS) setChatFollow(true);
    }, { passive: true });
    /* Clique na barra de scroll / arrasto dentro do chat também é intenção. */
    box.addEventListener("pointerdown", function(e){
      if (e.target === box) noteChatIntent();
    }, { passive: true });
    var touchY = 0;
    box.addEventListener("touchstart", function(e){
      if (e.touches && e.touches[0]) touchY = e.touches[0].clientY;
    }, { passive: true });
    box.addEventListener("touchmove", function(e){
      if (!e.touches || !e.touches[0]) return;
      noteChatIntent();
      var y = e.touches[0].clientY;
      /* Dedo a descer = conteúdo a subir = o Hubert está a ler para trás. */
      if (y > touchY + 2) setChatFollow(false);
      touchY = y;
    }, { passive: true });
    btn.addEventListener("click", function(){
      setChatFollow(true);
      scrollChatToBottom(true, true);
      /* A animação suave pode acabar ligeiramente antes do fundo e disparar
         um scroll a >4 px, que desligaria o seguimento. No fim, fixa-o — mas
         só se o Hubert entretanto não tiver voltado a subir. */
      setTimeout(function(){
        if (box.__follow !== false) {
          setChatFollow(true);
          scrollChatToBottom(true);
        }
        updateJumpEndBtn();
      }, 850);
    });
    document.querySelectorAll(".attach-strip, .queue-strip").forEach(function(st){
      if (st && window.MutationObserver) {
        new MutationObserver(placeJumpEndBtn).observe(st, { childList: true, attributes: true });
      }
    });
    window.addEventListener("resize", placeJumpEndBtn);
    placeJumpEndBtn();
  })();

  /* Delegação: Copiar / Canvas dos blocos de código (sobrevive ao render incremental). */
  (function initCodeActions() {
    var box = document.getElementById("chatScroll");
    if (!box) return;
    box.addEventListener("click", function(e){
      var t = (e.target && e.target.closest) ? e.target.closest("[data-code-copy],[data-code-canvas]") : null;
      if (!t) return;
      var cid = t.getAttribute("data-code-copy") || t.getAttribute("data-code-canvas");
      var item = codeStore[cid];
      if (!item) return;
      if (t.hasAttribute("data-code-copy")) {
        if (navigator.clipboard) {
          navigator.clipboard.writeText(item.code);
          t.textContent = "✓";
          setTimeout(function(){ t.textContent = "📋 Copiar"; }, 1200);
        }
      } else {
        openCanvas("Snippet · " + item.lang, item.code, item.lang);
      }
    });
  })();

  /* Explicit NAS actions: preview exact saved command, then approve once. */
  (function initAuthorizedActions(){
    var style = document.createElement("style");
    style.textContent = [
      '.turn.user [data-code-action],.live-turn [data-code-action],.turn.user .code-card-foot,.live-turn .code-card-foot{display:none}',
      '.code-card-acts{flex-wrap:wrap}',
      '@keyframes action-spin{to{transform:rotate(360deg)}}',
      '@keyframes action-done{50%{transform:scale(1.06);filter:brightness(1.3)}}',
      '.code-authorize[data-action-state=succeeded]{background:linear-gradient(135deg,#15803d,#047857);border-color:#4ade80}',
      '.code-authorize[data-action-state=failed]{border-color:#fb7185}',
      '.code-authorize[data-action-state=starting] .code-authorize-ico,.code-authorize[data-action-state=running] .code-authorize-ico{display:inline-block;animation:action-spin 1s linear infinite}',
      '.code-authorize[data-action-state=succeeded]{animation:action-done .5s ease}',
      '.action-approve[aria-busy=true]::before{content:"◌";display:inline-block;margin-right:8px;animation:action-spin 1s linear infinite}',
      '@media(prefers-reduced-motion:reduce){.code-authorize,.code-authorize .code-authorize-ico,.action-approve::before{animation:none!important}}',
      '.action-dialog{color:var(--text,#eee);background:var(--bg,#111827);border:1px solid #64748b;border-radius:16px;width:min(720px,90vw);max-height:85dvh;padding:20px}',
      '.action-dialog::backdrop{background:#0009}',
      '.action-dialog h2{margin:0 0 10px;font-size:1.05rem}',
      '.action-dialog .action-output:empty,.action-dialog .action-status:empty{display:none}',
      '.action-dialog pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:40dvh;overflow:auto;border:1px solid var(--line);border-radius:10px;padding:10px;background:#050810}',
      '.action-dialog .action-acts{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:14px}',
      '.action-dialog .action-approve{flex:1 1 240px;justify-content:center;min-height:40px;padding:11px 18px;border-radius:10px;border:1px solid color-mix(in srgb,var(--cyan) 78%,#fff);background:linear-gradient(135deg,var(--cyan),#6366f1 62%,var(--violet));color:#fff;font-family:var(--sans);font-size:.86rem;font-weight:700;cursor:pointer;text-shadow:0 1px 2px rgba(5,9,18,.45);box-shadow:inset 0 1px 0 rgba(255,255,255,.28),0 8px 22px color-mix(in srgb,var(--cyan) 28%,transparent);transition:transform .15s ease,filter .15s ease}',
      '.action-dialog .action-approve:hover{transform:translateY(-1px);filter:brightness(1.08)}',
      '.action-dialog .action-approve:active{transform:translateY(0) scale(.98)}',
      '.action-dialog .action-approve:disabled{opacity:.55;cursor:wait;transform:none;filter:none}',
      '.action-dialog .action-cancel,.action-dialog .action-close{min-height:40px;padding:10px 14px;border-radius:9px;border:1px solid var(--line);background:rgba(255,255,255,.05);color:#e2e8f0;font-family:var(--sans);font-size:.8rem;font-weight:600;cursor:pointer}',
      '.action-dialog .action-cancel:hover,.action-dialog .action-close:hover{background:rgba(255,255,255,.09);border-color:var(--line-strong)}',
      '@media (max-width:640px){.action-dialog{width:calc(100vw - 20px);padding:16px}.action-dialog .action-approve{flex-basis:100%}.action-dialog .action-cancel,.action-dialog .action-close{flex:1 1 0}}'
    ].join('');
    document.head.appendChild(style);
    var dialog = document.createElement("dialog");
    dialog.className = "action-dialog";
    dialog.innerHTML = '<h2></h2><p class="action-cwd"></p><pre class="action-command"></pre><p class="action-status" role="status"></p><pre class="action-output"></pre><div class="action-acts"><button class="action-approve"></button><button class="action-cancel"></button><button class="action-close"></button></div>';
    document.body.appendChild(dialog);
    var current = null, records = {}, timers = {};
    try { records = JSON.parse(localStorage.getItem('maestro_action_feedback') || '{}') || {}; } catch(e){}
    function actionKey(chatId, code){ return JSON.stringify([chatId, code]); }
    function active(a){ return a.status === 'starting' || a.status === 'running'; }
    function syncButtons(){
      if (!state.currentChat) return;
      document.querySelectorAll('#chatScroll .turn.assistant:not(.live-turn) [data-code-action]').forEach(function(button){
        var item = codeStore[button.getAttribute('data-code-action')];
        var a = item && records[actionKey(state.currentChat.id, item.code)];
        if (!a || button.dataset.actionState === a.status) return;
        button.dataset.actionState = a.status;
        var labels = {pending:ui('Autorizar e avançar','Authorize and proceed'), starting:ui('A iniciar…','Starting…'), running:ui('A executar…','Running…'), succeeded:ui('Concluído · Ver resultado','Completed · View result'), failed:ui('Falhou · Ver detalhes','Failed · View details'), cancelled:ui('Cancelado','Cancelled'), expired:ui('Autorização expirada','Approval expired')};
        button.querySelector('.code-authorize-ico').textContent = active(a) ? '◌' : a.status === 'succeeded' ? '✓' : a.status === 'failed' ? '!' : '⚡';
        button.querySelector('span:last-child').textContent = labels[a.status] || a.status;
        button.setAttribute('aria-busy', String(active(a)));
      });
    }
    function remember(a){
      records[actionKey(a.chat_id, a.code)] = {id:a.id, chat_id:a.chat_id, code:a.code, status:a.status};
      var keys = Object.keys(records);
      keys.slice(0, Math.max(0, keys.length - 100)).forEach(function(k){ delete records[k]; });
      try { localStorage.setItem('maestro_action_feedback', JSON.stringify(records)); } catch(e){}
      syncButtons();
      clearTimeout(timers[a.id]);
      if (active(a)) timers[a.id] = setTimeout(function(){ refresh(a.id); }, 1500);
    }
    function refresh(id){
      api('/api/actions/status', {action_id:id}).then(function(a){
        remember(a);
        if (current && current.id === a.id && dialog.open) render(a);
      }).catch(function(){ timers[id] = setTimeout(function(){ refresh(id); }, 2500); });
    }
    // Chat polling replaces DOM nodes and code ids; restore feedback from command identity.
    new MutationObserver(syncButtons).observe(document.getElementById('chatScroll'), {childList:true, subtree:true});
    Object.keys(records).forEach(function(k){ refresh(records[k].id); });
    try {
      var lastAction = localStorage.getItem('maestro_last_action');
      if (lastAction && !Object.keys(records).some(function(k){ return records[k].id === lastAction; })) refresh(lastAction);
    } catch(e){}
    var approve = dialog.querySelector('.action-approve');
    var cancel = dialog.querySelector('.action-cancel');
    dialog.querySelector('h2').textContent = ui('Executar ação no NAS', 'Run action on NAS');
    approve.textContent = ui('Autorizar e avançar', 'Authorize and proceed');
    cancel.textContent = ui('Cancelar ação', 'Cancel action');
    dialog.querySelector('.action-close').textContent = ui('Fechar', 'Close');
    function render(a){
      current = a;
      remember(a);
      dialog.querySelector('.action-cwd').textContent = ui('Pasta: ', 'Directory: ') + a.cwd;
      dialog.querySelector('.action-command').textContent = a.code;
      var names = {pending:ui('Aguarda autorização para este comando.', 'Awaiting approval for this command.'), starting:ui('A iniciar…', 'Starting…'), running:ui('A executar…', 'Running…'), succeeded:ui('Concluído', 'Completed'), failed:ui('Falhou', 'Failed'), cancelled:ui('Cancelado', 'Cancelled'), expired:ui('Autorização expirada', 'Approval expired')};
      dialog.querySelector('.action-status').textContent = (names[a.status] || a.status) + (a.exit_code !== undefined ? ' · exit ' + a.exit_code : '') + (a.error ? ' · ' + a.error : '');
      dialog.querySelector('.action-output').textContent = a.output || '';
      approve.hidden = cancel.hidden = a.status !== 'pending';
      approve.disabled = cancel.disabled = false;
      approve.removeAttribute('aria-busy');
      approve.textContent = ui('Autorizar e avançar', 'Authorize and proceed');
      try { localStorage.setItem('maestro_last_action', a.id); } catch(e){}
    }
    function decide(yes){
      if (!current || current.status !== 'pending' || approve.disabled) return;
      approve.disabled = cancel.disabled = true;
      approve.setAttribute('aria-busy', 'true');
      approve.textContent = yes ? ui('A autorizar…', 'Authorizing…') : ui('A cancelar…', 'Cancelling…');
      api('/api/actions/decide', {action_id:current.id, approve:yes}).then(function(a){
        render(a);
        if (active(a) || a.status === 'succeeded' || a.status === 'cancelled') dialog.close();
      }).catch(function(e){
        approve.removeAttribute('aria-busy');
        approve.textContent = ui('Autorizar e avançar', 'Authorize and proceed');
        dialog.querySelector('.action-status').textContent = e.message;
        approve.disabled = cancel.disabled = false;
      });
    }
    approve.onclick = function(){ decide(true); };
    cancel.onclick = function(){ decide(false); };
    dialog.querySelector('.action-close').onclick = function(){ dialog.close(); };
    document.getElementById('chatScroll').addEventListener('click', function(e){
      var button = e.target.closest('[data-code-action]');
      if (!button || !button.closest('.turn.assistant:not(.live-turn)')) return;
      var item = codeStore[button.getAttribute('data-code-action')];
      if (!item || !state.currentChat) return;
      button.disabled = true;
      api('/api/actions/preview', {chat_id:state.currentChat.id, code:item.code}).then(function(a){
        render(a); dialog.showModal();
      }).catch(function(e){ notifyUser('Maestro', e.message); }).finally(function(){ button.disabled = false; });
    });
  })();

  /* ---- RIGHT INSPECTOR ---- */
  function setInspTab(key){
    state.inspTab = key;
    renderInspector();
  }

  document.querySelectorAll(".insp-tab").forEach(function(tab){
    tab.onclick = function(){ setInspTab(tab.getAttribute("data-itab")); };
  });

  /* Tabs a sério: setas, Home/End e um único tab stop (padrão ARIA). */
  (function initInspectorKeys(){
    var list = document.querySelector(".insp-tabs");
    if (!list) return;
    list.addEventListener("keydown", function(e){
      var tabs = Array.prototype.slice.call(list.querySelectorAll(".insp-tab"));
      var idx = tabs.indexOf(document.activeElement);
      if (idx < 0) idx = tabs.map(function(t){ return t.getAttribute("data-itab"); }).indexOf(state.inspTab);
      var next = idx;
      if (e.key === "ArrowRight" || e.key === "ArrowDown") next = (idx + 1) % tabs.length;
      else if (e.key === "ArrowLeft" || e.key === "ArrowUp") next = (idx - 1 + tabs.length) % tabs.length;
      else if (e.key === "Home") next = 0;
      else if (e.key === "End") next = tabs.length - 1;
      else return;
      e.preventDefault();
      if (next < 0) next = 0;
      tabs[next].focus();
      setInspTab(tabs[next].getAttribute("data-itab"));
    });
  })();

  /* ---- QUOTAS: UMA SÓ LINGUAGEM (BATERIA QUE RESTA) ---- */
  function remainingPct(used) {
    var n = Number(used);
    if (used === null || used === undefined || used === "" || !isFinite(n)) return null;
    return Math.max(0, Math.min(100, 100 - n));
  }
  function quotaCls(rem, limited) {
    if (limited || rem === null || rem <= 0) return "bad";
    if (rem < 25) return "bad";
    if (rem < 50) return "warn";
    return "ok";
  }
  function battHtml(used, limited) {
    var rem = remainingPct(used);
    if (rem === null) return '<span class="quota-batt"><span class="batt-ico">🔋</span>sem dados</span>';
    var cls = quotaCls(rem, limited);
    var body = rem <= 0
      ? "resta <b>0%</b>"
      : (limited ? "<b>limite atingido</b>" : "resta <b>" + Math.round(rem) + "%</b>");
    return '<span class="quota-batt ' + cls + '"><span class="batt-ico">🔋</span>' + body + '</span>';
  }
  function restText(used) {
    var rem = remainingPct(used);
    return rem === null ? "?" : String(Math.round(rem));
  }
  function clockText(ts) {
    if (!ts) return "—";
    try { return new Date(ts).toLocaleTimeString("pt-PT", { hour: "2-digit", minute: "2-digit", second: "2-digit" }); }
    catch (e) { return "—"; }
  }
  function fleetQuotaLine(key) {
    var st = state.status || {};
    var a = st.antigravity || {}, c = st.claude || {}, x = st.codex || {}, d = st.deepseek || {};
    if (key === "agy") return "Resta " + restText(a.gemini_used_pct) + "% · reset " + (a.gemini_reset_fmt || "livre");
    if (key === "claude") return "Resta 5h " + restText(c.five_hour_pct) + "% · 7d " + restText(c.seven_day_pct) + "% · " + (c.seven_day_reset_fmt || "—");
    if (key === "codex") return "Resta 5h " + restText(x.five_hour_est_pct) + "% · 7d " + restText(x.seven_day_pct) + "% · " + (x.five_hour_reset_fmt || "—");
    if (key === "deepseek") return "Saldo " + (d.balance_text || "—");
    return "";
  }

  function renderInspector() {
    var box = document.getElementById("inspBody");
    if (!box) return;
    document.querySelectorAll(".insp-tab").forEach(function(tab){
      var selected = tab.getAttribute("data-itab") === state.inspTab;
      tab.classList.toggle("active", selected);
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
      if (selected) box.setAttribute("aria-labelledby", tab.id);
    });
    if (state.inspTab === "quotas") {
      var refreshed = state.statusAt ? clockText(state.statusAt) : "—";
      var staleNote = (state.statusError && state.status)
        ? '<div class="quota-note">⚠️ Sem resposta do servidor. A mostrar a última leitura válida das ' + esc(refreshed) + '.</div>'
        : '';

      function bindQuotaRefresh(label) {
        var rq = document.getElementById("btnRefreshQuotas");
        if (!rq) return;
        rq.disabled = !!state.quotaRefreshing;
        rq.setAttribute("aria-busy", state.quotaRefreshing ? "true" : "false");
        rq.textContent = state.quotaRefreshing
          ? "⏳ A ler APIs de quota…"
          : (label || "🔄 Atualizar quotas agora");
        rq.onclick = function(){
          if (state.quotaRefreshing) return;
          state.quotaRefreshing = true;
          renderInspector();
          loadStatusAndPulse({ force: true }).then(function(){
            state.quotaRefreshing = false;
            renderInspector();
          });
        };
      }

      if (!state.status) {
        box.innerHTML =
          '<div class="stat-card">' +
            '<div class="stat-head"><span>' + (state.statusError ? "⚠️ Quotas indisponíveis" : "⏳ A ler quotas…") + '</span></div>' +
            '<div class="stat-sub">' + (state.statusError
              ? "O servidor não respondeu a /api/status. Não mostro 0% para não dar a ideia errada de que está tudo cheio."
              : "A consultar as APIs OAuth e o CLI. As barras aparecem dentro de instantes.") + '</div>' +
            '<button type="button" class="insp-refresh-btn" id="btnRefreshQuotas">🔄 Tentar de novo</button>' +
          '</div>';
        bindQuotaRefresh("🔄 Tentar de novo");
        return;
      }

      var a = state.status.antigravity || {}, c = state.status.claude || {}, x = state.status.codex || {}, d = state.status.deepseek || {};
      var am = state.status.active_maestro || {};

      /* Gemini / Antigravity */
      var gUsedRaw = a.gemini_used_pct;
      var gUsed = (gUsedRaw == null || !isFinite(Number(gUsedRaw))) ? null : Math.round(Number(gUsedRaw) * 10) / 10;
      var gRem = remainingPct(gUsedRaw);
      var gLimited = !!(a.limited || (gRem !== null && gRem <= 0));
      var gCls = quotaCls(gRem, gLimited);

      /* Claude Max */
      var c5raw = c.five_hour_pct, c7raw = c.seven_day_pct, cFraw = c.fable_pct;
      var cRem = remainingPct(c7raw);
      var cLimited = !!(c.limited || (cRem !== null && cRem <= 0));
      var cCls = quotaCls(cRem, cLimited);
      var cResetFmt = c.seven_day_reset_fmt || c.seven_day_reset || "Quinta 20:00";
      var pace = Math.round(c.pace_target_pct || 0);

      /* Codex */
      var x5raw = x.five_hour_est_pct, x7raw = x.seven_day_pct;
      var xRem = remainingPct(x5raw);
      var xLimited = !!(x.limit_reached || (xRem !== null && xRem <= 0));
      var xCls = quotaCls(xRem, xLimited);
      var xResetFmt = x.five_hour_reset_fmt || "Janela 5h";

      var amName = String(am.name || "Antigravity").replace(/\s*\(Maestro Substituto[^)]*\)/, "").replace(/\s*\(Maestro Principal\)/, "");
      var chatEng = document.getElementById("selEngine");
      var curEngKey = (state.currentChat && state.currentChat.engine) || (chatEng ? chatEng.value : "auto");
      var engLabels = { auto: "⚡ Auto (orquestra)", agy: "🧠 Gemini / Antigravity", sonnet: "🎭 Claude Sonnet", "claude-sonnet": "🎭 Claude Sonnet", opus: "👑 Claude Opus", "claude-opus": "👑 Claude Opus", codex: "⚙️ Codex", deepseek: "🔮 DeepSeek V3.2" };
      var chatEngTxt = engLabels[curEngKey] || (chatEng && chatEng.selectedOptions && chatEng.selectedOptions[0] ? chatEng.selectedOptions[0].textContent.trim() : "Auto");
      /* Último plano conhecido: turno ao vivo > última resposta da conversa.
         É a única forma de o painel não contradizer o cartão "Motor · …". */
      var liveTurn = !!(state.activeJob && state.currentChat && state.activeJob.chat_id === state.currentChat.id && state.activeJob.status === "running");
      var planNow = liveTurn ? state.activeJob.plan : null;
      if (!planNow && state.currentChat && state.currentChat.messages) {
        for (var _pi = state.currentChat.messages.length - 1; _pi >= 0; _pi--) {
          var _pm = state.currentChat.messages[_pi];
          if (_pm && _pm.role === "assistant" && _pm.plan) { planNow = _pm.plan; break; }
        }
      }
      var motorKey = planNow ? (planNow.exec_engine || "") : "";
      var motorTxt = planNow ? (planNow.exec_engine_name || engLabels[motorKey] || motorKey) : "";
      var motorOrigin = planNow ? String(planNow.engine_origin || "") : "";
      var amRoleTxt = motorKey && motorKey === am.engine
        ? (liveTurn ? "Este modelo está a executar o pedido." : "Este modelo executou o último turno.")
        : "Disponível na hierarquia de substituição; sem participação confirmada neste turno.";

      box.innerHTML =
        '<div class="quota-legenda">🔋 <span>Uma só leitura: as barras enchem com a <b>quota que resta</b> (100% = cheio). <span class="quota-updated">Última leitura: ' + esc(refreshed) + '</span></span></div>' +
        staleNote +
        '<div class="stat-card">' +
          '<div class="stat-head"><span>👑 Maestro disponível</span><span class="quota-chip ' + (am.is_substitute ? 'warn' : 'ok') + '">' + (am.is_substitute ? "SUBSTITUTO" : "PRINCIPAL") + '</span></div>' +
          '<div class="maestro-name">' + esc(amName) + '</div>' +
          '<div class="stat-sub">' + esc(am.reason || "Substituto automático configurado.") + '</div>' +
          '<div class="stat-sub" style="opacity:.75">' + esc(amRoleTxt) + '</div>' +
          '<div class="stat-sub">Nesta conversa: <b>' + esc(chatEngTxt) + (curEngKey === "auto" && motorTxt ? " → " + esc(motorTxt) : "") + '</b></div>' +
          (motorTxt ? '<div class="stat-sub">' + (liveTurn ? 'A executar agora: ' : 'Motor do último turno: ') + '<b>' + esc(motorTxt) + '</b>' + (motorOrigin === "task_route" ? ' <span style="opacity:.75">(seleção automática por regras)</span>' : motorOrigin === "failover" ? ' <span style="opacity:.75">(substituição automática)</span>' : '') + '</div>' : '') +
        '</div>' +
        '<div class="stat-card' + (gLimited ? ' quota-exhausted' : '') + '">' +
          '<div class="stat-head"><span>♊ Gemini / Antigravity</span>' + battHtml(gUsedRaw, a.limited) + '</div>' +
          '<div class="meter-track battery"><div class="meter-fill ' + gCls + '" style="width:' + (gRem == null ? 0 : gRem) + '%"></div></div>' +
          '<div class="stat-sub">' +
            (gUsed == null ? "sem leitura de quota" : '<span class="quota-used">' + gUsed + '% usado</span>' + (gLimited ? ' · <span class="quota-chip bad">ESGOTADO</span>' : '')) +
            ' · Reset real: <b>' + esc(a.gemini_reset_fmt || "Livre") + '</b>' +
            '<br>3P OAuth (projeto, não fiável): 5h resta <b>' + restText(a.models_3p_5h_used_pct) + '%</b> · 7d resta <b>' + restText(a.models_3p_7d_used_pct) + '%</b>' +
          '</div>' +
          (a.quota_note ? '<div class="quota-note">⚠️ ' + esc(a.quota_note) + '</div>' : '') +
          (a.quota_source ? '<div class="quota-src">fonte: ' + esc(a.quota_source) + '</div>' : '') +
        '</div>' +
        '<div class="stat-card' + (cLimited ? ' quota-exhausted' : '') + '">' +
          '<div class="stat-head"><span>🎭 Claude Max</span>' + battHtml(c7raw, c.limited) + '</div>' +
          '<div class="meter-track battery"><div class="meter-fill ' + cCls + '" style="width:' + (cRem == null ? 0 : cRem) + '%"></div></div>' +
          '<div class="stat-sub">5h: resta <b>' + restText(c5raw) + '%</b> · 7d: resta <b>' + restText(c7raw) + '%</b> · Fable: resta <b>' + restText(cFraw) + '%</b>' +
            '<br>Usado 7d: <b>' + (c7raw == null ? "?" : Math.round(c7raw) + '%') + '</b> (ideal ≤' + pace + '%) · Reset 7d: <b>' + esc(cResetFmt) + '</b>' +
            (cLimited ? ' · <span class="quota-chip bad">ESGOTADO</span>' : '') +
          '</div>' +
        '</div>' +
        '<div class="stat-card' + (xLimited ? ' quota-exhausted' : '') + '">' +
          '<div class="stat-head"><span>⚙️ Codex Plus (5h)</span>' + battHtml(x5raw, x.limit_reached) + '</div>' +
          '<div class="meter-track battery"><div class="meter-fill ' + xCls + '" style="width:' + (xRem == null ? 0 : xRem) + '%"></div></div>' +
          '<div class="stat-sub">5h: resta <b>' + restText(x5raw) + '%</b> · 7d: resta <b>' + restText(x7raw) + '%</b>' +
            '<br>Usado 5h: <b>' + (x5raw == null ? "?" : Math.round(x5raw) + '%') + '</b> · Reset 5h: <b>' + esc(xResetFmt) + '</b>' +
            (xLimited ? ' · <span class="quota-chip bad">ESGOTADO</span>' : '') +
          '</div>' +
        '</div>' +
        '<div class="stat-card">' +
          '<div class="stat-head"><span>🔮 DeepSeek API</span><span style="color:var(--green)">' + esc(d.balance_text || "Sem leitura") + '</span></div>' +
          '<div class="stat-sub">Saldo em dólares — a API não expõe percentagem de quota, por isso não há barra inventada.</div>' +
        '</div>' +
        '<div class="quota-legenda">🔋 100% = cheio · 0% = esgotado. A barra de cada modelo mostra a janela que aperta primeiro (7d no Claude, 5h no Codex).</div>' +
        '<button type="button" class="insp-refresh-btn" id="btnRefreshQuotas">🔄 Atualizar quotas agora</button>';
      bindQuotaRefresh();
      return;
    }

    if (state.inspTab === "pulso") {
      var items = (state.pulse || {}).items || [];
      var pulseErrors = (state.pulse || {}).errors || [];
      if (!items.length) {
        box.innerHTML = '<div class="stat-card">✅ Sem alertas no Pulso!</div>';
        return;
      }
      var urgent = items.filter(function(it){ return ["alert","err","warn"].indexOf(it.level) !== -1; });

      function buildPulsePrompt(sel, analyze) {
        var lines = sel.map(function(it, idx){
          return (idx + 1) + ". [" + String(it.level || "info").toUpperCase() + "] " + it.app + " — " + it.text;
        });
        if (analyze) {
          return "🧠 Analisa o Pulso do Pavilion-NAS e dá-me um plano priorizado. Para cada alerta diz: causa provável, impacto, correção concreta e se é seguro aplicar já.\n\n" + lines.join("\n");
        }
        return "⚡ Resolve os seguintes alertas do Pulso no Pavilion-NAS. Para cada um: diagnostica a causa, aplica a correção no sistema (Docker/Radarr/Sonarr/HA/scripts), valida o resultado e diz-me o que mudou. Não peças confirmação para passos reversíveis.\n\n" + lines.join("\n");
      }
      function launchPulse(sel, analyze) {
        if (!sel.length) return;
        setModule("chat");
        var ta = document.getElementById("promptInput");
        ta.value = buildPulsePrompt(sel, analyze);
        ta.focus();
        handleComposerSubmit();
      }
      box.innerHTML =
        '<div class="pulse-cmd">' +
          '<div class="pulse-cmd-info"><b>' + items.length + '</b> alertas · <span class="pulse-urgent-n">' + urgent.length + ' urgentes</span></div>' +
          '<div class="pulse-cmd-acts">' +
            '<button type="button" class="pulse-act urgent" data-pact="urgent"' + (urgent.length ? '' : ' disabled') + '>⚡ Resolver urgentes (' + urgent.length + ')</button>' +
            '<button type="button" class="pulse-act" data-pact="all">📋 Resolver todos (' + items.length + ')</button>' +
            '<button type="button" class="pulse-act analyze" data-pact="analyze">🧠 Analisar Pulso</button>' +
          '</div>' +
          '<div class="pulse-cmd-hint">Marca as caixas para resolver só os que escolheres.</div>' +
        '</div>' +
        (pulseErrors.length ? '<div class="quota-note">⚠️ Fontes com erro: ' + esc(pulseErrors.join(" · ")) + '</div>' : '') +
        items.map(function(it, idx){
          var lvl = String(it.level || "info");
          var badge = lvl === "alert" ? "err" : (lvl === "live" ? "ok" : lvl);
          return (
            '<div class="pulse-card ' + esc(badge) + '">' +
              '<div class="pulse-card-head">' +
                '<label class="pulse-sel"><input type="checkbox" data-psel="' + idx + '"><span>[' + esc(lvl.toUpperCase()) + ']</span></label>' +
                '<strong style="font-size:0.76rem">' + esc(it.app) + '</strong>' +
                '<button type="button" class="round-tool-btn magic" data-pidx="' + idx + '">⚡ Resolver</button>' +
              '</div>' +
              '<div style="font-size:0.75rem;color:#e2e8f0">' + esc(it.text) + '</div>' +
            '</div>'
          );
        }).join("");
      function selectedPulse() {
        return Array.prototype.slice.call(box.querySelectorAll("[data-psel]:checked")).map(function(cb){
          return items[Number(cb.getAttribute("data-psel"))];
        }).filter(Boolean);
      }
      box.querySelectorAll("[data-pact]").forEach(function(b){
        b.onclick = function(){
          var act = b.getAttribute("data-pact");
          if (act === "urgent") return launchPulse(urgent);
          if (act === "analyze") return launchPulse(items, true);
          var sel = selectedPulse();
          launchPulse(sel.length ? sel : items);
        };
      });
      box.querySelectorAll("[data-pidx]").forEach(function(b){
        b.onclick = function(){
          var it = items[Number(b.getAttribute("data-pidx"))];
          if (it) launchPulse([it]);
        };
      });
      return;
    }

    if (state.inspTab === "git") {
      var info = state.projectInfo;
      if (!info) {
        box.innerHTML = '<div class="stat-card">A carregar ficheiros…</div>';
        loadProjectInspector(document.getElementById("selProject").value);
        return;
      }
      var rec = info.recent_files || [];
      box.innerHTML =
        '<div class="stat-card">' +
          '<div class="stat-head"><span>' + esc((info.project||{}).name || "Projeto") + '</span><span style="color:var(--cyan);font-family:var(--mono);font-size:0.7rem">🌿 ' + esc(info.git_branch || "—") + '</span></div>' +
          '<div class="stat-sub" style="font-family:var(--mono)">' + esc((info.project||{}).cwd || "") + '</div>' +
        '</div>' +
        rec.slice(0, 14).map(function(f){
          return '<div class="file-row" data-fpath="' + esc(f.path) + '"><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + esc(f.rel) + '</span><span>🪟</span></div>';
        }).join("");
      box.querySelectorAll("[data-fpath]").forEach(function(row){
        row.onclick = function(){
          var fp = row.getAttribute("data-fpath");
          api("/api/project/file?path=" + encodeURIComponent(fp)).then(function(res){
            if (res.content !== undefined) openCanvas(res.name || fp, res.content, "code");
          });
        };
      });
      return;
    }

    if (state.inspTab === "fleet") {
      if (!state.status) {
        box.innerHTML = '<div class="stat-card">' + (state.statusError
          ? "⚠️ Frota indisponível: o servidor não respondeu. Os agentes não podem ser inventados."
          : "⏳ A ler a frota…") + '</div>';
        return;
      }
      var st = state.status || {};
      var a = st.antigravity || {}, c = st.claude || {}, x = st.codex || {}, d = st.deepseek || {};
      var fs = st.fleet_summary || {};
      var fleet = fs.fleet || [];
      var engMap = { agy: "agy", claude: "sonnet", codex: "codex", deepseek: "deepseek" };
      var c5 = Math.round(c.five_hour_pct || 0);
      var c7 = Math.round(c.seven_day_pct || 0);
      var x5 = Math.round(x.five_hour_est_pct || 0);
      var x7 = Math.round(x.seven_day_pct || 0);
      if (fleet.length) {
        var cards = fleet.map(function(f){
          var engine = engMap[f.key] || "auto";
          var act = document.getElementById("selEngine").value === engine;
          var tasks = f.active_tasks || [];
          var taskLine = tasks.length
            ? tasks.map(function(task){ return '<div class="fleet-task">▶ ' + esc(task.agent_label || ui("Em execução", "Running")) + ' · ' + esc(statusText(task.current_step || ui("a trabalhar", "working"))) + '</div>'; }).join('')
            : '';
          return '<div class="stat-card fleet-card" style="cursor:pointer;border-color:' + (act ? 'var(--cyan)' : 'var(--line)') + '" data-eng="' + engine + '" data-fleet-key="' + esc(f.key) + '" title="Ver o que este agente está a fazer">' +
            '<div class="stat-head"><span>' + esc(f.icon) + ' ' + esc(f.name) + '</span>' +
              '<span class="quota-chip ' + (f.available ? 'ok' : 'bad') + '">' + (f.available ? "LIVRE" : "ESGOTADO") + '</span></div>' +
            '<div class="stat-sub">' + esc(f.role || "") + '</div>' +
            '<div class="stat-sub fleet-quota">' + esc(fleetQuotaLine(f.key) || f.quota_label || "") + '</div>' +
            (tasks.length ? '<div class="stat-sub" style="color:var(--amber)">' + tasks.length + ' tarefa(s) ativa(s)</div>' : '') +
            taskLine +
          '</div>';
        }).join("");
        var rules =
          '<details class="rules-details" open>' +
            '<summary>📋 Regras & Modelos atuais (' + (fs.available_count || 0) + '/' + fleet.length + ' agentes livres)</summary>' +
            '<div class="rich-table-wrap"><table class="rich-table">' +
              '<thead><tr><th>Matéria</th><th>Modelo / Empresa</th><th>Agente</th><th>Autonomia</th><th>Quota restante / Reset</th></tr></thead><tbody>' +
              '<tr><td data-l="Matéria">Arquitetura & C# Unity</td><td data-l="Modelo">Claude Opus 5.5 · Anthropic</td><td data-l="Agente">Claude Max</td><td data-l="Autonomia">Total (1.0x)</td><td data-l="Quota">Resta ' + restText(c.seven_day_pct) + '% 7d · ' + esc(c.seven_day_reset_fmt || "—") + '</td></tr>' +
              '<tr><td data-l="Matéria">UI/Web, refactor, copy</td><td data-l="Modelo">Claude Sonnet 5.5 · Anthropic</td><td data-l="Agente">Claude Max</td><td data-l="Autonomia">Total (0.2x)</td><td data-l="Quota">Resta ' + restText(c.five_hour_pct) + '% 5h · ' + restText(c.seven_day_pct) + '% 7d</td></tr>' +
              '<tr><td data-l="Matéria">Visão, Scout & web search</td><td data-l="Modelo">Gemini 4 Argon · Google</td><td data-l="Agente">Antigravity</td><td data-l="Autonomia">Total (never ask)</td><td data-l="Quota">Resta ' + restText(a.gemini_used_pct) + '% · ' + esc(a.gemini_reset_fmt || "—") + '</td></tr>' +
              '<tr><td data-l="Matéria">Debug, Docker, modding</td><td data-l="Modelo">gpt-6.1-sol · OpenAI</td><td data-l="Agente">Codex CLI</td><td data-l="Autonomia">Total</td><td data-l="Quota">Resta ' + restText(x.five_hour_est_pct) + '% 5h · ' + restText(x.seven_day_pct) + '% 7d</td></tr>' +
              '<tr><td data-l="Matéria">Scripts, batch, texto</td><td data-l="Modelo">DeepSeek V3.2 · DeepSeek</td><td data-l="Agente">DeepSeek CLI</td><td data-l="Autonomia">Total (headless)</td><td data-l="Quota">' + esc(d.balance_text || "—") + ' API</td></tr>' +
              '<tr><td data-l="Matéria">Orquestração / failover</td><td data-l="Modelo">Auto</td><td data-l="Agente">Maestro</td><td data-l="Autonomia">Total</td><td data-l="Quota">' + esc((st.active_maestro || {}).name || "—") + '</td></tr>' +
            '</tbody></table></div>' +
            '<div class="pulse-cmd-hint">Regras vivas do classificador: falha de quota → Codex → Claude Sonnet → DeepSeek. Quotas lidas da API OAuth + 429 real do CLI.</div>' +
          '</details>';
        var freeNames = fleet.filter(function(x){ return x.available; }).map(function(x){ return x.name; });
        var poolNote = freeNames.length <= 2
          ? '<div class="fleet-task" style="color:var(--amber)">⚠️ Só ' + freeNames.length + ' modelo(s) livre(s): ' + esc(freeNames.join(" + ") || "nenhum") + ' — o Maestro alterna entre eles e guarda o último para o fim.</div>'
          : '';
        box.innerHTML = '<div class="fleet-summary">🤖 ' + fleet.length + ' agentes · ' + (fs.available_count || 0) + ' livres agora</div>' + poolNote + cards + rules;
        box.querySelectorAll("[data-eng]").forEach(function(card){
          card.onclick = function(){ document.getElementById("selEngine").value = card.getAttribute("data-eng"); renderInspector(); };
        });
      } else {
        box.innerHTML = '<div class="stat-card">A carregar frota…</div>';
      }
    }
  }

  function loadStatusAndPulse(opts) {
    opts = opts || {};
    if (state.statusLoading && !opts.force) return Promise.resolve();
    state.statusLoading = true;

    /* Quotas e Pulso falham em separado: uma API em baixo não pode apagar a
       outra, e uma falha de rede nunca deve transformar-se em "0% usado". */
    var pStatus = api("/api/status").then(function(v){
      if (v && typeof v === "object") {
        state.status = v;
        state.statusAt = Date.now();
        state.statusError = false;
      }
    }, function(){
      state.statusError = true;
    });

    var pPulse = api("/api/pulse").then(function(v){
      state.pulse = v || { items: [] };
      state.pulseError = false;
      var items = (state.pulse.items || []);
      var errs = items.filter(function(i){ return i.level === "err" || i.level === "warn"; });
      var badge = document.getElementById("headerPulseBadge");
      if (badge) badge.textContent = errs.length ? "(" + errs.length + ")" : "";
      if (state.lastPulseErrCount !== null && errs.length > state.lastPulseErrCount && errs[0]) {
        notifyUser("⚠️ Alerta no Pulso · " + errs[0].app, errs[0].text);
      }
      state.lastPulseErrCount = errs.length;
    }, function(){
      state.pulseError = true;
    });

    return Promise.all([pStatus, pPulse]).then(function(){
      state.statusLoading = false;
      var box = document.getElementById("inspBody");
      var top = box ? box.scrollTop : 0;
      try {
        if (!document.hidden) renderInspector();
      } catch (err) {
        if (window.console && console.error) console.error("renderInspector", err);
      }
      if (box) box.scrollTop = top;
      return true;
    });
  }

  function loadProjectInspector(projKey) {
    api("/api/project/inspect?project=" + encodeURIComponent(projKey || "nas")).then(function(d){
      state.projectInfo = d;
      if (state.inspTab === "git") renderInspector();
    });
  }
  document.getElementById("selProject").onchange = function(e){ loadProjectInspector(e.target.value); };

  /* ---- CHAT LIST & CRUD ---- */
  function chatRelativeTime(ts) {
    var t = Number(ts) || 0;
    if (!t) return "";
    var diff = Math.max(0, Math.floor(Date.now() / 1000 - t));
    if (diff < 60) return ui("agora", "now");
    if (diff < 3600) return Math.floor(diff / 60) + " min";
    if (diff < 86400) return Math.floor(diff / 3600) + " h";
    if (diff < 172800) return ui("ontem", "yesterday");
    if (diff < 604800) return Math.floor(diff / 86400) + ui(" dias", " days");
    return new Date(t * 1000).toLocaleDateString();
  }

  var SCROLL_KEY = "maestro_scroll_v1";
  function scrollStore() {
    try { return JSON.parse(localStorage.getItem(SCROLL_KEY) || "{}") || {}; } catch (e) { return {}; }
  }
  function saveScrollNow() {
    var box = document.getElementById("chatScroll");
    if (!box || !state.currentChat) return;
    var m = scrollStore();
    var atBottom = distanceToBottom(box) <= SCROLL_BOTTOM_EPS;
    if (atBottom) delete m[state.currentChat.id];
    else m[state.currentChat.id] = { top: Math.round(box.scrollTop), h: box.scrollHeight };
    try { localStorage.setItem(SCROLL_KEY, JSON.stringify(m)); } catch (e) {}
  }
  var _scrollTimer = null;
  function restoreScrollFor(chatId) {
    var box = document.getElementById("chatScroll");
    if (!box) return;
    var v = chatId ? scrollStore()[chatId] : null;
    window.requestAnimationFrame(function () {
      if (v && typeof v.top === "number") {
        box.__follow = false;
        var maxTop = Math.max(0, box.scrollHeight - box.clientHeight);
        box.scrollTop = Math.min(v.top, maxTop);
      } else {
        box.__follow = true;
        scrollChatToBottom(true);
      }
    });
  }

  function chatDayDiff(ts) {
    var d = new Date((ts || 0) * 1000), t = new Date();
    var a = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    var b = new Date(t.getFullYear(), t.getMonth(), t.getDate());
    return Math.round((b - a) / 86400000);
  }
  function chatBucket(c) {
    var n = chatDayDiff(c.updated_at);
    if (n <= 0) return { key: "today", label: ui("Hoje", "Today") };
    if (n === 1) return { key: "yday", label: ui("Ontem", "Yesterday") };
    if (n <= 7) return { key: "week", label: ui("Últimos 7 dias", "Last 7 days") };
    if (n <= 30) return { key: "month", label: ui("Este mês", "This month") };
    return { key: "older", label: ui("Mais antigas", "Older") };
  }
  function chatGroupsCollapsed() {
    try { return JSON.parse(localStorage.getItem("maestro_chat_groups") || "[]"); } catch (e) { return []; }
  }
  function toggleChatGroup(key) {
    var cur = chatGroupsCollapsed();
    var i = cur.indexOf(key);
    if (i === -1) cur.push(key); else cur.splice(i, 1);
    try { localStorage.setItem("maestro_chat_groups", JSON.stringify(cur)); } catch (e) {}
    renderChatList();
  }
  function makeChatRow(c, fmap) {
    var fld = fmap[c.project] || { icon: "📁", color: "#38bdf8", name: c.project };
    var row = document.createElement("div");
    row.className = "chat-row" + (state.currentChat && state.currentChat.id === c.id ? " active" : "");
    var info = document.createElement("div");
    info.className = "chat-row-info";
    var when = chatRelativeTime(c.updated_at);
    var subProj = (state.filterProj === "all")
      ? '<span title="' + esc(fld.name) + '" style="color:' + esc(fld.color) + '">' + esc(fld.icon) + '</span><span>·</span>'
      : '';
    var activity = c.activity || { label: "Nova conversa", text: "À espera do primeiro pedido" };
    var label = statusText(activity.label);
    var detailText = routeText(statusText(activity.detail));
    var preview = label + " · " + activity.text;
    var detail = activity.detail ? "\n" + detailText : "";
    info.innerHTML =
      '<div class="chat-row-title">' + (c.pinned ? "📌 " : "") + esc(c.title) + '</div>' +
      '<div class="chat-row-preview ' + esc(activity.state || 'idle') + '" title="' + esc(preview + detail) + '"><b>' + esc(label) + '</b> · ' + esc(activity.text === "À espera do primeiro pedido" || activity.text === "A processar anexos" ? statusText(activity.text) : activity.text) + '</div>' +
      (activity.state === 'running' && activity.detail ? '<div class="chat-row-step" title="' + esc(detailText) + '">' + esc(detailText) + '</div>' : '') +
      '<div class="chat-row-sub">' + subProj + '<span>' + c.msg_count + ' msg</span>' +
      (when ? '<span>· ' + esc(when) + '</span>' : '') + '</div>';
    info.onclick = function(){ setModule("chat"); openChat(c.id); closeMobileDrawers(); };

    var acts = document.createElement("div");
    acts.className = "chat-row-actions";
    var moveIcon = document.createElement("span");
    moveIcon.textContent = "📁";
    moveIcon.style.cssText = "position:relative;display:inline-flex;align-items:center;justify-content:center;width:24px;height:24px";
    var move = document.createElement("select");
    move.setAttribute("aria-label", ui("Organizar conversa", "Organize conversation"));
    move.title = ui("Mover para uma pasta ou ativar organização automática", "Move to a folder or enable automatic organization");
    move.style.cssText = "position:absolute;inset:0;width:24px;height:24px;opacity:0;cursor:pointer";
    var placeholder = document.createElement("option");
    placeholder.textContent = "📁"; placeholder.value = ""; placeholder.disabled = true; placeholder.selected = true;
    move.appendChild(placeholder);
    var automatic = document.createElement("option");
    automatic.value = "auto"; automatic.textContent = ui("Automático (45 min)", "Automatic (45 min)");
    move.appendChild(automatic);
    state.folders.forEach(function(folder){
      var option = document.createElement("option");
      option.value = folder.id; option.textContent = folder.icon + " " + folder.name;
      move.appendChild(option);
    });
    move.onclick = function(ev){ ev.stopPropagation(); };
    move.onchange = function(){
      api("/api/chat/folder", {id:c.id, folder:move.value}).then(refreshChats);
    };
    var bPin = document.createElement("button"); bPin.textContent = "📌";
    bPin.onclick = function(ev){ ev.stopPropagation(); api("/api/chat/pin", {id: c.id}).then(refreshChats); };
    var bDel = document.createElement("button"); bDel.textContent = "🗑";
    bDel.onclick = function(ev){
      ev.stopPropagation();
      if (confirm(ui("Apagar esta conversa?", "Delete this conversation?"))) {
        api("/api/chat/delete", {id: c.id}).then(function(){
          if (state.currentChat && state.currentChat.id === c.id) state.currentChat = null;
          refreshChats(); renderMessages();
        });
      }
    };
    moveIcon.appendChild(move);
    acts.appendChild(moveIcon); acts.appendChild(bPin); acts.appendChild(bDel);
    row.appendChild(info); row.appendChild(acts);
    return row;
  }
  function renderChatList() {
    var box = document.getElementById("chatList");
    var q = state.search.toLowerCase();
    var fmap = {};
    state.folders.forEach(function(f){ fmap[f.id] = f; });

    var filtered = state.chats.filter(function(c){
      if (state.filterProj !== "all" && c.project !== state.filterProj) return false;
      if (q && (c.title + " " + ((c.activity || {}).text || "")).toLowerCase().indexOf(q) === -1) return false;
      return true;
    });
    box.innerHTML = "";

    /* à procura: lista lisa (sem grupos) para os resultados ficarem juntos */
    if (q) {
      filtered.forEach(function(c){ box.appendChild(makeChatRow(c, fmap)); });
      return;
    }

    var collapsed = chatGroupsCollapsed();
    var order = ["pinned", "today", "yday", "week", "month", "older"];
    var byKey = {};
    filtered.forEach(function(c){
      var b = c.pinned ? { key: "pinned", label: ui("📌 Fixadas", "📌 Pinned") } : chatBucket(c);
      if (!byKey[b.key]) byKey[b.key] = { key: b.key, label: b.label, items: [] };
      byKey[b.key].items.push(c);
    });

    order.forEach(function(key){
      var g = byKey[key];
      if (!g || !g.items.length) return;
      var isCollapsed = collapsed.indexOf(key) !== -1;
      var head = document.createElement("div");
      head.className = "chat-group-head";
      head.innerHTML = '<span class="chev">' + (isCollapsed ? "▸" : "▾") + '</span>' +
        '<span>' + esc(g.label) + '</span><span class="n">' + g.items.length + '</span>';
      head.onclick = function(){ toggleChatGroup(key); };
      box.appendChild(head);
      var wrap = document.createElement("div");
      wrap.className = "chat-group" + (isCollapsed ? " collapsed" : "");
      g.items.forEach(function(c){ wrap.appendChild(makeChatRow(c, fmap)); });
      box.appendChild(wrap);
    });
  }

  function refreshChats() {
    return api("/api/chats").then(function(d){
      state.chats = d.chats || [];
      if (d.folders) state.folders = d.folders;
      renderFolderStripAndSelect();
      renderChatList();
    });
  }

  function openChat(id) {
    return api("/api/chat?id=" + encodeURIComponent(id)).then(function(d){
      if (d.chat) {
        state.currentChat = d.chat;
        state.liveSig = null;
        document.getElementById("selProject").value = d.chat.project || "nas";
        document.getElementById("selEngine").value = d.chat.engine || "auto";
        loadProjectInspector(d.chat.project || "nas");
        renderChatList();
        if (state.status) renderInspector();
        renderMessages();
        checkRunningJobForChat(d.chat.id);
        persistLastChat(d.chat.id);
        restoreDraft();
        restoreQueue(d.chat.id);
        updateComposerPlaceholder();
      }
    });
  }

  function startNewChat() {
    var proj = document.getElementById("selProject").value || "nas";
    var eng = document.getElementById("selEngine").value || "auto";
    api("/api/chat/new", { project: proj, engine: eng }).then(function(d){
      state.currentChat = d.chat;
      state.activeJob = null;
      state.liveSig = null;
      setModule("chat");
      refreshChats();
      renderMessages();
      persistLastChat(d.chat.id);
      restoreDraft();
      syncComposerBusyUI();
      closeMobileDrawers();
    });
  }
  document.getElementById("btnNewChat").onclick = startNewChat;
  document.getElementById("searchBox").oninput = function(e){ state.search = e.target.value || ""; renderChatList(); };

  /* ---- ATTACHMENTS, VOICE & PROMPT ENHANCER ---- */
  function uploadImageFile(file) {
    var reader = new FileReader();
    reader.onload = function(){
      api("/api/upload", { name: file.name, data_url: reader.result }).then(function(res){
        if (res.path) { state.pendingImages.push(res.path); renderAttachStrip(); syncComposerBusyUI(); }
      });
    };
    reader.readAsDataURL(file);
  }

  function renderAttachStrip() {
    var box = document.getElementById("attachStrip");
    box.innerHTML = "";
    state.pendingImages.forEach(function(p, idx){
      var thumb = document.createElement("div");
      thumb.className = "attach-thumb";
      thumb.innerHTML = '<img src="/api/image?path=' + encodeURIComponent(p) + '"><button type="button">✕</button>';
      thumb.querySelector("button").onclick = function(){ state.pendingImages.splice(idx, 1); renderAttachStrip(); syncComposerBusyUI(); };
      box.appendChild(thumb);
    });
    (state.pendingDocs || []).forEach(function(d, idx){
      var chip = document.createElement("div");
      chip.className = "doc-chip";
      chip.title = d.path + "\n" + (d.preview || "");
      chip.innerHTML = '<span>📄</span><span class="doc-name">' + esc(d.name || "colado") + '</span>' +
        '<span style="opacity:.65">' + fmtChars(d.chars) + '</span><button type="button" title="Remover anexo">✕</button>';
      chip.querySelector("button").onclick = function(){
        state.pendingDocs.splice(idx, 1); renderAttachStrip(); syncComposerBusyUI();
      };
      box.appendChild(chip);
    });
    placeJumpEndBtn();
  }

  /* Colar muito texto/código => guarda em ficheiro no NAS e passa só a referência.
     Evita que 400 linhas de log/código poluam o prompt (e o histórico do chat). */
  var PASTE_AS_FILE_MIN_CHARS = 600;
  var PASTE_AS_FILE_MIN_LINES = 12;

  function addPastedDoc(text) {
    var name = "colado-" + ((state.pendingDocs || []).length + 1) + ".md";
    return api("/api/paste-file", { text: text, name: name }).then(function(res){
      if (!res || !res.path) throw new Error("sem path");
      state.pendingDocs.push({
        path: res.path, name: res.name || name,
        chars: res.chars || text.length, lines: res.lines || 0,
        preview: res.preview || text.slice(0, 120)
      });
      renderAttachStrip();
      syncComposerBusyUI();
      document.getElementById("promptInput").focus();
      notifyUser("📄 Texto colado como ficheiro", (res.chars || text.length) + " caracteres guardados em " +
        (res.name || name) + " — o Maestro lê o ficheiro em vez de encher o prompt.");
      return res;
    }).catch(function(){
      // Fallback: se o servidor não tiver o endpoint, cola normal.
      var ta = document.getElementById("promptInput");
      ta.value = (ta.value ? ta.value + "\n" : "") + text;
      ta.dispatchEvent(new Event("input"));
    });
  }
  document.getElementById("btnAttach").onclick = function(){ document.getElementById("fileInput").click(); };
  document.getElementById("fileInput").onchange = function(e){
    Array.prototype.forEach.call(e.target.files || [], uploadImageFile);
    e.target.value = "";
  };
  document.addEventListener("paste", function(e){
    var items = (e.clipboardData || {}).items || [];
    for (var i = 0; i < items.length; i++) {
      if (items[i].type && items[i].type.indexOf("image/") === 0) {
        var f = items[i].getAsFile();
        if (f) uploadImageFile(f);
      }
    }
  });

  var btnVoice = document.getElementById("btnVoice");
  var SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (SpeechRec) {
    var rec = new SpeechRec();
    rec.lang = "pt-PT";
    rec.onresult = function(ev){
      var ta = document.getElementById("promptInput");
      ta.value = (ta.value ? ta.value + " " : "") + ev.results[0][0].transcript;
      ta.dispatchEvent(new Event("input"));
    };
    rec.onend = function(){ state.recording = false; btnVoice.textContent = "🎤 Voz"; };
    state.recognition = rec;
  }
  btnVoice.onclick = function(){
    if (!state.recognition) { document.getElementById("promptInput").focus(); return; }
    if (state.recording) state.recognition.stop();
    else { state.recording = true; btnVoice.textContent = "🔴 A ouvir…"; state.recognition.start(); }
  };

  document.getElementById("btnEnhance").onclick = function(){
    var ta = document.getElementById("promptInput");
    var raw = (ta.value || "").trim();
    if (!raw) { ta.focus(); return; }
    var btn = document.getElementById("btnEnhance");
    btn.textContent = "⏳…";
    api("/api/enhance", { text: raw, project: document.getElementById("selProject").value }).then(function(r){
      if (r.enhanced) { ta.value = r.enhanced; ta.dispatchEvent(new Event("input")); }
    }).finally(function(){ btn.textContent = "✨ Melhorar"; });
  };

  document.getElementById("btnAutoEnhance").onclick = function(){
    state.autoEnhance = !state.autoEnhance;
    var b = document.getElementById("btnAutoEnhance");
    b.classList.toggle("magic", state.autoEnhance);
    b.classList.toggle("active", state.autoEnhance);
    b.textContent = state.autoEnhance ? "Auto-✨ ON" : "Auto-✨";
  };

  /* ---- LIVE STREAMING JOB POLLER + MESSAGE QUEUE & STEER ENGINE ---- */
  var ta = document.getElementById("promptInput");
  /* ---- ESTILO DE CHAT: auto / código / prosa ---- */
  var CODE_HINTS = /(```|^(\s*)\$\s|\b(ssh|docker|git|npm|pnpm|systemctl|curl|grep|sed|awk|python|node|bash|sudo|chmod|kubectl)\b|\b(erro|error|traceback|exception|stack trace|segfault|build|deploy|patch|commit|diff|stack|log|stdout|stderr)\b|\b(função|classe|script|endpoint|api|json|yaml|sql|regex|C#|Unity|\.py|\.js|\.ts|\.cs|\.sh)\b)/i;
  var STYLE_LABELS = { auto: "⌘ Auto", code: "⌘ Código", prose: "📖 Prosa" };
  var STYLE_ORDER = ["auto", "code", "prose"];

  function looksCodey(text) {
    if (!text) return false;
    if (/```/.test(text)) return true;
    var hits = (String(text).match(CODE_HINTS) || []).length;
    return hits >= 2;
  }
  function chatStylePref(chatId) {
    try {
      var m = JSON.parse(localStorage.getItem("maestro_chat_style") || "{}");
      return m[chatId] || "auto";
    } catch (e) { return "auto"; }
  }
  function saveChatStylePref(chatId, style) {
    try {
      var m = JSON.parse(localStorage.getItem("maestro_chat_style") || "{}");
      m[chatId] = style;
      localStorage.setItem("maestro_chat_style", JSON.stringify(m));
    } catch (e) {}
  }
  function detectChatStyle() {
    var c = state.currentChat;
    if (!c || !c.messages || !c.messages.length) return "prose";
    var codey = 0, total = 0;
    for (var i = Math.max(0, c.messages.length - 8); i < c.messages.length; i++) {
      var m = c.messages[i];
      total++;
      if (looksCodey(m.text || "")) codey++;
    }
    return (codey >= Math.max(1, Math.ceil(total / 3))) ? "code" : "prose";
  }
  function applyChatStyle() {
    var box = document.getElementById("chatScroll");
    if (!box) return;
    var cid = state.currentChat ? state.currentChat.id : "__none__";
    var pref = chatStylePref(cid);
    var resolved = (pref === "auto") ? detectChatStyle() : pref;
    if (box.getAttribute("data-style") !== resolved) box.setAttribute("data-style", resolved);
    var btn = document.getElementById("btnChatStyle");
    if (btn) {
      btn.textContent = state.lang === "en" ? ({auto:"⌘ Auto", code:"⌘ Code", prose:"📖 Prose"}[pref] || "⌘ Auto") : (STYLE_LABELS[pref] || "⌘ Auto");
      btn.classList.toggle("magic", pref === "code");
      btn.title = ui("Auto deteta: agora ", "Auto detected: ") + (resolved === "code" ? ui("⌘ código", "⌘ code") : ui("📖 prosa", "📖 prose"));
    }
  }
  var _btnChatStyle = document.getElementById("btnChatStyle");
  if (_btnChatStyle) {
    _btnChatStyle.onclick = function(){
      var cid = state.currentChat ? state.currentChat.id : "__none__";
      var cur = chatStylePref(cid);
      var next = STYLE_ORDER[(STYLE_ORDER.indexOf(cur) + 1) % STYLE_ORDER.length];
      saveChatStylePref(cid, next);
      applyChatStyle();
    };
  }

  // Keep the options above the composer even when its dock scrolls.
  (function initComposerMenu(){
    var trigger = document.getElementById("btnComposerMore");
    var menu = document.getElementById("composerMenu");
    document.body.appendChild(menu);
    function close(restoreFocus){
      menu.hidden = true;
      trigger.setAttribute("aria-expanded", "false");
      if (restoreFocus) trigger.focus();
    }
    trigger.addEventListener("click", function(){
      if (!menu.hidden) { close(true); return; }
      var rect = trigger.getBoundingClientRect();
      menu.style.maxHeight = Math.max(120, rect.top - 20) + "px";
      menu.hidden = false;
      menu.style.left = Math.max(12, Math.min(rect.left, innerWidth - menu.offsetWidth - 12)) + "px";
      menu.style.top = Math.max(12, rect.top - menu.offsetHeight - 8) + "px";
      trigger.setAttribute("aria-expanded", "true");
      menu.querySelector("button:not([hidden])").focus();
    });
    document.addEventListener("click", function(e){
      if (!menu.hidden && !menu.contains(e.target) && !trigger.contains(e.target)) close(false);
    });
    document.addEventListener("keydown", function(e){
      if (menu.hidden) return;
      if (e.key === "Escape") { e.preventDefault(); close(true); }
    });
    document.addEventListener("focusin", function(e){
      if (!menu.hidden && !menu.contains(e.target) && e.target !== trigger) close(false);
    });
    menu.addEventListener("click", function(e){
      var button = e.target.closest("button");
      if (button && ["btnAttach", "btnVoice", "btnEnhance", "btnAltBusy"].indexOf(button.id) !== -1) close(true);
    });
    window.addEventListener("resize", function(){ close(false); });
    document.querySelector(".composer-dock").addEventListener("scroll", function(){ close(false); });
  })();

  var btnBusyMode = document.getElementById("btnBusyMode");
  var btnAltBusy = document.getElementById("btnAltBusy");
  var btnStopJob = document.getElementById("btnStopJob");
  var btnSend = document.getElementById("btnSend");

  function syncBusyModeButton() {
    if (!btnBusyMode) return;
    var isSteer = (state.busyMode === "steer");
    btnBusyMode.textContent = isSteer ? ui("🧭 Modo: Orientar", "🧭 Mode: Steer") : ui("📥 Modo: Fila", "📥 Mode: Queue");
    btnBusyMode.classList.toggle("magic", isSteer);
    syncComposerBusyUI();
  }
  if (btnBusyMode) {
    btnBusyMode.onclick = function(){
      state.busyMode = (state.busyMode === "queue") ? "steer" : "queue";
      localStorage.setItem("maestro_busy_mode", state.busyMode);
      syncBusyModeButton();
    };
  }

  function isCurrentChatBusy() {
    return Boolean(state.activeJob && ["running", "queued"].indexOf(state.activeJob.status) !== -1 && state.currentChat && state.activeJob.chat_id === state.currentChat.id);
  }

  function queueReason(item) {
    var labels = {
      evaluating: ["A avaliar estratégia…", "Evaluating strategy…"],
      dependency: ["Aguardar contexto / evitar conflitos", "Waiting for context / avoiding conflicts"],
      capacity: ["Aguardar vaga (máximo 2 agentes)", "Waiting for capacity (maximum 2 agents)"],
      quota: ["Aguardar quota ou modelo compatível", "Waiting for quota or compatible model"],
      engine_busy: ["Aguardar agente adequado", "Waiting for suitable agent"],
      interrupted: ["Interrompido · retomar manualmente", "Interrupted · resume manually"]
    };
    var label = labels[item.reason] || labels.evaluating;
    return ui(label[0], label[1]);
  }
  function renderQueueStrip() {
    var box = document.getElementById("queueStrip");
    if (!box) return;
    /* A fila é reenviada a cada poll. Reconstruir o innerHTML sem mudanças
       fazia o compositor redimensionar (e o chat saltar). */
    var sig = state.lang + "|" + state.messageQueue.map(function(item){
      return [item.id, item.status, item.reason, item.text].join("~");
    }).join("|");
    if (box.__queueSig === sig) return;
    box.__queueSig = sig;
    box.hidden = !state.messageQueue.length;
    box.innerHTML = state.messageQueue.length ? '<div class="queue-strip-head">' + ui('📥 FILA MAESTRO · estratégia por contexto, quotas e custo', '📥 MAESTRO QUEUE · context, quotas and cost strategy') + '</div>' +
      state.messageQueue.map(function(item, idx){
        return '<div class="queue-item"><span class="queue-item-txt"><b>#' + (idx+1) + '</b> ' + esc(item.text || ui("Anexo", "Attachment")) +
          '<br><small>' + esc(queueReason(item)) + '</small></span><div class="queue-item-acts">' +
          (item.status === 'interrupted' ? '<button type="button" data-qretry="' + esc(item.id) + '">▶</button>' : '') +
          '<button type="button" data-qcancel="' + esc(item.id) + '" aria-label="' + ui('Remover da fila', 'Remove from queue') + '">✕</button></div></div>';
      }).join('') : '';
    box.querySelectorAll('[data-qcancel], [data-qretry]').forEach(function(button){
      button.onclick = function(){
        var retry = button.getAttribute('data-qretry');
        button.disabled = true;
        api('/api/chat/queue/' + (retry ? 'retry' : 'cancel'), {task_id: retry || button.getAttribute('data-qcancel')})
          .then(function(){ checkRunningJobForChat(state.currentChat.id); });
      };
    });
  }
  function receiveQueue(res) {
    if (res && res.activity_version && res.chat) {
      var versions = state.activityVersions || (state.activityVersions = {});
      if (res.activity_version < (versions[res.chat.id] || 0)) return false;
      versions[res.chat.id] = res.activity_version;
    }
    if (res && Array.isArray(res.queue)) { state.messageQueue = res.queue; renderQueueStrip(); }
    return true;
  }

  function syncComposerBusyUI() {
    var busy = isCurrentChatBusy();
    updateComposerPlaceholder();
    var hasInput = Boolean((ta.value || "").trim() || state.pendingImages.length);
    /* Este bloco corre a cada 950 ms. Escrever o mesmo texto (ou recriar o
       innerHTML dos pontinhos) obrigava o dock a re-medir a altura, e o chat
       inteiro mexia. Só se toca no DOM quando o estado muda mesmo. */
    function setModeText(el, mode, txt) {
      if (!el) return;
      if (el.dataset.mode !== mode) {
        el.dataset.mode = mode;
        el.textContent = txt;
      } else if (el.textContent !== txt) {
        el.textContent = txt;
      }
    }
    function setAlt(mode, txt) {
      if (!btnAltBusy) return;
      if (btnAltBusy.hidden) btnAltBusy.hidden = false;
      setModeText(btnAltBusy, "alt-" + mode, txt);
    }
    if (btnStopJob && btnStopJob.hidden === busy) btnStopJob.hidden = !busy;
    if (!busy) {
      if (btnAltBusy && !btnAltBusy.hidden) btnAltBusy.hidden = true;
      if (btnSend) {
        btnSend.disabled = false;
        setModeText(btnSend, "idle", ui("Enviar ↗", "Send ↗"));
      }
      return;
    }
    if (!hasInput) {
      if (btnAltBusy && !btnAltBusy.hidden) btnAltBusy.hidden = true;
      if (btnSend) {
        btnSend.disabled = false;
        var busyMode = state.activeJob.status === 'queued' ? 'waiting' : 'busy';
        if (btnSend.dataset.mode !== busyMode) {
          btnSend.dataset.mode = busyMode;
          btnSend.innerHTML = state.activeJob.status === 'queued' ? ui('Em fila', 'Queued') : ui('A trabalhar', 'Working') + '<span class="btn-dots"><i></i><i></i><i></i></span>';
        }
      }
    } else {
      if (btnSend) btnSend.disabled = false;
      if (state.busyMode === "queue") {
        setModeText(btnSend, "queue", ui("📥 Meter em Fila ↵", "📥 Queue message ↵"));
        setAlt("queue", ui("🧭 Orientar já", "🧭 Steer now"));
      } else {
        setModeText(btnSend, "steer", ui("🧭 Orientar Agora ↵", "🧭 Steer now ↵"));
        setAlt("steer", ui("📥 Meter em Fila", "📥 Queue message"));
      }
    }
  }
  syncBusyModeButton();

  if (btnStopJob) {
    btnStopJob.onclick = function(){
      if (!state.currentChat) return;
      api("/api/chat/stop", { id: state.currentChat.id }).then(function(){
        state.activeJob = null;
        syncComposerBusyUI();
        openChat(state.currentChat.id);
      });
    };
  }

  if (btnAltBusy) {
    btnAltBusy.onclick = function(){
      var oppositeMode = (state.busyMode === "queue") ? "steer" : "queue";
      handleComposerSubmit(oppositeMode);
    };
  }

  function checkRunningJobForChat(chatId) {
    if (!chatId) return;
    api("/api/chat/job?id=" + encodeURIComponent(chatId)).then(function(res){
      if (!state.currentChat || state.currentChat.id !== chatId) return;
      if (!receiveQueue(res)) return;
      if (res.chat) state.currentChat = res.chat;
      if (res && res.job && ["running", "queued"].indexOf(res.job.status) !== -1) {
        state.activeJob = res.job;
        renderMessages();
        syncComposerBusyUI();
        startJobPolling(chatId);
      } else {
        if (state.activeJob && state.activeJob.chat_id === chatId) {
          state.activeJob = null;
          syncComposerBusyUI();
        }
        /* Fila restaurada de um reload/turno falhado: sem isto ficava
           eternamente à espera de um job que já não existe. */
        renderMessages();
        syncComposerBusyUI();
        flushQueueIfIdle(chatId);
      }
    }).catch(function(){});
  }

  function flushQueueIfIdle(chatId) {
    // Admission and dispatch belong to the durable server queue.
  }

  function startJobPolling(chatId) {
    if (state.jobPollTimer) clearInterval(state.jobPollTimer);
    state.jobPollTimer = setInterval(function(){
      api("/api/chat/job?id=" + encodeURIComponent(chatId)).then(function(res){
        if (!res || !state.currentChat || state.currentChat.id !== chatId) return;
        if (!receiveQueue(res)) return;
        var changed = res.chat && JSON.stringify(res.chat.messages) !== JSON.stringify(state.currentChat.messages);
        if (res.chat) state.currentChat = res.chat;
        if (res.job && ["running", "queued"].indexOf(res.job.status) !== -1) {
          var activityChanged = !state.activeJob || state.activeJob.task_id !== res.job.task_id || state.activeJob.status !== res.job.status;
          var queueChanged = JSON.stringify(state.messageQueue) !== state.renderedQueue;
          state.activeJob = res.job;
          if (state.currentChat && state.currentChat.id === chatId) {
            /* Atualização cirúrgica: só o turno "ao vivo" é tocado, em sítio.
               O histórico já renderizado nunca é reconstruído => zero flashes. */
            if (changed || activityChanged || queueChanged) renderMessages();
            state.renderedQueue = JSON.stringify(state.messageQueue);
            ensureLiveTurn(res.job);
            syncComposerBusyUI();
          }
        } else {
          state.liveSig = null;
          state.liveStructSig = null;
          state.lastLiveRender = 0;
          clearInterval(state.jobPollTimer);
          state.jobPollTimer = null;
          state.activeJob = null;
          if (res.chat && state.currentChat && state.currentChat.id === chatId) {
            state.currentChat = res.chat;
          }
          refreshChats();
          renderMessages();
          syncComposerBusyUI();
          loadStatusAndPulse();
          if (res.chat && res.chat.messages && res.chat.messages.length) {
            var lastMsg = res.chat.messages[res.chat.messages.length - 1];
            if (lastMsg && lastMsg.role === "assistant") {
              notifyUser("👑 " + (lastMsg.agent_used || "Maestro AI"), lastMsg.text || "Concluído!");
            }
          }
          flushQueueIfIdle(chatId);
        }
      }).catch(function(){});
    }, 950);
  }

  ta.addEventListener("input", function(){
    ta.style.height = "auto";
    ta.style.height = Math.min(160, ta.scrollHeight) + "px";
    syncComposerBusyUI();
  });

  /* ---- RASCUNHOS PERSISTENTES ----
     O auto-update, um F5 ou um crash a meio do turno recarregavam a página e
     levavam o texto já escrito. Aqui o rascunho fica em localStorage, por
     conversa: sobrevive a reload, a fechar o separador e a reiniciar a app. */
  var DRAFT_STORE = "maestro_drafts_v2";
  var QUEUE_STORE = "maestro_queue_v2";
  var LAST_CHAT_STORE = "maestro_last_chat";

  function readStore(key){
    try { return JSON.parse(localStorage.getItem(key) || "null"); } catch(e){ return null; }
  }
  function writeStore(key, val){
    try { localStorage.setItem(key, JSON.stringify(val)); } catch(e){}
  }
  function draftSlot(){
    return (state.currentChat && state.currentChat.id) ? state.currentChat.id : "__new__";
  }
  var draftTimer = null;
  function persistDraft(immediate){
    if (draftTimer) { clearTimeout(draftTimer); draftTimer = null; }
    var write = function(){
      var all = readStore(DRAFT_STORE) || {};
      var slot = draftSlot();
      var txt = ta.value || "";
      if (txt.trim()) all[slot] = { text: txt, ts: Date.now() };
      else delete all[slot];
      var keys = Object.keys(all);
      if (keys.length > 80) {
        keys.sort(function(a, b){ return (all[a].ts || 0) - (all[b].ts || 0); });
        keys.slice(0, keys.length - 80).forEach(function(k){ delete all[k]; });
      }
      writeStore(DRAFT_STORE, all);
    };
    if (immediate) write(); else draftTimer = setTimeout(write, 250);
  }
  function clearDraft(){
    var all = readStore(DRAFT_STORE) || {};
    delete all[draftSlot()];
    writeStore(DRAFT_STORE, all);
  }
  function restoreDraft(){
    var all = readStore(DRAFT_STORE) || {};
    var d = all[draftSlot()];
    if (!d || typeof d.text !== "string" || !d.text.trim()) return;
    ta.value = d.text;
    ta.dispatchEvent(new Event("input"));
    syncComposerBusyUI();
  }
  function persistLastChat(id){
    try { if (id) localStorage.setItem(LAST_CHAT_STORE, id); } catch(e){}
  }
  function restoreQueue(chatId){
    state.messageQueue = [];
    renderQueueStrip();
    var legacy = readStore(QUEUE_STORE);
    if (legacy && legacy.chatId === chatId && Array.isArray(legacy.items) && legacy.items.length) {
      legacy.items.reduce(function(chain, item, index){
        return chain.then(function(){ return api('/api/chat/send', {id: chatId, text: item.text, images: item.images || [], docs: item.docs || [],
          project: item.proj, engine: item.eng || 'auto', async: true, request_id: 'legacy-' + chatId + '-' + index + '-' + hash32(item.text)}); });
      }, Promise.resolve()).then(function(){ localStorage.removeItem(QUEUE_STORE); checkRunningJobForChat(chatId); });
    }
  }

  ta.addEventListener("input", function(){ persistDraft(false); });
  ta.addEventListener("change", function(){ persistDraft(true); });
  window.addEventListener("beforeunload", function(){ persistDraft(true); });
  document.addEventListener("visibilitychange", function(){ if (document.hidden) persistDraft(true); });

  /* Colar blocos grandes => anexo em ficheiro (como no Claude/CLIs), não lixo no prompt. */
  ta.addEventListener("paste", function(e){
    var cd = e.clipboardData;
    if (!cd) return;
    var txt = "";
    try { txt = cd.getData("text/plain") || ""; } catch(err) { return; }
    var hasImage = Array.prototype.some.call(cd.items || [], function(it){
      return it.kind === "file" && String(it.type || "").indexOf("image/") === 0;
    });
    if (hasImage || !txt.trim()) return;
    var lines = txt.split("\n").length;
    if (txt.length < PASTE_AS_FILE_MIN_CHARS && lines < PASTE_AS_FILE_MIN_LINES) return;
    e.preventDefault();
    addPastedDoc(txt);
  });
  ta.addEventListener("keydown", function(e){
    if (e.key === "Enter" && !e.shiftKey && window.innerWidth > 760) {
      e.preventDefault();
      document.getElementById("composerForm").requestSubmit();
    }
  });

  function dispatchChatTurn(rawTxt, imgs, proj, eng, isSteer, docs) {
    docs = docs || [];
    var prep = (state.autoEnhance && rawTxt.length > 3 && rawTxt.length < 220)
      ? api("/api/enhance", { text: rawTxt, project: proj }).then(function(r){ return r.enhanced || rawTxt; }).catch(function(){ return rawTxt; })
      : Promise.resolve(rawTxt);

    var targetChat = state.currentChat;
    var modelConfig = selectedModelConfig();
    var requestId = (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : String(Date.now()) + '-' + Math.random();
    return state.submitChain = (state.submitChain || Promise.resolve()).then(function(){ return prep; }).then(function(finalTxt){
      var ensure = (targetChat || state.currentChat)
        ? Promise.resolve(targetChat || state.currentChat)
        : api("/api/chat/new", { project: proj, engine: eng }).then(function(d){ return d.chat; });

      return ensure.then(function(chat){
        if (!state.currentChat) state.currentChat = chat;
        return api("/api/chat/send", {
          id: chat.id,
          project: proj,
          engine: eng,
          model_id: modelConfig.model_id,
          effort: modelConfig.effort,
          text: finalTxt,
          images: imgs,
          docs: docs,
          steer: Boolean(isSteer),
          async: true,
          request_id: requestId
        });
      });
    }).then(function(res){
      if (res && res.chat && state.currentChat && res.chat.id !== state.currentChat.id) return;
      if (!receiveQueue(res)) return;
      if (res && res.chat) state.currentChat = res.chat;
      state.activeJob = res && res.job || null;
      renderMessages();
      syncComposerBusyUI();
      if (state.currentChat) startJobPolling(state.currentChat.id);
    }).catch(function(error){
      ta.value = rawTxt;
      state.pendingImages = imgs;
      state.pendingDocs = docs;
      renderAttachStrip(); persistDraft(true);
      notifyUser("Maestro", ui("Não foi possível guardar o pedido. O texto foi reposto.", "Could not save the request. Your text was restored."));
      if (state.currentChat) checkRunningJobForChat(state.currentChat.id);
    });
  }

  function handleComposerSubmit(overrideBusyMode) {
    var txt = (ta.value || "").trim();
    var docs = (state.pendingDocs || []).slice();
    if (!txt && !state.pendingImages.length && !docs.length) {
      ta.focus();
      return;
    }
    if (!txt && (docs.length || state.pendingImages.length)) {
      txt = docs.length ? "Analisa o conteúdo dos ficheiros que colei e diz-me o que encontras." : "Analisa a imagem anexada.";
    }
    var proj = document.getElementById("selProject").value || "nas";
    var eng = document.getElementById("selEngine").value || "auto";
    var imgs = state.pendingImages.slice();
    state.pendingImages = [];
    state.pendingDocs = [];
    renderAttachStrip();
    ta.value = ""; ta.style.height = "auto";
    clearDraft();

    if (isCurrentChatBusy()) {
      var modeToUse = overrideBusyMode || state.busyMode || "queue";
      if (modeToUse === "queue") {
        dispatchChatTurn(txt, imgs, proj, eng, false, docs);
        return;
      } else {
        dispatchChatTurn(txt, imgs, proj, eng, true, docs);
        return;
      }
    }
    dispatchChatTurn(txt, imgs, proj, eng, false, docs);
  }

  document.getElementById("composerForm").onsubmit = function(e){
    e.preventDefault();
    handleComposerSubmit();
  };

  /* ---- MODERN HOVER TOOLTIP ENGINE ---- */
  var tipEl = document.createElement("div");
  tipEl.id = "modernTooltip";
  tipEl.className = "modern-tooltip";
  document.body.appendChild(tipEl);

  var DEFAULT_TIPS = {
    "btnNewChat": "＋ Nova Conversa|Cria uma sessão limpa no projeto e motor selecionados.",
    "btnOpenSettings": "⚙️ Opções & Personalização|Muda idioma (PT/EN), cores de destaque, ícones unicolores e instaladores (.exe / Android).",
    "btnHeaderSettings": "⚙️ Opções & Personalização|Abre o painel de idioma, temas de cor, estilo de ícones e Auto-Updater.",
    "btnCleanup": "🧹 Limpar Sessões|Fecha processos temporários e limpa recursos órfãos do Maestro.",
    "btnToggleLeft": "☰ Menu & Pastas|Mostra ou recolhe a barra lateral de projetos e histórico de conversas.",
    "btnToggleRight": "📊 Painel Studio|Mostra ou recolhe o inspetor lateral (Quotas reais OAuth, Pulso NAS, Git & Frota).",
    "btnNotif": "🔔 Notificações Nativas|Ativa alertas sonoros e notificações quando o Maestro termina uma tarefa.",
    "selProject": "📁 Pasta / Projeto Ativo|Define o diretório de trabalho (NAS, Polistrat, SpaceBourne 2, DungenLabs…).",
    "selEngine": "🧠 Motor de IA|Escolhe Auto (Maestro Orquestra) ou fixa Antigravity, Claude Sonnet/Opus, Codex ou DeepSeek."
  };

  function resolveTipContent(el) {
    if (!el) return null;
    var raw = el.getAttribute("data-tip");
    if (!raw && el.id && DEFAULT_TIPS[el.id]) raw = DEFAULT_TIPS[el.id];
    if (!raw && el.getAttribute("title")) {
      raw = el.getAttribute("title");
      el.setAttribute("data-tip", raw);
      el.removeAttribute("title");
    }
    if (!raw) {
      var mod = el.getAttribute("data-mod");
      if (mod === "chat") raw = "💬 Chat & Orquestrador|Conversa multimodal com visão, voz, fila de mensagens e orientação em tempo real.";
      else if (mod === "studio") raw = "🎨 Imagens AI, GIFs & Galeria|Gera imagens Flux HD, animações GIF e explora os teus screenshots com zoom.";
      else if (mod === "media") raw = "🎬 Media (Radarr/Sonarr) & Docker|Pesquisa filmes/séries para sacar em 1 clique e gere containers do NAS.";
      else if (mod === "casa") raw = "🏠 Casa, 3D & Schedulers|Monitoriza as impressoras Centauri Carbon 2 e Qidi Plus 4, tomadas e automações.";
      else if (mod === "artifacts") raw = "🧩 Biblioteca de Artifacts|Código, scripts C# e previews HTML/SVG guardados por pasta.";
    }
    return raw;
  }

  document.addEventListener("mouseover", function(e){
    if (window.innerWidth <= 820) return;
    var target = e.target.closest ? e.target.closest("button, a, select, [data-tip], [title]") : null;
    var tipText = resolveTipContent(target);
    if (!tipText) {
      tipEl.classList.remove("show");
      return;
    }
    var parts = tipText.split("|");
    if (parts.length > 1) {
      tipEl.innerHTML = '<span class="tt-title">' + esc(parts[0]) + '</span><span class="tt-desc">' + esc(parts.slice(1).join(" ")) + '</span>';
    } else {
      tipEl.innerHTML = '<span class="tt-title">' + esc(parts[0]) + '</span>';
    }
    var rect = target.getBoundingClientRect();
    tipEl.classList.add("show");
    var tw = tipEl.offsetWidth || 220;
    var th = tipEl.offsetHeight || 48;
    var left = Math.max(10, Math.min(window.innerWidth - tw - 10, rect.left + rect.width / 2 - tw / 2));
    var top = (rect.top > th + 16) ? (rect.top - th - 9) : (rect.bottom + 9);
    tipEl.style.left = Math.round(left) + "px";
    tipEl.style.top = Math.round(top) + "px";
  });
  document.addEventListener("mouseout", function(e){
    var rel = e.relatedTarget;
    if (!rel || !rel.closest || !rel.closest("button, a, select, [data-tip]")) {
      tipEl.classList.remove("show");
    }
  });
  document.addEventListener("mousedown", function(){ tipEl.classList.remove("show"); });

  document.getElementById("btnCleanup").onclick = function(){
    api("/api/cleanup", {}).then(function(r){
      notifyUser("🧹 Maestro", (state.lang === "en" ? "Temporary sessions cleaned (" : "Sessões temporárias limpas (") + (r.cleaned_sessions || []).length + ").");
    });
  };
  document.getElementById("btnInstallPwa").onclick = function(){
    if (isStandaloneApp()) {
      notifyUser("📲 Maestro AI", state.lang === "en" ? "You are already using the installed app." : "Já estás a usar a app instalada.");
      return;
    }
    if (!window.isSecureContext) {
      window.open("https://maestro.hubertdungen.com/", "_blank", "noopener,noreferrer");
      return;
    }
    var promptEvent = state.deferredPrompt;
    if (promptEvent) {
      state.deferredPrompt = null;
      try {
        var promptResult = promptEvent.prompt();
        if (promptResult && promptResult.catch) promptResult.catch(installPromptFailed);
        if (promptEvent.userChoice && promptEvent.userChoice.then) {
          promptEvent.userChoice.then(function(choice){
            if (choice && choice.outcome === "accepted") {
              notifyUser("📲 Maestro AI", state.lang === "en" ? "Installing app…" : "A instalar a app…");
            } else {
              syncInstallButton();
            }
          }).catch(installPromptFailed);
        }
      } catch (e) {
        installPromptFailed(e);
      }
      return;
    }
    var ua = navigator.userAgent || "";
    var android = /Android/i.test(ua);
    var ios = /iPhone|iPad|iPod/i.test(ua);
    var msg;
    if (state.lang === "en") {
      msg = android
        ? "📲 Chrome on Android: tap ⋮ → «Install app» (or «Add to Home screen»). If «Install app» is unavailable, open the site over HTTPS (maestro.hubertdungen.com)."
        : ios
          ? "📲 On iPhone/iPad: open in Safari → Share → «Add to Home Screen»."
          : "📲 In Chrome/Edge: use the install icon in the address bar, or the browser menu → «Install Maestro AI».";
    } else {
      msg = android
        ? "📲 Chrome no Android: toca em ⋮ → «Instalar aplicação» (ou «Adicionar ao ecrã principal»). Se «Instalar aplicação» não aparecer, abre o site por HTTPS (maestro.hubertdungen.com)."
        : ios
          ? "📲 No iPhone/iPad: abre no Safari → Partilhar → «Adicionar ao ecrã principal»."
          : "📲 No Chrome/Edge: usa o ícone de instalação na barra de endereço, ou o menu do browser → «Instalar Maestro AI».";
    }
    alert(msg);
  };

  /* ---- SVG UNICOLOR ICON LIBRARY & ICON STYLE SWITCHER ---- */
  var SVG_ICONS = {
    chat: '<svg class="svg-ico" viewBox="0 0 24 24"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg>',
    studio: '<svg class="svg-ico" viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg>',
    media: '<svg class="svg-ico" viewBox="0 0 24 24"><rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4M10 7l6 3-6 3V7z"/></svg>',
    casa: '<svg class="svg-ico" viewBox="0 0 24 24"><path d="M3 10.5L12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1v-9.5z"/></svg>',
    artifacts: '<svg class="svg-ico" viewBox="0 0 24 24"><polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/></svg>',
    settings: '<svg class="svg-ico" viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9c.26.604.852.997 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>',
    clean: '<svg class="svg-ico" viewBox="0 0 24 24"><path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6h14z"/></svg>',
    notif: '<svg class="svg-ico" viewBox="0 0 24 24"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>',
    panel: '<svg class="svg-ico" viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="15" y1="3" x2="15" y2="21"/></svg>',
    camera: '<svg class="svg-ico" viewBox="0 0 24 24"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg>',
    mic: '<svg class="svg-ico" viewBox="0 0 24 24"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2M12 19v4M8 23h8"/></svg>',
    sparkle: '<svg class="svg-ico" viewBox="0 0 24 24"><path d="M12 3l1.9 5.8a2 2 0 0 0 1.3 1.3L21 12l-5.8 1.9a2 2 0 0 0-1.3 1.3L12 21l-1.9-5.8a2 2 0 0 0-1.3-1.3L3 12l5.8-1.9a2 2 0 0 0 1.3-1.3L12 3z"/></svg>',
    quotas: '<svg class="svg-ico" viewBox="0 0 24 24"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>',
    pulso: '<svg class="svg-ico" viewBox="0 0 24 24"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>',
    git: '<svg class="svg-ico" viewBox="0 0 24 24"><circle cx="18" cy="18" r="3"/><circle cx="6" cy="6" r="3"/><path d="M6 21V9a9 9 0 0 0 9 9"/></svg>',
    fleet: '<svg class="svg-ico" viewBox="0 0 24 24"><rect x="3" y="11" width="18" height="10" rx="2"/><circle cx="12" cy="5" r="2"/><path d="M12 7v4M8 16h.01M16 16h.01"/></svg>'
  };

  var EMOJI_ICONS = {
    chat: "💬", studio: "🎨", media: "🎬", casa: "🏠", artifacts: "🧩"
  };

  /* ---- SETTINGS MODAL, LANGUAGE (PT/EN) & THEME/COLOR CUSTOMIZER ---- */
  var THEME_PRESETS = {
    cyber:   { cyan: "#38bdf8", violet: "#a855f7", bg: "#050811", panel: "#0a0f1d", card: "#10182d" },
    emerald: { cyan: "#10b981", violet: "#06b6d4", bg: "#040d0a", panel: "#071712", card: "#0c241c" },
    solar:   { cyan: "#f59e0b", violet: "#ef4444", bg: "#0f0906", panel: "#18100b", card: "#241810" },
    opus:    { cyan: "#c084fc", violet: "#ec4899", bg: "#090514", panel: "#120a24", card: "#1b1035" },
    oled:    { cyan: "#38bdf8", violet: "#a855f7", bg: "#000000", panel: "#050508", card: "#0c0c12" },
    crimson: { cyan: "#fb7185", violet: "#f43f5e", bg: "#0d0408", panel: "#170910", card: "#240f19" }
  };

  var I18N = {
    pt: {
      settings_btn: "Opções",
      clean_btn: "Limpar",
      panel_btn: "Painel",
      settings_title: "⚙️ Opções, Idioma, Cores & Instaladores",
      sec_lang: "🌐 IDIOMA DA INTERFACE / LANGUAGE",
      sec_icons: "✨ ESTILO DE ÍCONES (UNICOLOR / DUOTONE / COLORIDO)",
      sec_theme: "🎨 TEMAS & PERSONALIZADOR DE CORES",
      reset_theme: "↺ Repor Padrão",
      col_primary: "Cor Principal",
      col_secondary: "Brilho / Glow",
      col_bg: "Fundo App",
      col_panel: "Painéis / Cartões",
      ui_scale_lbl: "Escala da Interface / Texto:",
      sec_updater: "🔄 AUTO-UPDATER & INSTALADORES DA APP",
      updater_desc: "Atualiza automaticamente a interface e o .exe sempre que o Maestro evolui no servidor.",
      check_upd_btn: "Verificar",
      refresh_app: "Atualizar app",
      pull_refresh: "Puxa para atualizar",
      install_desc: "Instala o Maestro AI Studio como aplicação nativa no teu Windows (ecrãs grandes) ou no telemóvel/tablet Android & iOS.",
      dl_exe_btn: "Instalar no Windows (.exe)",
      dl_pwa_btn: "Instalar App Android / Tablet",
      modal_folder_title: "🎨 Nova Pasta / Projeto Colorido",
      save_folder_btn: "Guardar Pasta Colorida",
      nav_chat: "Chat", nav_studio: "Galeria", nav_media: "Filmes", nav_casa: "Casa/HA", nav_artifacts: "Artifacts",
      new_chat: "＋ Nova Conversa",
      folders_hdr: "PASTAS & PROJETOS",
      new_folder_btn: "＋ Pasta",
      search_ph: "Procurar conversas ou pastas…",
      prompt_ph: "Diz ao Maestro o que queres fazer (ex: 'mete o filme Dune a sacar', 'cria script C#', 'estado da Qidi')…",
      lbl_attach: "Imagem", lbl_voice: "Voz", lbl_enhance: "Melhorar", btn_send: "Enviar ↗",
      itab_quotas: "Quotas", itab_pulso: "Pulso", itab_git: "Git/Files", itab_fleet: "Frota"
    },
    en: {
      settings_btn: "Settings",
      clean_btn: "Cleanup",
      panel_btn: "Inspector",
      settings_title: "⚙️ Settings, Language, Colors & Installers",
      sec_lang: "🌐 INTERFACE LANGUAGE / IDIOMA",
      sec_icons: "✨ ICON STYLE (UNICOLOR / DUOTONE / COLOR)",
      sec_theme: "🎨 THEMES & COLOR CUSTOMIZER",
      reset_theme: "↺ Reset Default",
      col_primary: "Primary Accent",
      col_secondary: "Secondary Glow",
      col_bg: "App Background",
      col_panel: "Panels / Cards",
      ui_scale_lbl: "UI & Font Scale:",
      sec_updater: "🔄 LIVE AUTO-UPDATER & APP INSTALLERS",
      updater_desc: "Automatically updates the UI and Windows .exe whenever Maestro evolves on the server.",
      check_upd_btn: "Check Now",
      refresh_app: "Update app",
      pull_refresh: "Pull to refresh",
      install_desc: "Install Maestro AI Studio as a native desktop app on Windows (widescreen) or on your Android / iOS phone & tablet.",
      dl_exe_btn: "Install on Windows (.exe)",
      dl_pwa_btn: "Install Android / Tablet App",
      modal_folder_title: "🎨 New Colored Folder / Project",
      save_folder_btn: "Save Colored Folder",
      nav_chat: "Chat", nav_studio: "Gallery", nav_media: "Media", nav_casa: "Home/HA", nav_artifacts: "Artifacts",
      new_chat: "＋ New Chat",
      folders_hdr: "FOLDERS & PROJECTS",
      new_folder_btn: "＋ Folder",
      search_ph: "Search chats or folders…",
      prompt_ph: "Ask Maestro anything (e.g., 'download Dune in Radarr', 'inspect C# script', 'check Qidi status')…",
      lbl_attach: "Image", lbl_voice: "Voice", lbl_enhance: "Enhance", btn_send: "Send ↗",
      itab_quotas: "Quotas", itab_pulso: "Pulse", itab_git: "Git/Files", itab_fleet: "Fleet"
    }
  };

  function savePrefs(prefs) {
    try { localStorage.setItem("maestro_prefs_v3", JSON.stringify(prefs)); } catch(e){}
  }
  function loadPrefs() {
    try { return JSON.parse(localStorage.getItem("maestro_prefs_v3") || "{}"); } catch(e){ return {}; }
  }

  function applyThemeColors(t) {
    var root = document.documentElement;
    if (t.cyan) { root.style.setProperty("--cyan", t.cyan); document.getElementById("colCyan").value = t.cyan; }
    if (t.violet) { root.style.setProperty("--violet", t.violet); document.getElementById("colViolet").value = t.violet; }
    if (t.bg) { root.style.setProperty("--bg", t.bg); document.getElementById("colBg").value = t.bg; }
    if (t.panel) {
      root.style.setProperty("--panel", t.panel);
      root.style.setProperty("--card", t.card || t.panel);
      document.getElementById("colPanel").value = t.panel;
    }
    if (t.scale) { root.style.fontSize = t.scale; }
    var prefs = loadPrefs();
    prefs.theme = Object.assign(prefs.theme || {}, t);
    savePrefs(prefs);
  }

  function applyIconStyle(styleName) {
    state.iconStyle = styleName || "unicolor";
    document.documentElement.setAttribute("data-icon-style", state.iconStyle);
    var useSvg = (state.iconStyle === "unicolor" || state.iconStyle === "duotone");
    var dict = I18N[state.lang || "pt"];

    // Module navigation buttons (sidebar + mobile bottom nav)
    document.querySelectorAll("[data-mod]").forEach(function(btn){
      var m = btn.getAttribute("data-mod");
      var icoSpan = btn.querySelector(".ico");
      if (icoSpan) {
        icoSpan.innerHTML = useSvg ? (SVG_ICONS[m] || EMOJI_ICONS[m]) : EMOJI_ICONS[m];
      }
    });

    // Header & sidebar buttons
    var btnOpenSet = document.getElementById("btnOpenSettings");
    if (btnOpenSet) {
      btnOpenSet.innerHTML = (useSvg ? SVG_ICONS.settings + " " : "⚙️ ") + '<span data-i18n="settings_btn">' + esc(dict.settings_btn) + '</span>';
    }
    var btnClean = document.getElementById("btnCleanup");
    if (btnClean) {
      btnClean.innerHTML = (useSvg ? SVG_ICONS.clean + " " : "🧹 ") + '<span data-i18n="clean_btn">' + esc(dict.clean_btn) + '</span>';
    }
    var btnHdrSet = document.getElementById("btnHeaderSettings");
    if (btnHdrSet) btnHdrSet.innerHTML = useSvg ? SVG_ICONS.settings : "⚙️";
    var btnRight = document.getElementById("btnToggleRight");
    if (btnRight) {
      var badgeHtml = document.getElementById("headerPulseBadge") ? document.getElementById("headerPulseBadge").outerHTML : '<span id="headerPulseBadge"></span>';
      btnRight.innerHTML = (useSvg ? SVG_ICONS.panel + " " : "📊 ") + '<span data-i18n="panel_btn">' + esc(dict.panel_btn) + '</span> ' + badgeHtml;
    }

    // Composer buttons
    document.getElementById("btnAttach").innerHTML = (useSvg ? SVG_ICONS.camera + " " : "📷 ") + esc(dict.lbl_attach);
    if (!state.recording) {
      document.getElementById("btnVoice").innerHTML = (useSvg ? SVG_ICONS.mic + " " : "🎤 ") + esc(dict.lbl_voice);
    }
    document.getElementById("btnEnhance").innerHTML = (useSvg ? SVG_ICONS.sparkle + " " : "✨ ") + esc(dict.lbl_enhance);

    // Inspector tabs
    var itabMap = { quotas: [SVG_ICONS.quotas, "📊", dict.itab_quotas], pulso: [SVG_ICONS.pulso, "💓", dict.itab_pulso], git: [SVG_ICONS.git, "📁", dict.itab_git], fleet: [SVG_ICONS.fleet, "🤖", dict.itab_fleet] };
    document.querySelectorAll("[data-itab]").forEach(function(tab){
      var k = tab.getAttribute("data-itab");
      if (itabMap[k]) {
        tab.innerHTML = (useSvg ? itabMap[k][0] + " " : itabMap[k][1] + " ") + esc(itabMap[k][2]);
      }
    });

    document.querySelectorAll("#iconStyleGrid [data-icostyle]").forEach(function(b){
      b.classList.toggle("primary", b.getAttribute("data-icostyle") === state.iconStyle);
    });

    var prefs = loadPrefs();
    prefs.iconStyle = state.iconStyle;
    savePrefs(prefs);
  }

  function applyLanguage(lang) {
    state.lang = (lang === "en") ? "en" : "pt";
    document.documentElement.lang = state.lang;
    var dict = I18N[state.lang];
    document.querySelectorAll("[data-i18n]").forEach(function(el){
      var k = el.getAttribute("data-i18n");
      if (dict[k]) el.textContent = dict[k];
    });
    var navMap = { chat: dict.nav_chat, studio: dict.nav_studio, media: dict.nav_media, casa: dict.nav_casa, artifacts: dict.nav_artifacts };
    document.querySelectorAll("[data-mod]").forEach(function(btn){
      var spans = btn.querySelectorAll("span");
      var m = btn.getAttribute("data-mod");
      if (spans.length > 1 && navMap[m]) spans[1].textContent = navMap[m];
    });
    var newChatSpans = document.querySelectorAll("#btnNewChat span");
    if (newChatSpans.length) newChatSpans[0].textContent = dict.new_chat;
    var secLbl = document.querySelector(".sec-label-row span");
    if (secLbl) secLbl.textContent = dict.folders_hdr;
    var btnNF = document.getElementById("btnNewFolder");
    if (btnNF) btnNF.textContent = dict.new_folder_btn;
    document.getElementById("searchBox").placeholder = dict.search_ph;
    document.getElementById("promptInput").placeholder = dict.prompt_ph;
    document.getElementById("btnSend").textContent = dict.btn_send;

    document.getElementById("btnLangPt").classList.toggle("active", state.lang === "pt");
    document.getElementById("btnLangEn").classList.toggle("active", state.lang === "en");
    if (state.recognition) state.recognition.lang = (state.lang === "en" ? "en-US" : "pt-PT");

    var prefs = loadPrefs();
    prefs.lang = state.lang;
    savePrefs(prefs);
    applyIconStyle(state.iconStyle || prefs.iconStyle || "unicolor");
    document.querySelectorAll("[data-chat-label]").forEach(function(el){
      var labels = [["⬇ Ir para o fim", "⬇ Jump to latest"], ["Ferramentas do pedido", "Request tools"], ["Preferências e ações", "Preferences and actions"]];
      var pair = labels[Number(el.getAttribute("data-chat-label"))];
      el.textContent = ui(pair[0], pair[1]);
    });
    var rb = document.getElementById("btnRefreshApp");
    if (rb && !rb.classList.contains("has-update")) {
      rb.title = ui("Atualizar app", "Update app");
      rb.setAttribute("aria-label", ui("Atualizar app", "Update app"));
    }
    var rdb = document.getElementById("btnRefreshDrawer");
    if (rdb && !rdb.classList.contains("has-update")) {
      rdb.setAttribute("aria-label", ui("Verificar e instalar atualização da app", "Check for and install an app update"));
    }
    var tips = {
      btnComposerMore: "More request options", composerMenu: "Request options",
      btnStopJob: "Stop task", btnJumpEnd: "Jump to latest",
      btnAttach: "📷 Attach image|Add images/screenshots to the request (or paste with Ctrl+V).",
      btnVoice: "🎤 Voice dictation|Dictate in the selected interface language.",
      btnEnhance: "✨ Enhance prompt|Add technical context from the current project.",
      btnAutoEnhance: "Auto-✨ On send|Automatically enhance short requests when sending.",
      btnBusyMode: "Default action while running|Switch between queuing a message and steering in real time.",
      btnChatStyle: "Chat appearance|Detect code or prose automatically. Click to cycle: Auto → Code → Prose.",
      btnAltBusy: "Alternative action|Use the opposite action: steer now or queue a message.",
      btnSend: "Send request (Enter)|Send to Maestro, queue a message, or steer while running."
    };
    Object.keys(tips).forEach(function(id){
      var el = document.getElementById(id);
      ["title", "aria-label", "data-tip"].forEach(function(attr){
        if (!el.hasAttribute(attr)) return;
        var saved = "data-pt-" + attr;
        if (!el.hasAttribute(saved)) el.setAttribute(saved, el.getAttribute(attr));
        el.setAttribute(attr, ui(el.getAttribute(saved), tips[id]));
      });
    });
    applyChatStyle();
    syncHomeLanguage();
    var scroll = document.getElementById("chatScroll");
    var top = scroll.scrollTop, follow = scroll.__follow;
    if (_liveEl) { _liveEl.remove(); _liveEl = null; }
    renderChatList();
    renderMessages();
    syncBusyModeButton();
    renderQueueStrip();
    syncInstallButton();
    scroll.scrollTop = top; scroll.__follow = follow;
  }

  /* ---- MUNDOS VISUAIS (matrix / scifi / cyberpunk / hesperia / dune / dungen) ---- */
  var _rainRAF = null;
  function stopMatrixRain() {
    if (_rainRAF) { cancelAnimationFrame(_rainRAF); _rainRAF = null; }
    var cv = document.querySelector(".world-bg-canvas");
    if (cv) { var c = cv.getContext("2d"); c.clearRect(0, 0, cv.width, cv.height); }
  }
  function startMatrixRain() {
    var cv = document.querySelector(".world-bg-canvas");
    if (!cv) return;
    var ctx = cv.getContext("2d");
    function size() { cv.width = window.innerWidth; cv.height = window.innerHeight; }
    size();
    window.addEventListener("resize", size);
    var font = 15;
    var nCols = Math.max(1, Math.floor(cv.width / font));
    var drops = [];
    for (var i = 0; i < nCols; i++) drops.push(Math.random() * cv.height);
    var glyphs = "01アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモ".split("");
    function frame() {
      ctx.fillStyle = "rgba(2,8,4,0.13)";
      ctx.fillRect(0, 0, cv.width, cv.height);
      ctx.font = font + "px 'Share Tech Mono', monospace";
      for (var i = 0; i < drops.length; i++) {
        var ch = glyphs[(Math.random() * glyphs.length) | 0];
        ctx.fillStyle = Math.random() < 0.07 ? "#e6ffee" : "rgba(0,255,65,0.72)";
        ctx.fillText(ch, i * font, drops[i]);
        if (drops[i] > cv.height && Math.random() > 0.972) drops[i] = 0;
        drops[i] += font;
      }
      _rainRAF = requestAnimationFrame(frame);
    }
    if (_rainRAF) cancelAnimationFrame(_rainRAF);
    frame();
  }
  function applyWorld(name) {
    name = name || "";
    var root = document.documentElement;
    if (name) root.setAttribute("data-world", name);
    else root.removeAttribute("data-world");
    if (name === "matrix") startMatrixRain(); else stopMatrixRain();
    state.world = name;
    document.querySelectorAll("[data-world-pick]").forEach(function (b) {
      b.classList.toggle("active", (b.getAttribute("data-world-pick") || "") === name);
    });
  }
  function pickWorld(name) {
    applyWorld(name);
    var prefs = loadPrefs();
    prefs.world = name || "";
    savePrefs(prefs);
  }
  document.querySelectorAll("[data-world-pick]").forEach(function (b) {
    b.onclick = function () { pickWorld(b.getAttribute("data-world-pick")); };
  });
  var _wReset = document.getElementById("btnWorldReset");
  if (_wReset) _wReset.onclick = function () { pickWorld(""); };

  /* ---- MAESTRO WORLDS: homepage aberta pelo logótipo ---- */
  var HOME_WORLDS = [
    { id: "matrix", name: "Matrix", icon: "🟩", tag: "Chuva digital", tag_en: "Digital rain", desc: "Verde fósforo, scanlines e código a cair.", desc_en: "Phosphor green, scanlines and falling code." },
    { id: "scifi", name: "Sci-Fi HUD", icon: "🛰", tag: "Órbita", tag_en: "Orbit", desc: "Grelha orbital, radar e azul de instrumento.", desc_en: "Orbital grid, radar and instrument blue." },
    { id: "cyberpunk", name: "Cyberpunk", icon: "🌆", tag: "Neon 2099", tag_en: "Neon 2099", desc: "Rosa elétrico, amarelo ácido e horizonte em perspetiva.", desc_en: "Electric pink, acid yellow and a perspective horizon." },
    { id: "hesperia", name: "Hesperia", icon: "🏛", tag: "Civitas", tag_en: "Civitas", desc: "Mármore, ouro e geometria greco-romana.", desc_en: "Marble, gold and Greco-Roman geometry." },
    { id: "dune", name: "Dune · Arrakis", icon: "🏜", tag: "Deserto", tag_en: "Desert", desc: "Areia, especiaria e um planeta em rotação.", desc_en: "Sand, spice and a rotating planet." },
    { id: "dungen", name: "Hubert Dungen", icon: "🪨", tag: "Bone & sand", tag_en: "Bone & sand", desc: "Osso, carvão e a textura do portfólio.", desc_en: "Bone, charcoal and the portfolio texture." }
  ];
  var _homeRoot = null;

  function homeWorldName(id) {
    for (var i = 0; i < HOME_WORLDS.length; i++) if (HOME_WORLDS[i].id === id) return HOME_WORLDS[i].name;
    return state.lang === "en" ? "Default" : "Padrão";
  }
  function updateHomeActive() {
    var el = document.getElementById("mhActive");
    if (!el) return;
    var id = (_homeRoot && _homeRoot.getAttribute("data-home-world")) || state.world || "";
    el.textContent = homeWorldName(id);
  }
  function syncHomeLanguage() {
    var en = state.lang === "en";
    var t = document.getElementById("mhTitle");
    if (t) t.innerHTML = en ? "One studio.<br><em>Six worlds.</em>" : "Um estúdio.<br><em>Seis mundos.</em>";
    var lead = document.querySelector(".mh-lead");
    if (lead) lead.textContent = en
      ? "Maestro orchestrates your agents, files and services. Each world changes the light, type and effects of the whole app — pick one and step in."
      : "O Maestro orquestra os teus agentes, ficheiros e serviços. Cada mundo muda a luz, a tipografia e os efeitos da app inteira — escolhe um e entra.";
    var hint = document.querySelector(".mh-hint");
    if (hint) hint.textContent = en
      ? "Click a world to try it. Your choice is saved on this device."
      : "Clica num mundo para o experimentar. A escolha fica guardada neste dispositivo.";
    var enter = document.getElementById("btnHomeEnter");
    if (enter) enter.innerHTML = (en ? "Enter Studio" : "Entrar no Studio") + ' <span aria-hidden="true">→</span>';
    var start = document.getElementById("btnHomeStart");
    if (start) start.textContent = en ? "Enter Studio" : "Entrar no Studio";
    var install = document.getElementById("btnHomeInstall");
    if (install) install.textContent = en ? "📲 Install app" : "📲 Instalar app";
    var foot = document.querySelector(".mh-foot > span");
    if (foot) foot.textContent = en ? "✨ Backgrounds, type and animated effects per theme" : "✨ Fundos, tipografia e efeitos animados por tema";
    var wrap = document.getElementById("mhActiveWrap");
    if (wrap) wrap.innerHTML = (en ? "Active world" : "Mundo ativo") + ': <b id="mhActive">' + esc(homeWorldName(state.world || "")) + '</b>';
    renderHomeWorlds();
    updateHomeActive();
  }

  function renderHomeWorlds() {
    var box = document.getElementById("mhWorlds");
    if (!box) return;
    var current = (_homeRoot && _homeRoot.getAttribute("data-home-world")) || state.world || "";
    box.innerHTML = HOME_WORLDS.map(function(w){
      var on = w.id === current;
      var en = state.lang === "en";
      var tag = (en && w.tag_en) ? w.tag_en : w.tag;
      var desc = (en && w.desc_en) ? w.desc_en : w.desc;
      return '<button type="button" class="mh-world' + (on ? ' active' : '') + '" data-home-world="' + w.id + '" aria-pressed="' + (on ? 'true' : 'false') + '">' +
        '<span class="mh-art mh-art-' + w.id + '" aria-hidden="true"></span>' +
        '<span class="mh-meta"><span class="mh-name">' + w.icon + ' ' + esc(w.name) + '</span>' +
          '<span class="mh-tag">' + esc(tag) + '</span><span class="mh-desc">' + esc(desc) + '</span></span>' +
        '<span class="mh-active-chip" aria-hidden="true">' + (en ? "ACTIVE" : "ATIVO") + '</span>' +
      '</button>';
    }).join("");
    box.querySelectorAll("[data-home-world]").forEach(function(btn){
      btn.onclick = function(){
        var id = btn.getAttribute("data-home-world");
        pickWorld(id);
        if (_homeRoot) _homeRoot.setAttribute("data-home-world", id);
        box.querySelectorAll("[data-home-world]").forEach(function(other){
          var on = other.getAttribute("data-home-world") === id;
          other.classList.toggle("active", on);
          other.setAttribute("aria-pressed", on ? "true" : "false");
        });
        updateHomeActive();
        btn.focus();
      };
    });
  }
  function openMaestroHome() {
    _homeRoot = document.getElementById("maestroHome");
    if (!_homeRoot) return;
    var id = state.world || "";
    _homeRoot.setAttribute("data-home-world", id);
    _homeRoot.hidden = false;
    document.body.classList.add("home-open");
    renderHomeWorlds();
    updateHomeActive();
    var start = document.getElementById("btnHomeStart");
    if (start) setTimeout(function(){ start.focus(); }, 30);
  }
  function closeMaestroHome() {
    if (!_homeRoot) _homeRoot = document.getElementById("maestroHome");
    if (!_homeRoot) return;
    _homeRoot.hidden = true;
    document.body.classList.remove("home-open");
    var brand = document.getElementById("btnBrandHome");
    if (brand) brand.focus();
  }

  var _brandHome = document.getElementById("btnBrandHome");
  if (_brandHome) _brandHome.onclick = openMaestroHome;
  var _homeEnter = document.getElementById("btnHomeEnter");
  if (_homeEnter) _homeEnter.onclick = closeMaestroHome;
  var _homeStart = document.getElementById("btnHomeStart");
  if (_homeStart) _homeStart.onclick = closeMaestroHome;
  var _homeInstall = document.getElementById("btnHomeInstall");
  if (_homeInstall) _homeInstall.onclick = function(){
    closeMaestroHome();
    setTimeout(function(){ var b = document.getElementById("btnInstallPwa"); if (b) b.click(); }, 120);
  };
  /* Foco preso dentro da homepage enquanto estiver aberta. */
  document.addEventListener("keydown", function(e){
    if (e.key !== "Tab" || !_homeRoot || _homeRoot.hidden) return;
    var focusable = Array.prototype.slice.call(_homeRoot.querySelectorAll("button:not([disabled]), a[href], [tabindex]:not([tabindex='-1'])"));
    if (!focusable.length) return;
    var first = focusable[0], last = focusable[focusable.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });

  /* ---- PLACEHOLDER ADAPTATIVO DO COMPOSER ---- */
  function updateComposerPlaceholder() {
    var el = document.getElementById("promptInput");
    if (!el) return;
    var en = state.lang === "en";
    var chat = state.currentChat;
    var nMsgs = chat && chat.messages ? chat.messages.length : 0;
    var ph;
    if (isCurrentChatBusy()) {
      ph = state.activeJob.status === 'queued' ? (en ? "Queued… waiting to start" : "Em fila… a aguardar execução") : en ? "Working… type to queue or steer (Ctrl+Enter sends)"
              : "A trabalhar… escreve para meter em fila ou orientar (Ctrl+Enter envia)";
    } else if (nMsgs > 0) {
      var last = null;
      for (var i = chat.messages.length - 1; i >= 0; i--) {
        if (chat.messages[i].role === "user") { last = chat.messages[i].text || ""; break; }
      }
      var hint = (last || "")
        .replace(/\[[^\]]*\]/g, " ")
        .replace(/^[\s\u{1F300}-\u{1FAFF}\u2600-\u27BF•·\-–—>]+/u, "")
        .replace(/\s+/g, " ")
        .trim()
        .slice(0, 34);
      ph = en ? "Continue the conversation…" + (hint ? " (" + hint + "…)" : "") + " · Ctrl+Enter sends"
              : "Continua a conversa…" + (hint ? " (a propósito de «" + hint + "…»)" : "") + " · Ctrl+Enter envia";
    } else {
      ph = en ? "Tell Maestro what to do (e.g. 'download Dune', 'inspect C# script', 'Qidi status')…"
              : "Diz ao Maestro o que queres fazer (ex: 'mete o filme Dune a sacar', 'cria script C#', 'estado da Qidi')…";
    }
    /* O placeholder é só texto: remove ícones/emoji iniciais (⏳, 📥, 🧭, …). */
    ph = String(ph || "").replace(/^[\s\u{1F000}-\u{1FAFF}\u{2300}-\u{23FF}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}\u{FE0F}\u{200D}]+/u, "").trim();
    if (el.placeholder !== ph) el.placeholder = ph;
  }

  function openSettings() { document.getElementById("settingsModal").hidden = false; }
  function closeSettings() { document.getElementById("settingsModal").hidden = true; }

  /* ---- DETALHE POR AGENTE (o que faz agora + últimas tarefas) ---- */
  var AGENT_ENGINE = { agy: "agy", claude: "sonnet", codex: "codex", deepseek: "deepseek" };
  var AGENT_ENGINE_LABEL = { agy: "🧠 Gemini / Antigravity", claude: "🎭 Claude Sonnet", codex: "⚙️ Codex", deepseek: "🔮 DeepSeek V3.2" };

  function agentRecentTasks(f) {
    var out = [];
    var msgs = (state.currentChat && state.currentChat.messages) || [];
    var needle = String(f.name || "").toLowerCase().split(/[\s·]+/)[0];
    for (var i = msgs.length - 1; i >= 0 && out.length < 5; i--) {
      var m = msgs[i];
      if (m.role !== "assistant") continue;
      var used = String(m.agent_used || "").toLowerCase();
      if (needle && used.indexOf(needle) === -1) continue;
      out.push(m);
    }
    return out;
  }

  function openAgentDetail(key) {
    var st = state.status || {};
    var fleet = (st.fleet_summary || {}).fleet || st.fleet || [];
    var f = null;
    for (var i = 0; i < fleet.length; i++) if (fleet[i].key === key) f = fleet[i];
    if (!f) return;
    var engine = AGENT_ENGINE[f.key] || "auto";
    var body = document.getElementById("agentModalBody");
    document.getElementById("agentModalTitle").textContent = (f.icon || "🤖") + " " + f.name;

    var aj = state.activeJob;
    var html = '';
    html += '<div class="stat-card" style="margin:0">' +
      '<div class="stat-head"><span>' + esc(f.role || "") + '</span>' +
      '<span class="quota-chip ' + (f.available ? "ok" : "bad") + '">' + (f.available ? "LIVRE" : "ESGOTADO") + '</span></div>' +
      '<div class="stat-sub">' + esc(fleetQuotaLine(f.key) || f.quota_label || "—") + '</div></div>';

    var now = aj ? (aj.agent_label + " · " + (aj.current_step || "a trabalhar")) : "sem tarefa a correr agora";
    html += '<div class="stat-card" style="margin:0">' +
      '<div class="stat-head"><span>▶ A fazer agora</span></div>' +
      '<div class="stat-sub">' + esc(now) + '</div>' +
      (aj && aj.last_line ? '<div class="stat-sub" style="font-family:var(--mono);font-size:.64rem">↳ ' + esc(String(aj.last_line).slice(0, 160)) + '</div>' : '') +
      '</div>';

    var tasks = agentRecentTasks(f);
    html += '<div class="stat-card" style="margin:0"><div class="stat-head"><span>🕘 Últimas tarefas nesta conversa</span></div>';
    if (!tasks.length) {
      html += '<div class="stat-sub">Ainda sem tarefas deste agente nesta conversa.</div>';
    } else {
      tasks.forEach(function(m){
        var when = new Date((m.ts || 0) * 1000).toLocaleTimeString("pt-PT", { hour: "2-digit", minute: "2-digit" });
        var line = String(m.text || "").replace(/\s+/g, " ").slice(0, 110);
        html += '<div class="stat-sub" style="display:flex;gap:8px"><span style="color:var(--cyan);font-family:var(--mono)">' + when + '</span><span>' + esc(line) + '…</span></div>';
      });
    }
    html += '</div>';

    html += '<div style="display:flex;gap:8px;margin-top:4px">' +
      '<button type="button" class="card-btn" id="btnAgentUse" style="flex:1">Usar este motor</button></div>';
    body.innerHTML = html;
    document.getElementById("agentModal").hidden = false;

    document.getElementById("btnAgentUse").onclick = function(){
      document.getElementById("selEngine").value = engine;
      closeAgentDetail();
      notifyUser("🧠 Motor", "Passa a usar " + (AGENT_ENGINE_LABEL[f.key] || f.name) + " nas próximas mensagens.");
    };
  }
  function closeAgentDetail() { document.getElementById("agentModal").hidden = true; }

  document.addEventListener("click", function(ev){
    var t = ev.target.closest ? ev.target.closest(".fleet-card") : null;
    if (!t) return;
    ev.preventDefault(); ev.stopPropagation();
    var key = t.getAttribute("data-fleet-key") || "";
    if (key) openAgentDetail(key);
  }, true);

  var _bCloseAgent = document.getElementById("btnCloseAgentModal");
  if (_bCloseAgent) _bCloseAgent.onclick = closeAgentDetail;
  var _mAgent = document.getElementById("agentModal");
  if (_mAgent) _mAgent.onclick = function(e){ if (e.target === _mAgent) closeAgentDetail(); };
  document.getElementById("btnOpenSettings").onclick = openSettings;
  document.getElementById("btnHeaderSettings").onclick = openSettings;
  document.getElementById("btnCloseSettingsModal").onclick = closeSettings;
  document.getElementById("settingsModal").onclick = function(e){
    if (e.target === document.getElementById("settingsModal")) closeSettings();
  };

  document.getElementById("btnLangPt").onclick = function(){ applyLanguage("pt"); };
  document.getElementById("btnLangEn").onclick = function(){ applyLanguage("en"); };

  document.querySelectorAll("#iconStyleGrid [data-icostyle]").forEach(function(btn){
    btn.onclick = function(){ applyIconStyle(btn.getAttribute("data-icostyle")); };
  });

  document.querySelectorAll("#themePresetsGrid [data-preset]").forEach(function(btn){
    btn.onclick = function(){
      var p = THEME_PRESETS[btn.getAttribute("data-preset")];
      if (p) applyThemeColors(p);
    };
  });
  document.getElementById("btnResetTheme").onclick = function(){
    applyThemeColors(Object.assign({ scale: "15.5px" }, THEME_PRESETS.cyber));
  };
  document.getElementById("colCyan").oninput = function(e){ applyThemeColors({ cyan: e.target.value }); };
  document.getElementById("colViolet").oninput = function(e){ applyThemeColors({ violet: e.target.value }); };
  document.getElementById("colBg").oninput = function(e){ applyThemeColors({ bg: e.target.value }); };
  document.getElementById("colPanel").oninput = function(e){ applyThemeColors({ panel: e.target.value, card: e.target.value }); };
  document.querySelectorAll("[data-scale]").forEach(function(btn){
    btn.onclick = function(){
      var sc = btn.getAttribute("data-scale");
      applyThemeColors({ scale: sc });
      var sl = document.getElementById("uiScaleSlider");
      if (sl) sl.value = parseFloat(sc) || 15.5;
    };
  });
  var uiScaleSl = document.getElementById("uiScaleSlider");
  if (uiScaleSl) {
    uiScaleSl.oninput = function(e){ applyThemeColors({ scale: e.target.value + "px" }); };
  }

  /* ---- LIVE AUTO-UPDATER ---- */
  state.buildHash = null;
  state.autoUpdate = (loadPrefs().autoUpdate !== false);

  function syncAutoUpdateBtn() {
    var b = document.getElementById("btnToggleAutoUpdate");
    if (!b) return;
    b.classList.toggle("primary", state.autoUpdate);
    b.textContent = "🔄 Auto: " + (state.autoUpdate ? "ON" : "OFF");
  }
  syncAutoUpdateBtn();

  document.getElementById("btnToggleAutoUpdate").onclick = function(){
    state.autoUpdate = !state.autoUpdate;
    var prefs = loadPrefs();
    prefs.autoUpdate = state.autoUpdate;
    savePrefs(prefs);
    syncAutoUpdateBtn();
  };

  function isBusyNow() {
    var aj = state.activeJob;
    return !!(aj && (aj.status || "running") === "running");
  }

  var REFRESH_BTN_IDS = ["btnRefreshApp", "btnRefreshDrawer"];

  function setRefreshBusy(busy) {
    REFRESH_BTN_IDS.forEach(function(id){
      var b = document.getElementById(id);
      if (!b) return;
      b.classList.toggle("busy", !!busy);
      b.setAttribute("aria-busy", busy ? "true" : "false");
    });
    var ind = document.getElementById("pullRefresh");
    if (ind) ind.classList.toggle("loading", !!busy);
  }

  function markUpdateAvailable(hash) {
    var on = !!hash;
    REFRESH_BTN_IDS.forEach(function(id){
      var b = document.getElementById(id);
      if (!b) return;
      b.classList.toggle("has-update", on);
      if (id === "btnRefreshApp") {
        b.title = on ? ui("Nova versão #", "New version #") + hash.slice(0, 6) + ui(" — atualizar", " — update") : ui("Atualizar app", "Update app");
        b.setAttribute("aria-label", on ? ui("Nova versão disponível. Atualizar app", "New version available. Update app") : ui("Atualizar app", "Update app"));
      } else {
        b.setAttribute("aria-label", on
          ? ui("Nova versão disponível. Verificar e instalar atualização", "New version available. Check for and install the update")
          : ui("Verificar e instalar atualização da app", "Check for and install an app update"));
        var label = b.querySelector(".du-text");
        if (label) label.textContent = on ? ui("Nova versão · atualizar", "New version · update") : ui("Atualizar app", "Update app");
      }
    });
  }

  function syncVersionLabels(v) {
    if (!v || !v.hash) return;
    var vb = document.getElementById("versionBadge");
    if (vb) vb.textContent = "v" + v.version + " · #" + v.hash.slice(0, 6) + " (" + v.updated_str + ")";
    var dv = document.getElementById("drawerVersion");
    if (dv) dv.textContent = "v" + v.version + " · #" + v.hash.slice(0, 6);
    state.buildVersion = v.version;
  }

  function showUpdateToast(v, busyJob) {
    var toast = document.getElementById("updateToast");
    var toastTxt = document.getElementById("updateToastText");
    if (!toast || !toastTxt) return;
    toast.setAttribute("data-hash", v.hash);
    toastTxt.textContent = state.lang === "en"
      ? "New Maestro AI update (#" + v.hash.slice(0,6) + ") detected." + (busyJob ? " It will apply when the current run finishes." : "")
      : "Nova atualização do Maestro AI (#" + v.hash.slice(0,6) + ") detetada." + (busyJob ? " Aplica-se quando a execução atual terminar." : "");
    toast.hidden = false;
  }

  function performSeamlessReload(force) {
    if (state.reloading) return false;
    if (isBusyNow() && !force) {
      var ok = confirm(state.lang === "en"
        ? "A run is still in progress. Reloading now will interrupt its live view. Continue?"
        : "Há uma execução em curso. Atualizar agora interrompe o acompanhamento em direto. Continuar?");
      if (!ok) return false;
    }
    state.reloading = true;
    setRefreshBusy(true);
    var applyBtn = document.getElementById("btnApplyUpdateNow");
    if (applyBtn) { applyBtn.disabled = true; applyBtn.textContent = state.lang === "en" ? "Updating…" : "A atualizar…"; }
    var pullText = document.querySelector("#pullRefresh .pr-text");
    if (pullText) pullText.textContent = state.lang === "en" ? "Updating…" : "A atualizar…";
    persistDraft(true);
    try {
      sessionStorage.setItem("maestro_resume_state", JSON.stringify({
        mod: state.mod,
        chatId: state.currentChat ? state.currentChat.id : null,
        draft: document.getElementById("promptInput").value || ""
      }));
    } catch(e){}

    var reloaded = false;
    function reloadNow() {
      if (reloaded) return;
      reloaded = true;
      window.location.reload();
    }

    /* Limpar caches e pedir ao service worker que procure uma versão nova.
       A limpeza nunca pode bloquear a atualização: há um limite de tempo. */
    var jobs = [];
    if ("caches" in window) {
      jobs.push(caches.keys().then(function(names){
        return Promise.all(names.map(function(n){ return caches.delete(n); }));
      }));
    }
    if (navigator.serviceWorker && navigator.serviceWorker.getRegistrations) {
      jobs.push(navigator.serviceWorker.getRegistrations().then(function(regs){
        return Promise.all(regs.map(function(reg){
          try { return reg.update(); } catch (e) { return Promise.resolve(); }
        }));
      }).catch(function(){}));
    }
    var cleanup = Promise.all(jobs);
    // Nunca deixar a limpeza de cache bloquear a atualização.
    Promise.race([cleanup, new Promise(function(resolve){ setTimeout(resolve, 1500); })]).then(reloadNow, reloadNow);
    setTimeout(reloadNow, 2400);
    return true;
  }

  function requestAppRefresh() {
    if (state.reloading) return;
    if (isBusyNow()) {
      var ok = confirm(state.lang === "en"
        ? "A run is still in progress. Reloading now will interrupt its live view. Continue?"
        : "Há uma execução em curso. Atualizar agora interrompe o acompanhamento em direto. Continuar?");
      if (!ok) return;
    }
    setRefreshBusy(true);
    var pullText = document.querySelector("#pullRefresh .pr-text");
    if (pullText) pullText.textContent = state.lang === "en" ? "Checking…" : "A verificar…";
    var before = state.buildHash;
    api("/api/version").then(function(v){
      if (v && v.hash) {
        syncVersionLabels(v);
        if (before && v.hash !== before) {
          markUpdateAvailable(v.hash);
          showUpdateToast(v, false);
        }
      }
      setTimeout(function(){ performSeamlessReload(true); }, 240);
    }).catch(function(){
      // Sem servidor não há versão para comparar; recarregar mesmo assim.
      setTimeout(function(){ performSeamlessReload(true); }, 160);
    });
  }

  REFRESH_BTN_IDS.forEach(function(id){
    var b = document.getElementById(id);
    if (!b) return;
    b.onclick = requestAppRefresh;
  });

  document.getElementById("btnApplyUpdateNow").onclick = function(){
    performSeamlessReload(false);
  };

  document.getElementById("btnDismissUpdate").onclick = function(){
    var toast = document.getElementById("updateToast");
    if (toast) {
      toast.hidden = true;
      try { sessionStorage.setItem("maestro_update_dismissed", toast.getAttribute("data-hash") || ""); } catch(e){}
    }
    if (state.updateTimer) { clearTimeout(state.updateTimer); state.updateTimer = null; }
    // O ponto verde fica: continua a haver uma versão por instalar.
  };

  function checkServerVersion(manual) {
    return api("/api/version").then(function(v){
      if (!v || !v.hash) return;
      syncVersionLabels(v);
      if (!state.buildHash) {
        state.buildHash = v.hash;
        markUpdateAvailable("");
        if (manual) {
          notifyUser("🔄 Auto-Updater", state.lang === "en" ? "Maestro AI is up to date (" + v.hash.slice(0,6) + ")." : "Maestro AI está na versão mais recente (#" + v.hash.slice(0,6) + ").");
        }
        return;
      }
      if (v.hash !== state.buildHash) {
        var toast = document.getElementById("updateToast");
        var busyJob = isBusyNow();
        var busyTyping = (document.getElementById("promptInput").value || "").trim().length > 0 || document.getElementById("btnSend").disabled;
        var dismissed = "";
        try { dismissed = sessionStorage.getItem("maestro_update_dismissed") || ""; } catch(e){}
        markUpdateAvailable(v.hash);
        if (dismissed === v.hash) {
          // O utilizador já fechou este aviso; não voltar a abri-lo a cada 10s.
          if (state.autoUpdate && !busyTyping && !busyJob && !document.hidden) {
            state.buildHash = v.hash;
            state.updateTimer = setTimeout(performSeamlessReload, 1500);
          }
          return;
        }
        showUpdateToast(v, busyJob);
        var busy = busyTyping || busyJob;
        // NUNCA recarregar a página a meio de uma execução nem com a app em
        // segundo plano: isso cortava o scroll, o acompanhamento e o trabalho.
        if (state.autoUpdate && !busy && !document.hidden && !state.updateTimer) {
          state.buildHash = v.hash;
          state.updateTimer = setTimeout(function(){
            state.updateTimer = null;
            performSeamlessReload();
          }, 3000);
        }
      } else {
        markUpdateAvailable("");
        if (manual) {
          notifyUser("🔄 Auto-Updater", state.lang === "en" ? "Already on latest build (#" + v.hash.slice(0,6) + ")." : "Já estás na build mais recente (#" + v.hash.slice(0,6) + ").");
        }
      }
    }).catch(function(){});
  }

  document.getElementById("btnCheckUpdateNow").onclick = function(){ checkServerVersion(true); };

  /* Puxar para atualizar no topo da conversa (telemóvel/tablet).
     Fica longe das margens para não competir com as gavetas. */
  (function bindPullToRefresh() {
    var box = document.getElementById("chatScroll");
    var ind = document.getElementById("pullRefresh");
    if (!box || !ind) return;
    var startY = 0, startX = 0, dist = 0, pulling = false, active = false;
    var THRESHOLD = 64;
    function resetPull() {
      pulling = false; active = false; dist = 0;
      ind.classList.remove("visible", "ready");
      ind.style.transform = ""; ind.style.opacity = "";
      var t = ind.querySelector(".pr-text");
      if (t) t.textContent = state.lang === "en" ? "Pull to refresh" : "Puxa para atualizar";
    }
    box.addEventListener("touchstart", function(e){
      if (e.touches.length !== 1 || state.reloading || document.hidden) return;
      if (box.scrollTop > 0) return;
      if (document.querySelector(".mobile-open")) return;
      if (document.querySelector(".modal-backdrop:not([hidden])")) return;
      var t = e.touches[0];
      if (t.clientX < 30 || t.clientX > window.innerWidth - 30) return;
      startY = t.clientY; startX = t.clientX; pulling = true; active = false; dist = 0;
    }, { passive: true });
    box.addEventListener("touchmove", function(e){
      if (!pulling || e.touches.length !== 1) return;
      if (box.scrollTop > 0) { resetPull(); return; }
      var t = e.touches[0];
      var dy = t.clientY - startY;
      var dx = Math.abs(t.clientX - startX);
      if (dy <= 0 || dx > dy * 1.25) { if (!active) resetPull(); return; }
      if (dy > 8) { active = true; e.preventDefault(); }
      dist = Math.min(96, Math.max(0, (dy - 6) * 0.55));
      ind.classList.add("visible");
      ind.classList.toggle("ready", dist >= THRESHOLD);
      ind.style.transform = "translate(-50%, " + (4 + Math.min(38, dist * 0.34)) + "px)";
      ind.style.opacity = String(Math.min(1, dist / 42));
      var txt = ind.querySelector(".pr-text");
      if (txt) txt.textContent = dist >= THRESHOLD
        ? (state.lang === "en" ? "Release to refresh" : "Solta para atualizar")
        : (state.lang === "en" ? "Pull to refresh" : "Puxa para atualizar");
    }, { passive: false });
    function endPull() {
      if (!pulling) return;
      var go = dist >= THRESHOLD;
      if (go) {
        ind.classList.add("loading", "visible", "ready");
        ind.style.opacity = "1";
        var txt = ind.querySelector(".pr-text");
        if (txt) txt.textContent = state.lang === "en" ? "Updating…" : "A atualizar…";
        pulling = false;
        if (performSeamlessReload(false) === false) resetPull();
        return;
      }
      resetPull();
    }
    box.addEventListener("touchend", endPull, { passive: true });
    box.addEventListener("touchcancel", function(){ if (!pulling) return; resetPull(); }, { passive: true });
  })();

  /* Ao voltar à app (Android/iOS) verifica logo se há uma versão nova. */
  document.addEventListener("visibilitychange", function(){
    if (document.hidden) return;
    setTimeout(function(){ checkServerVersion(false); }, 300);
  });
  window.addEventListener("pageshow", function(e){ if (e.persisted) checkServerVersion(false); });
  window.addEventListener("online", function(){ checkServerVersion(false); });

  // Restore saved preferences on boot
  var initialPrefs = loadPrefs();
  if (initialPrefs.mod) setModule(initialPrefs.mod);
  applyWorld(initialPrefs.world || "");
  if (initialPrefs.theme) applyThemeColors(initialPrefs.theme);
  state.iconStyle = initialPrefs.iconStyle || "unicolor";
  applyLanguage(initialPrefs.lang || "pt");

  /* ---- ATALHOS DE TECLADO (Ctrl/Cmd) ---- */
  document.addEventListener("keydown", function(e){
    var mod = e.ctrlKey || e.metaKey;
    var k = (e.key || "").toLowerCase();
    if (e.key === "Escape") {
      if (_homeRoot && !_homeRoot.hidden) { closeMaestroHome(); return; }
      var opened = document.querySelectorAll(".modal-backdrop:not([hidden])");
      if (opened.length) { opened.forEach(function(m){ m.hidden = true; }); return; }
      var cv = document.getElementById("artifactCanvas");
      if (cv && !cv.hidden) { cv.hidden = true; return; }
      return;
    }
    if (!mod) return;
    if (k === "n") { e.preventDefault(); startNewChat(); return; }
    if (k === "k") { e.preventDefault(); document.getElementById("searchBox").focus(); return; }
    if (k === "b") { e.preventDefault(); document.getElementById("btnToggleLeft").click(); return; }
    if (k === "q") { e.preventDefault(); document.getElementById("btnToggleRight").click(); return; }
    if (k === "enter") { e.preventDefault(); handleComposerSubmit(); return; }
    if (["1","2","3","4","5"].indexOf(k) !== -1) {
      e.preventDefault();
      setModule(["chat","studio","media","casa","artifacts"][Number(k) - 1]);
    }
  });

  /* Match the actual message column, including native scrollbar space and zoom. */
  (function bindComposerColumn() {
    var stream = document.getElementById("chatScroll");
    var dock = document.querySelector(".composer-dock");
    var form = document.getElementById("composerForm");
    var pane = document.getElementById("paneChat");
    function align() {
      if (!stream.clientWidth || !dock.clientWidth) return;
      var cs = getComputedStyle(stream), ds = getComputedStyle(dock);
      var available = stream.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
      var width = Math.min(available, parseFloat(getComputedStyle(pane).getPropertyValue("--chat-column-width")));
      var left = stream.getBoundingClientRect().left + stream.clientLeft + parseFloat(cs.paddingLeft) + (available - width) / 2;
      var origin = dock.getBoundingClientRect().left + dock.clientLeft + parseFloat(ds.paddingLeft);
      form.style.setProperty("--composer-column-size", width + "px");
      form.style.setProperty("--composer-column-offset", (left - origin) + "px");
    }
    new ResizeObserver(align).observe(stream);
    new ResizeObserver(align).observe(dock);
    window.addEventListener("resize", align);
    align();
  })();

  /* ---- BOOT ---- */
  (function bindScrollMemory() {
    var box = document.getElementById("chatScroll");
    if (!box) return;
    box.addEventListener("scroll", function () {
      if (_scrollTimer) return;
      _scrollTimer = setTimeout(function () { _scrollTimer = null; saveScrollNow(); }, 350);
    }, { passive: true });
    window.addEventListener("beforeunload", saveScrollNow);
  })();

  /* Pinta o painel já com o estado "a ler" em vez de o deixar vazio. */
  renderInspector();

  checkServerVersion(false);
  setInterval(function(){ checkServerVersion(false); }, 10000);

  loadFolders().then(function(){
    loadStatusAndPulse();
    loadProjectInspector("nas");
    return refreshChats();
  }).then(function(){
    var resumed = null;
    try {
      resumed = JSON.parse(sessionStorage.getItem("maestro_resume_state") || "null");
      sessionStorage.removeItem("maestro_resume_state");
    } catch(e){}
    if (resumed && resumed.mod) setModule(resumed.mod);
    if (resumed && resumed.draft) document.getElementById("promptInput").value = resumed.draft;
    /* volta à conversa onde estavas (e não à mais recente) */
    var lastChat = null;
    try { lastChat = localStorage.getItem(LAST_CHAT_STORE); } catch(e){}
    var targetId = (resumed && resumed.chatId) || null;
    if (!targetId && lastChat && state.chats.some(function(c){ return c.id === lastChat; })) targetId = lastChat;
    if (targetId) return openChat(targetId);
    if (state.chats.length) openChat(state.chats[0].id);
    else renderMessages();
  });
  setInterval(loadStatusAndPulse, 10000);
  // Refresh all chat previews, including tasks running in another conversation.
  setInterval(function(){
    if (document.hidden) return;
    api("/api/chats").then(function(d){
      var chats = d.chats || [];
      var foldersChanged = d.folders && JSON.stringify(d.folders) !== JSON.stringify(state.folders);
      if (!foldersChanged && JSON.stringify(chats) === JSON.stringify(state.chats)) return;
      if (d.folders) state.folders = d.folders;
      state.chats = chats;
      renderFolderStripAndSelect();
      renderChatList();
    }).catch(function(){});
  }, 4000);
})();
</script>
</body>
</html>
"""

MANIFEST_JSON = {
    "id": "/",
    "name": "Maestro AI Studio · Pavilion-NAS",
    "short_name": "Maestro AI",
    "description": "Studio AI Orquestrador (NAS, Polistrat, SpaceBourne 2, Radarr/Sonarr, Home Assistant & Galeria AI)",
    "lang": "pt-PT",
    "dir": "ltr",
    "start_url": "/",
    "scope": "/",
    "display": "standalone",
    "display_override": ["standalone", "minimal-ui"],
    "background_color": "#050912",
    "theme_color": "#050912",
    "orientation": "any",
    "categories": ["productivity", "utilities"],
    "prefer_related_applications": False,
    "icons": [
        {"src": "/icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": "/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
        {"src": "/icons/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
    ],
}

SW_JS = r"""
const CACHE = "maestro-shell-v4";
const PRECACHE = [
  "/", "/manifest.json", "/icon.svg",
  "/icons/icon-192.png", "/icons/icon-512.png",
  "/icons/icon-maskable-512.png", "/icons/apple-touch-icon.png"
];

self.addEventListener("install", function(e) {
  e.waitUntil(
    caches.open(CACHE)
      .then(function(c) { return c.addAll(PRECACHE); })
      .catch(function() {})
      .then(function() { return self.skipWaiting(); })
  );
});

self.addEventListener("activate", function(e) {
  e.waitUntil(
    caches.keys().then(function(keys) {
      return Promise.all(keys.filter(function(k) { return k !== CACHE; })
        .map(function(k) { return caches.delete(k); }));
    }).then(function() { return self.clients.claim(); })
  );
});

self.addEventListener("fetch", function(e) {
  var req = e.request;
  if (req.method !== "GET") return;
  var url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.indexOf("/api/") === 0) return;
  if (url.pathname === "/sw.js") return;

  if (req.mode === "navigate") {
    e.respondWith(
      fetch(req, { cache: "no-store" }).then(function(res) {
        if (res && res.ok) {
          var copy = res.clone();
          caches.open(CACHE).then(function(c) { c.put("/", copy); }).catch(function() {});
        }
        return res;
      }).catch(function() { return caches.match("/"); })
    );
    return;
  }

  if (/\.(?:png|svg|ico|woff2?|css|js)$/i.test(url.pathname)) {
    e.respondWith(
      caches.match(req).then(function(hit) {
        if (hit) return hit;
        return fetch(req).then(function(res) {
          if (res && res.ok) {
            var copy = res.clone();
            caches.open(CACHE).then(function(c) { c.put(req, copy); }).catch(function() {});
          }
          return res;
        });
      })
    );
  }
});

self.addEventListener("message", function(e) {
  if (e.data && e.data.type === "SKIP_WAITING") self.skipWaiting();
});

self.addEventListener("notificationclick", function(e) {
  e.notification.close();
  e.waitUntil(clients.matchAll({ type: "window", includeUncontrolled: true }).then(function(list) {
    if (list.length > 0) return list[0].focus();
    return clients.openWindow("/");
  }));
});
"""


APP_HTML = APP_HTML.replace("</head>", "<style>" + WORKFLOW_UI.CSS + "</style></head>").replace("})();\n</script>", WORKFLOW_UI.JS + "\n})();\n</script>", 1)

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _reply(self, code: int, data, ctype: str = "application/json; charset=utf-8", extra_headers: dict | None = None) -> None:
        body = data if isinstance(data, bytes) else (data.encode("utf-8") if isinstance(data, str) else json.dumps(data, ensure_ascii=False).encode("utf-8"))
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        cache_control = "no-store"
        if extra_headers:
            for k, v in extra_headers.items():
                if k.lower() == "cache-control":
                    cache_control = str(v)
        self.send_header("Cache-Control", cache_control)
        if extra_headers:
            for k, v in extra_headers.items():
                if k.lower() == "cache-control":
                    continue
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self) -> None:
        # Installation clients may probe PWA metadata before downloading it.
        path = urlparse(self.path).path
        if path in (
            "/", "/index.html", "/manifest.json", "/sw.js", "/icon.svg",
            "/icons/icon-192.png", "/icons/icon-512.png",
            "/icons/icon-maskable-512.png", "/icons/apple-touch-icon.png",
            "/apple-touch-icon.png", "/icon-192.png", "/icon-512.png",
        ):
            return self.do_GET()
        return self._reply(405, b"Method not allowed", "text/plain; charset=utf-8", {"Allow": "GET, POST"})

    def do_GET(self) -> None:
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            return self._reply(200, APP_HTML, "text/html; charset=utf-8")
        if u.path == "/manifest.json":
            return self._reply(200, MANIFEST_JSON, "application/manifest+json; charset=utf-8")
        if u.path == "/sw.js":
            return self._reply(200, SW_JS, "application/javascript; charset=utf-8")
        if u.path == "/icon.svg":
            svg = SVG_PATH.read_bytes() if SVG_PATH.exists() else b"<svg xmlns='http://www.w3.org/2000/svg'/>"
            return self._reply(200, svg, "image/svg+xml")
        icon_files = {
            "/icons/icon-192.png": ICON_192_PATH,
            "/icons/icon-512.png": ICON_512_PATH,
            "/icons/icon-maskable-512.png": ICON_MASKABLE_PATH,
            "/icons/apple-touch-icon.png": APPLE_TOUCH_PATH,
            "/icon-192.png": ICON_192_PATH,
            "/icon-512.png": ICON_512_PATH,
            "/apple-touch-icon.png": APPLE_TOUCH_PATH,
        }
        if u.path in icon_files:
            icon_path = icon_files[u.path]
            if icon_path.exists():
                return self._reply(
                    200, icon_path.read_bytes(), "image/png",
                    {"Cache-Control": "public, max-age=604800, immutable"},
                )
            return self._reply(404, b"icon not found", "text/plain; charset=utf-8")
        if u.path == "/maestro.ico":
            ensure_all_dirs()
            return self._reply(200, ICO_PATH.read_bytes(), "image/x-icon")
        if u.path == "/Maestro-AI.exe":
            ensure_all_dirs()
            return self._reply(
                200,
                EXE_PATH.read_bytes(),
                "application/vnd.microsoft.portable-executable",
                {"Content-Disposition": 'attachment; filename="Maestro-AI.exe"'},
            )
        if u.path == "/api/version":
            return self._reply(200, get_build_info())
        if u.path == "/api/models":
            return self._reply(200, MODELS.catalog(quotas={"claude": M.get_claude_quota(), "codex": M.get_codex_quota(), "antigravity": M.get_antigravity_status(), "deepseek": M.get_deepseek_balance()}))
        if u.path == "/api/status":
            with JOBS_LOCK:
                jobs_copy = dict(ACTIVE_JOBS)
            return self._reply(200, {
                "active_maestro": M.choose_active_maestro(),
                "claude": M.get_claude_quota(),
                "codex": M.get_codex_quota(),
                "deepseek": M.get_deepseek_balance(),
                "antigravity": M.get_antigravity_status(),
                "fleet_summary": M.get_agent_fleet_summary(jobs_copy),
            })
        if u.path == "/api/folders":
            return self._reply(200, {"folders": F.get_folders()})
        if u.path == "/api/artifacts":
            return self._reply(200, {"artifacts": F.get_artifacts()})
        if u.path == "/api/gallery":
            return self._reply(200, {"items": F.list_gallery()})
        if u.path == "/api/media/search":
            qs = parse_qs(u.query)
            q = (qs.get("q") or [""])[0]
            kind = (qs.get("kind") or ["movie"])[0]
            return self._reply(200, F.search_media(q, kind))
        if u.path == "/api/media/queue":
            return self._reply(200, F.get_media_queue())
        if u.path == "/api/containers":
            return self._reply(200, {"containers": F.list_containers()})
        if u.path == "/api/ha/dashboard":
            return self._reply(200, F.get_ha_dashboard())
        if u.path == "/api/schedules":
            return self._reply(200, {"schedules": F.get_schedules()})
        if u.path == "/api/chats":
            return self._reply(200, {"chats": list_chats(), "folders": F.get_folders()})
        if u.path == "/api/handover":
            ho = read_handover()
            if ho:
                try:
                    ho["age_s"] = round(time.time() - float(ho.get("ts") or 0), 1)
                except Exception:
                    ho["age_s"] = None
            return self._reply(200, {"handover": ho})

        if u.path == "/api/handover/clear":
            clear_handover()
            return self._reply(200, {"ok": True})

        if u.path == "/api/chat":
            cid = (parse_qs(u.query).get("id") or [""])[0]
            chat = load_chat(cid)
            if not chat:
                return self._reply(404, {"error": "Conversa não encontrada"})
            return self._reply(200, {"chat": chat})
        if u.path == "/api/chat/job":
            cid = (parse_qs(u.query).get("id") or [""])[0]
            return self._reply(200, chat_activity_snapshot(cid))
        if u.path == "/api/pulse":
            if PULSE_JSON.exists():
                return self._reply(200, PULSE_JSON.read_bytes(), "application/json; charset=utf-8")
            return self._reply(200, {"items": []})
        if u.path == "/api/project/inspect":
            proj = (parse_qs(u.query).get("project") or ["nas"])[0]
            return self._reply(200, inspect_project(proj))
        if u.path == "/api/project/file":
            fp = (parse_qs(u.query).get("path") or [""])[0]
            return self._reply(200, read_project_file(fp))
        if u.path == "/api/image":
            img_p = (parse_qs(u.query).get("path") or [""])[0]
            allowed = (
                str(PASTES_DIR),
                str(F.GALLERY_DIR),
                "/home/hubert",
                "/srv/appdata/0_SERVER/2_CONFIG/homepage/images",
                "/srv/appdata/0_SERVER/1_STACKS/cyberstate/repo/public",
            )
            mime_map = {
                ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".webp": "image/webp", ".gif": "image/gif", ".avif": "image/avif",
                ".bmp": "image/bmp", ".ico": "image/x-icon", ".svg": "image/svg+xml",
                ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf", ".otf": "font/otf",
            }
            try:
                real = os.path.realpath(img_p) if img_p else ""
            except (OSError, ValueError):
                real = ""
            ext = os.path.splitext(real)[1].lower()
            mime = mime_map.get(ext)

            def _inside(path: str, root: str) -> bool:
                try:
                    return os.path.commonpath([path, os.path.realpath(root)]) == os.path.realpath(root)
                except (ValueError, OSError):
                    return False

            # Extensões de imagem/fonte + raiz real. Sem isto, /api/image servia
            # QUALQUER ficheiro legível em /home/hubert (chaves SSH, .env, etc.).
            if real and mime and os.path.isfile(real) and any(_inside(real, root) for root in allowed):
                headers = {"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox"} if ext == ".svg" else None
                return self._reply(200, pathlib.Path(real).read_bytes(), mime, headers)
            return self._reply(404, {"error": "Imagem não encontrada"})
        self._reply(404, {"error": "Não encontrado"})

    def do_POST(self) -> None:
        u = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(min(length, 15 * 1024 * 1024)) if length > 0 else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            body = {}

        if u.path.startswith("/api/actions/"):
            # Custom header forces a preflight for cross-origin callers. No CORS
            # permission is granted; reject ordinary cross-site form submissions.
            origin = self.headers.get("Origin")
            if (self.headers.get("X-Maestro-Action") != "1"
                    or self.headers.get("Sec-Fetch-Site") == "cross-site"
                    or (origin and urlparse(origin).netloc != self.headers.get("Host"))):
                return self._reply(403, {"error": "Pedido de autorização fora da aplicação"})
            try:
                if u.path == "/api/actions/preview":
                    chat = load_chat(str(body.get("chat_id") or ""))
                    if not chat:
                        raise ValueError("Conversa não encontrada")
                    cwd = (get_folder_map().get(chat.get("project")) or {}).get("cwd") or str(HOME)
                    return self._reply(200, ACTIONS.preview(chat, body.get("code") or "", cwd))
                if u.path == "/api/actions/status":
                    return self._reply(200, ACTIONS.status(body.get("action_id")))
                if u.path == "/api/actions/decide":
                    if not isinstance(body.get("approve"), bool):
                        raise ValueError("Decisão inválida")
                    return self._reply(200, ACTIONS.decide(body.get("action_id"), body["approve"]))
                return self._reply(404, {"error": "Ação desconhecida"})
            except (ValueError, OSError) as exc:
                return self._reply(400, {"error": str(exc)})

        if u.path == "/api/enhance":
            return self._reply(200, enhance_prompt(str(body.get("text") or ""), str(body.get("project") or "nas")))

        if u.path == "/api/folders/save":
            return self._reply(200, {"folders": F.save_folder(body)})

        if u.path == "/api/artifacts/delete":
            return self._reply(200, {"artifacts": F.delete_artifact(str(body.get("id") or ""))})

        if u.path == "/api/gallery/delete":
            return self._reply(200, F.delete_gallery_item(str(body.get("path") or "")))

        if u.path == "/api/media/generate-image":
            return self._reply(200, F.generate_ai_image(str(body.get("prompt") or "Maestro AI"), str(body.get("aspect") or "1:1")))

        if u.path == "/api/media/generate-gif":
            return self._reply(200, F.generate_animated_gif(str(body.get("prompt") or "Cyber pulse")))

        if u.path == "/api/media/request":
            return self._reply(200, F.request_download_media(
                str(body.get("kind") or "movie"),
                int(body.get("id") or 0),
                str(body.get("title") or ""),
            ))

        if u.path == "/api/container/action":
            return self._reply(200, F.container_action(str(body.get("name") or ""), str(body.get("action") or "logs")))

        if u.path == "/api/ha/action":
            return self._reply(200, F.trigger_ha_action(str(body.get("entity_id") or ""), str(body.get("action") or "toggle")))

        if u.path == "/api/schedules/save":
            return self._reply(200, {"schedules": F.save_schedule(body)})

        if u.path == "/api/schedules/toggle":
            return self._reply(200, {"schedules": F.toggle_or_delete_schedule(str(body.get("id") or ""), bool(body.get("delete", False)))})

        if u.path == "/api/schedules/run":
            return self._reply(200, F.run_schedule_now(str(body.get("id") or "")))

        if u.path == "/api/chat/new":
            chat = create_chat(body.get("project") or "nas", body.get("engine") or "auto")
            return self._reply(200, {"chat": chat})

        if u.path == "/api/chat/pin":
            chat = load_chat(str(body.get("id") or ""))
            if chat:
                chat["pinned"] = not bool(chat.get("pinned", False))
                save_chat(chat)
            return self._reply(200, {"ok": True})

        if u.path == "/api/chat/folder":
            folder = str(body.get("folder") or "")
            if folder != 'auto' and folder not in get_folder_map():
                return self._reply(400, {"error": "Unknown folder"})
            with CHAT_STORAGE_LOCK:
                chat = load_chat(str(body.get("id") or ""))
                if not chat:
                    return self._reply(404, {"error": "Chat not found"})
                chat['folder_source'] = 'auto' if folder == 'auto' else 'manual'
                if folder != 'auto':
                    chat['folder_id'] = folder
                path = CHATS_DIR / (chat['id'] + '.json')
                tmp = path.with_suffix('.json.organizing')
                tmp.write_text(json.dumps(chat, ensure_ascii=False, indent=2), encoding='utf-8')
                tmp.replace(path)
            return self._reply(200, {"ok": True})

        if u.path == "/api/chat/delete":
            clean_id = re.sub(r"[^a-zA-Z0-9_-]", "", str(body.get("id") or ""))
            (CHATS_DIR / f"{clean_id}.json").unlink(missing_ok=True)
            return self._reply(200, {"ok": True})

        if u.path == "/api/cleanup":
            return self._reply(200, M.cleanup_resources(verbose=False))

        if u.path == "/api/upload":
            data_url = str(body.get("data_url") or "")
            if "," in data_url:
                header, b64 = data_url.split(",", 1)
                ext = "png"
                if "jpeg" in header or "jpg" in header:
                    ext = "jpg"
                elif "webp" in header:
                    ext = "webp"
                data = base64.b64decode(b64)
                ensure_all_dirs()
                fname = f"paste-{time.strftime('%Y%m%d-%H%M%S')}-{os.urandom(3).hex()}.{ext}"
                fpath = PASTES_DIR / fname
                fpath.write_bytes(data)
                fpath.chmod(0o644)
                return self._reply(200, {"path": str(fpath)})
            return self._reply(400, {"error": "Upload inválido"})

        if u.path == "/api/paste-file":
            text = str(body.get("text") or "")
            if not text.strip():
                return self._reply(400, {"error": "Texto vazio"})
            if len(text) > 2_000_000:
                return self._reply(413, {"error": "Texto demasiado grande (máx. 2 MB)"})
            raw_name = str(body.get("name") or "colado.md")
            name = re.sub(r"[^A-Za-z0-9._-]", "-", raw_name)[:60] or "colado.md"
            ensure_all_dirs()
            fpath = DOCS_DIR / f"colado-{time.strftime('%Y%m%d-%H%M%S')}-{os.urandom(3).hex()}-{name}"
            fpath.write_text(text, encoding="utf-8")
            fpath.chmod(0o644)
            stripped = text.strip()
            return self._reply(200, {
                "path": str(fpath),
                "name": name,
                "chars": len(text),
                "lines": text.count("\n") + 1,
                "preview": (stripped.splitlines()[0][:120] if stripped else ""),
            })

        if u.path == "/api/chat/stop":
            cid = str(body.get("id") or "")
            stopped = stop_chat_job(cid, reason="Parado pelo utilizador")
            return self._reply(200, {"ok": True, "stopped": stopped})

        if u.path == "/api/chat/queue/cancel":
            task_id = str(body.get("task_id") or "")
            with TURN_QUEUE.lock:
                task = next((t for t in TURN_QUEUE.tasks if t['id'] == task_id), None)
                if task and task['status'] in ('queued', 'interrupted'):
                    TURN_QUEUE.update(task_id, status='cancelled')
            return self._reply(200, {"ok": True})

        if u.path == "/api/chat/queue/retry":
            with TURN_QUEUE.lock:
                task = next((t for t in TURN_QUEUE.tasks if t['id'] == body.get('task_id')), None)
                if task and task['status'] == 'interrupted':
                    TURN_QUEUE.update(task['id'], status='queued', reason='evaluating')
            return self._reply(200, {"ok": True})

        if u.path == "/api/chat/send":
            if not body.get("steer"):
                try:
                    return self._reply(200, enqueue_turn(body))
                except ValueError as exc:
                    return self._reply(400, {"error": str(exc)})
            try:
                return self._reply(200, dispatch_turn(body))
            except ValueError as exc:
                return self._reply(400, {"error": str(exc)})

        self._reply(404, {"error": "Endpoint não existe"})


def main() -> None:
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8109
    ensure_all_dirs()
    TURN_QUEUE.recover()
    F.start_scheduler_daemon()
    start_chat_organizer()
    start_turn_scheduler()
    start_hot_reload_watcher()
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    srv.daemon_threads = True
    print(f"Maestro AI Studio 3.0 a escutar em 0.0.0.0:{port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
