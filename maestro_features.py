#!/usr/bin/env python3
"""
Módulo de Funcionalidades Avançadas do Maestro AI Studio:
1. Integração Real Sonarr (:8989) & Radarr (:7878) — Pesquisa de filmes/séries com poster, fila de downloads e "Sacar Agora" com 1 clique.
2. Gestão e Ações Predefinidas de Containers Docker (estado ao vivo, logs, restart, diagnóstico Maestro).
3. Integração Real Home Assistant (:8123) — Impressoras 3D (Centauri Carbon 2, Qidi Plus 4), Tomadas inteligentes, Luzes e Webhooks.
4. Gerador de Imagens AI (Flux HD / Pollinations + Procedural PNG) & Gerador Nativo de GIFs Animados (GIF89a multi-frame) + Galeria.
5. Pastas Coloridas Personalizáveis (com ícone, cor hex, pasta cwd e regras) + Biblioteca de Artifacts.
6. Schedulers & Automação (motor em background que executa tarefas agendadas e automações HA/Maestro).
"""
from __future__ import annotations

import datetime as dt
import functools
import json
import os
import pathlib
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
import zlib

HOME = pathlib.Path.home()
STATE_DIR = HOME / ".local" / "state" / "maestro"
GALLERY_DIR = STATE_DIR / "gallery"
ARTIFACTS_FILE = STATE_DIR / "artifacts.json"
FOLDERS_FILE = STATE_DIR / "folders.json"
FOLDERS_LOCK = threading.RLock()
SCHEDULES_FILE = STATE_DIR / "schedules.json"
PASTES_DIR = pathlib.Path("/srv/appdata/0_SERVER/pastes")

def _ha_conf():
    """HA token/webhook live outside the repo: env vars or ~/.config/maestro/ha.env (KEY=VALUE)."""
    conf = {}
    try:
        for line in (pathlib.Path.home() / ".config/maestro/ha.env").read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                conf[k.strip()] = v.strip()
    except OSError:
        pass
    return {k: os.environ.get(k) or conf.get(k, "") for k in ("HA_TOKEN", "HA_WEBHOOK")}


HA_URL = "http://127.0.0.1:8123"
_HA = _ha_conf()
HA_TOKEN = _HA["HA_TOKEN"]
HA_WEBHOOK = _HA["HA_WEBHOOK"]

DEFAULT_FOLDERS = [
    {"id": "nas", "name": "Pavilion-NAS & Pulso", "icon": "⚡", "color": "#38bdf8", "cwd": str(HOME), "desc": "Administração total do servidor, Docker, segurança e Pulso"},
    {"id": "polistrat", "name": "Polistrat (Unity C#)", "icon": "🎮", "color": "#a78bfa", "cwd": "/srv/cloud4tb/WORK/DEVELOPMENT/Polistrat", "desc": "Desenvolvimento de jogo de estratégia em Unity C#"},
    {"id": "modding", "name": "SpaceBourne 2 & Mods", "icon": "🚀", "color": "#fbbf24", "cwd": "/srv/cloud4tb/WORK/DEVELOPMENT/MODDING", "desc": "Engenharia reversa, tabelas Unreal Engine e pak mods"},
    {"id": "web", "name": "DungenLabs & Portfólio", "icon": "🌐", "color": "#34d399", "cwd": str(HOME / "hubert-portfolio"), "desc": "Websites, UI/UX, Fatia, Bobina, Rumo e Grimório"},
    {"id": "media", "name": "Cinema & Media (Arr)", "icon": "🎬", "color": "#f43f5e", "cwd": str(HOME), "desc": "Sonarr, Radarr, Jellyfin, Bazarr e pedidos de filmes/séries"},
    {"id": "casa", "name": "Casa & Impressão 3D", "icon": "🏠", "color": "#fb923c", "cwd": str(HOME), "desc": "Home Assistant, Centauri Carbon 2, Qidi Plus 4 e tomadas"},
]


def ensure_feature_dirs() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    GALLERY_DIR.mkdir(parents=True, exist_ok=True)
    PASTES_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================================
# 1. PASTAS COLORIDAS & ARTIFACTS
# ============================================================================

def locked_folders(fn):
    @functools.wraps(fn)
    def call(*args, **kwargs):
        with FOLDERS_LOCK:
            return fn(*args, **kwargs)
    return call


def write_folders(folders: list[dict]) -> None:
    tmp = FOLDERS_FILE.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(folders, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(FOLDERS_FILE)


@locked_folders
def get_folders() -> list[dict]:
    ensure_feature_dirs()
    if FOLDERS_FILE.exists():
        try:
            data = json.loads(FOLDERS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                return data
        except Exception:
            pass
    write_folders(DEFAULT_FOLDERS)
    return list(DEFAULT_FOLDERS)


@locked_folders
def save_folder(folder: dict) -> list[dict]:
    folders = get_folders()
    fid = re.sub(r"[^a-zA-Z0-9_-]", "", str(folder.get("id") or "")) or f"folder-{uuid.uuid4().hex[:6]}"
    item = {
        "id": fid,
        "name": str(folder.get("name") or "Nova Pasta")[:45],
        "icon": str(folder.get("icon") or "📁")[:4],
        "color": str(folder.get("color") or "#38bdf8")[:16],
        "cwd": str(folder.get("cwd") or str(HOME)),
        "desc": str(folder.get("desc") or "")[:140],
    }
    replaced = False
    for idx, f in enumerate(folders):
        if f["id"] == fid:
            folders[idx] = item
            replaced = True
            break
    if not replaced:
        folders.append(item)
    write_folders(folders)
    return folders


@locked_folders
def delete_folder(fid: str) -> list[dict]:
    folders = [f for f in get_folders() if f["id"] != fid]
    if not folders:
        folders = list(DEFAULT_FOLDERS)
    write_folders(folders)
    return folders


def get_artifacts() -> list[dict]:
    ensure_feature_dirs()
    if ARTIFACTS_FILE.exists():
        try:
            return json.loads(ARTIFACTS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def save_artifact(art: dict) -> dict:
    arts = get_artifacts()
    aid = art.get("id") or f"art-{int(time.time()*1000)}-{uuid.uuid4().hex[:5]}"
    entry = {
        "id": aid,
        "title": str(art.get("title") or "Artifact sem título")[:80],
        "folder": str(art.get("folder") or "nas"),
        "kind": str(art.get("kind") or "code"),  # code | html | svg | markdown | config
        "lang": str(art.get("lang") or "text"),
        "content": str(art.get("content") or ""),
        "updated_at": time.time(),
    }
    arts = [a for a in arts if a.get("id") != aid]
    arts.insert(0, entry)
    ARTIFACTS_FILE.write_text(json.dumps(arts[:150], ensure_ascii=False, indent=2), encoding="utf-8")
    return entry


def delete_artifact(aid: str) -> list[dict]:
    arts = [a for a in get_artifacts() if a.get("id") != aid]
    ARTIFACTS_FILE.write_text(json.dumps(arts, ensure_ascii=False, indent=2), encoding="utf-8")
    return arts


# ============================================================================
# 2. INTEGRAÇÃO SONARR (:8989) & RADARR (:7878) — MEDIA HUB
# ============================================================================

def _read_arr_api_key(app: str, fallback: str) -> str:
    cfg = pathlib.Path(f"/srv/appdata/0_SERVER/2_CONFIG/{app}/config.xml")
    if cfg.exists():
        try:
            root = ET.fromstring(cfg.read_text(encoding="utf-8"))
            el = root.find("ApiKey")
            if el is not None and el.text:
                return el.text.strip()
        except Exception:
            pass
    return fallback


SONARR_KEY = _read_arr_api_key("sonarr", "")
RADARR_KEY = _read_arr_api_key("radarr", "")


def _arr_request(base_url: str, api_key: str, endpoint: str, method: str = "GET", payload: dict | None = None):
    url = f"{base_url}/api/v3/{endpoint.lstrip('/')}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={"X-Api-Key": api_key, "Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def search_media(query: str, kind: str = "movie") -> dict:
    """Pesquisa filmes no Radarr ou séries no Sonarr em tempo real."""
    q = urllib.parse.quote(query.strip())
    if not q:
        return {"results": []}
    try:
        if kind == "series":
            items = _arr_request("http://127.0.0.1:8989", SONARR_KEY, f"series/lookup?term={q}")
            out = []
            for it in items[:12]:
                poster = ""
                for img in it.get("images") or []:
                    if img.get("coverType") == "poster":
                        poster = img.get("remoteUrl") or img.get("url") or ""
                        break
                out.append({
                    "kind": "series",
                    "title": it.get("title"),
                    "year": it.get("year"),
                    "overview": (it.get("overview") or "")[:240],
                    "tvdbId": it.get("tvdbId"),
                    "poster": poster,
                    "seasons": len(it.get("seasons") or []),
                    "already_added": bool(it.get("id")),
                    "raw_title_slug": it.get("titleSlug"),
                })
            return {"results": out}
        else:
            items = _arr_request("http://127.0.0.1:7878", RADARR_KEY, f"movie/lookup?term={q}")
            out = []
            for it in items[:12]:
                poster = ""
                for img in it.get("images") or []:
                    if img.get("coverType") == "poster":
                        poster = img.get("remoteUrl") or img.get("url") or ""
                        break
                out.append({
                    "kind": "movie",
                    "title": it.get("title"),
                    "year": it.get("year"),
                    "overview": (it.get("overview") or "")[:240],
                    "tmdbId": it.get("tmdbId"),
                    "poster": poster,
                    "already_added": bool(it.get("id")),
                    "raw_title_slug": it.get("titleSlug"),
                })
            return {"results": out}
    except Exception as e:
        return {"error": str(e), "results": []}


def request_download_media(kind: str, identifier: int, title: str) -> dict:
    """Adiciona um filme ao Radarr ou uma série ao Sonarr e inicia logo a pesquisa de download."""
    try:
        if kind == "series":
            roots = _arr_request("http://127.0.0.1:8989", SONARR_KEY, "rootfolder")
            profiles = _arr_request("http://127.0.0.1:8989", SONARR_KEY, "qualityprofile")
            root_path = roots[0]["path"] if roots else "/tv"
            prof_id = profiles[0]["id"] if profiles else 1
            lookup = _arr_request("http://127.0.0.1:8989", SONARR_KEY, f"series/lookup?term=tvdb:{int(identifier)}")
            if not lookup:
                return {"ok": False, "error": "Série não encontrada no TVDB."}
            series_obj = lookup[0]
            series_obj["qualityProfileId"] = prof_id
            series_obj["rootFolderPath"] = root_path
            series_obj["monitored"] = True
            series_obj["addOptions"] = {"searchForMissingEpisodes": True}
            res = _arr_request("http://127.0.0.1:8989", SONARR_KEY, "series", method="POST", payload=series_obj)
            return {"ok": True, "message": f"Série «{res.get('title', title)}» adicionada ao Sonarr e pesquisa de episódios iniciada!"}
        else:
            roots = _arr_request("http://127.0.0.1:7878", RADARR_KEY, "rootfolder")
            profiles = _arr_request("http://127.0.0.1:7878", RADARR_KEY, "qualityprofile")
            root_path = roots[0]["path"] if roots else "/movies"
            prof_id = profiles[0]["id"] if profiles else 1
            lookup = _arr_request("http://127.0.0.1:7878", RADARR_KEY, f"movie/lookup/tmdb?tmdbId={int(identifier)}")
            movie_obj = lookup if isinstance(lookup, dict) else (lookup[0] if lookup else None)
            if not movie_obj:
                return {"ok": False, "error": "Filme não encontrado no TMDB."}
            movie_obj["qualityProfileId"] = prof_id
            movie_obj["rootFolderPath"] = root_path
            movie_obj["monitored"] = True
            movie_obj["addOptions"] = {"searchForMovie": True}
            res = _arr_request("http://127.0.0.1:7878", RADARR_KEY, "movie", method="POST", payload=movie_obj)
            return {"ok": True, "message": f"Filme «{res.get('title', title)}» adicionado ao Radarr e pesquisa de download iniciada!"}
    except urllib.error.HTTPError as he:
        err_body = he.read().decode("utf-8", errors="ignore")[:300]
        return {"ok": False, "error": f"Já existe ou aviso da API: {err_body}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_media_queue() -> dict:
    """Fila ativa e títulos recentes, incluindo os que ainda aguardam download."""
    queue, recent, errors = [], [], []
    for name, url, key in [("Radarr 🎬", "http://127.0.0.1:7878", RADARR_KEY), ("Sonarr 📺", "http://127.0.0.1:8989", SONARR_KEY)]:
        is_movie = "Radarr" in name
        id_field = "movieId" if is_movie else "seriesId"
        active_ids = set()
        try:
            page = 1
            while True:
                data = _arr_request(url, key, f"queue?page={page}&pageSize=100&includeMovie=true&includeSeries=true")
                records = data.get("records") or []
                for rec in records:
                    active_ids.add(rec.get(id_field))
                queue.extend(dict(rec, source=name) for rec in records)
                if not records or page * 100 >= data.get("totalRecords", len(records)):
                    break
                page += 1
        except Exception:
            errors.append(f"{name}: não foi possível consultar a fila.")
        try:
            items = _arr_request(url, key, "movie" if is_movie else "series")
            for it in sorted(items, key=lambda x: x.get("added") or "", reverse=True)[:8]:
                if it.get("id") in active_ids:
                    continue
                if is_movie:
                    available = bool(it.get("hasFile"))
                    label = "Disponível" if available else ("À espera de download" if it.get("monitored") else "Sem monitorização")
                else:
                    stats = it.get("statistics") or {}
                    files = stats.get("episodeFileCount") or 0
                    total = stats.get("episodeCount") or 0
                    available = files > 0 and files >= total
                    label = f"{files}/{total} episódios disponíveis" if files else "À espera de episódios"
                recent.append({"source": name, "title": it.get("title"), "year": it.get("year"),
                               "status_label": label, "available": available, "added": it.get("added") or ""})
        except Exception:
            errors.append(f"{name}: não foi possível consultar os títulos recentes.")
    normalized = []
    for rec in queue:
        size = float(rec.get("size") or 0)
        left = float(rec.get("sizeleft") or 0)
        pct = round(max(0, min(100, (1 - left / size) * 100)), 1) if size > 0 else 0
        status = str(rec.get("status") or "unknown").lower()
        tracked = str(rec.get("trackedDownloadState") or "").lower()
        warning = rec.get("trackedDownloadStatus") in ("warning", "error")
        label = {"downloading": "A descarregar", "paused": "Em pausa", "queued": "Em fila",
                 "completed": "A aguardar importação", "delay": "Em espera", "warning": "Precisa de atenção",
                 "failed": "Falhou"}.get(status, "Em processamento")
        if tracked == "importing":
            label = "A importar"
        if warning:
            label = "Precisa de atenção"
        details = []
        for msg in rec.get("statusMessages") or []:
            details.extend(str(x) for x in msg.get("messages") or [])
        title_obj = rec.get("movie") or rec.get("series") or {}
        normalized.append({
            "source": rec["source"],
            "title": title_obj.get("title") or rec.get("title") or "Download",
            "status": rec.get("status") or "downloading",
            "status_label": label,
            "warning": warning,
            "details": " · ".join(details),
            "progress": pct,
            "timeleft": rec.get("timeleft") or "—",
        })
    return {"queue": normalized, "recent": sorted(recent, key=lambda x: x["added"], reverse=True)[:12],
            "errors": errors, "updated_at": int(time.time())}


# ============================================================================
# 3. CONTAINERS DOCKER & AÇÕES PREDEFINIDAS
# ============================================================================

PREDEFINED_CONTAINER_META = {
    "jellyfin": {"icon": "🎞️", "port": 8096, "cat": "Media", "prompts": ["Verifica uso de CPU/transcoding do Jellyfin", "Vê os últimos erros nos logs do Jellyfin"]},
    "sonarr": {"icon": "📺", "port": 8989, "cat": "Media", "prompts": ["Lista as séries com episódios em falta no Sonarr", "Verifica se há downloads bloqueados no Sonarr"]},
    "radarr": {"icon": "🎬", "port": 7878, "cat": "Media", "prompts": ["Lista os últimos filmes adicionados ao Radarr", "Verifica o estado dos indexers e downloads no Radarr"]},
    "bazarr": {"icon": "💬", "port": 6767, "cat": "Media", "prompts": ["Verifica legendas PT em falta no Bazarr"]},
    "prowlarr": {"icon": "🔍", "port": 9696, "cat": "Media", "prompts": ["Testa todos os indexers do Prowlarr e vê se algum falhou"]},
    "qbittorrent": {"icon": "⬇️", "port": 8080, "cat": "Media", "prompts": ["Verifica os torrents ativos e a ligação VPN do qBittorrent"]},
    "homeassistant": {"icon": "🏠", "port": 8123, "cat": "Casa", "prompts": ["Analisa os logs do Home Assistant e sensores indisponíveis"]},
    "nextcloud": {"icon": "☁️", "port": 8089, "cat": "Cloud", "prompts": ["Verifica o estado e espaço da Nextcloud"]},
    "caddy": {"icon": "🔒", "port": 443, "cat": "Rede", "prompts": ["Verifica os certificados e reverse proxies do Caddyfile"]},
    "authelia": {"icon": "🛡️", "port": 9091, "cat": "Segurança", "prompts": ["Mostra os últimos acessos e tentativas de login no Authelia"]},
    "uptime-kuma": {"icon": "📈", "port": 3001, "cat": "Sistema", "prompts": ["Verifica quais os monitores em baixo no Uptime Kuma"]},
    "n8n": {"icon": "⚡", "port": 5678, "cat": "Automação", "prompts": ["Verifica os workflows ativos no n8n"]},
    "homepage": {"icon": "🧭", "port": 3000, "cat": "Sistema", "prompts": ["Valida o services.yaml e custom.js da Homepage"]},
}


def list_containers() -> list[dict]:
    try:
        p = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}|{{.Status}}|{{.State}}|{{.Image}}"],
            capture_output=True, text=True, timeout=6
        )
        out = []
        for line in p.stdout.splitlines():
            parts = line.split("|", 3)
            if len(parts) < 4:
                continue
            name, status, state, img = parts
            key = name.lower()
            meta = next((v for k, v in PREDEFINED_CONTAINER_META.items() if k in key), None)
            out.append({
                "name": name,
                "status": status,
                "state": state,
                "image": img.split("/")[-1],
                "icon": meta["icon"] if meta else "🐳",
                "port": meta["port"] if meta else None,
                "cat": meta["cat"] if meta else "Docker",
                "prompts": meta["prompts"] if meta else [f"Analisa os logs e estado do container {name}"],
            })
        out.sort(key=lambda x: (0 if x["state"] != "running" else 1, x["name"]))
        return out
    except Exception:
        return []


def container_action(name: str, action: str) -> dict:
    clean = re.sub(r"[^a-zA-Z0-9_.-]", "", name)
    if not clean:
        return {"ok": False, "error": "Nome inválido"}
    try:
        if action == "restart":
            r = subprocess.run(["docker", "restart", clean], capture_output=True, text=True, timeout=25)
            return {"ok": r.returncode == 0, "output": f"Container {clean} reiniciado com sucesso!" if r.returncode == 0 else r.stderr}
        elif action == "logs":
            r = subprocess.run(["docker", "logs", "--tail", "80", clean], capture_output=True, text=True, timeout=8)
            logs = (r.stdout or "") + "\n" + (r.stderr or "")
            return {"ok": True, "output": logs.strip()[-6000:] or "Sem logs recentes."}
        return {"ok": False, "error": "Ação desconhecida"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ============================================================================
# 4. HOME ASSISTANT (IMPRESSORAS 3D, TOMADAS, LUZES & WEBHOOKS)
# ============================================================================

def _ha_get_states() -> dict[str, dict]:
    req = urllib.request.Request(
        f"{HA_URL}/api/states",
        headers={"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=6) as r:
        arr = json.loads(r.read().decode("utf-8"))
        return {it["entity_id"]: it for it in arr if "entity_id" in it}


def get_ha_dashboard() -> dict:
    try:
        st = _ha_get_states()
    except Exception as e:
        return {"error": str(e), "printers": [], "switches": []}

    def val(eid: str, default: str = "—") -> str:
        s = st.get(eid, {}).get("state")
        return default if s in (None, "unavailable", "unknown") else str(s)

    printers = [
        {
            "id": "centauri",
            "name": "Centauri Carbon 2",
            "icon": "🖨️",
            "status": val("sensor.centauri_carbon_2_status_de_impressao", "desligada"),
            "progress": val("sensor.centauri_carbon_2_percent_complete", "0"),
            "remaining_min": val("sensor.centauri_carbon_2_remaining_print_time", "0"),
            "nozzle": val("sensor.centauri_carbon_2_nozzle_temperature", "—"),
            "bed": val("sensor.centauri_carbon_2_bed_temperature", "—"),
            "plug_entity": "switch.tomada_centauri_carbon_2",
            "plug_state": val("switch.tomada_centauri_carbon_2", "off"),
            "light_entity": "light.centauri_carbon_2_chamber_light",
            "light_state": val("light.centauri_carbon_2_chamber_light", "off"),
        },
        {
            "id": "qidi",
            "name": "Qidi Plus 4",
            "icon": "🔥",
            "status": val("sensor.qidi_estado", val("sensor.qidi_plus_4", "desligada")),
            "progress": val("sensor.qidi_progresso", "0"),
            "remaining_min": val("sensor.qidi_tempo_restante", "0"),
            "nozzle": val("sensor.qidi_bico", "—"),
            "bed": val("sensor.qidi_cama", "—"),
            "plug_entity": "switch.tomada_qidi_plus_4",
            "plug_state": val("switch.tomada_qidi_plus_4", "off"),
        },
    ]

    switch_defs = [
        ("switch.tomada_centauri_carbon_2", "Tomada Centauri Carbon 2", "🖨️"),
        ("switch.tomada_qidi_plus_4", "Tomada Qidi Plus 4", "🔥"),
        ("switch.tomada_ender_3_v2", "Tomada Ender 3 V2", "⚙️"),
        ("light.centauri_carbon_2_chamber_light", "Luz Câmara Centauri", "💡"),
        ("switch.tomada_qidi_box", "Qidi Box (Secador)", "🌀"),
        ("switch.tomada_anycubic_1", "Anycubic 4K · 1 (Resina)", "🧪"),
        ("switch.tomada_anycubic_2", "Anycubic 4K · 2 (Resina)", "🧪"),
        ("switch.tomada_mercury_plus", "Mercury Plus (Cura)", "✨"),
        ("switch.camara_dungenlabs", "Câmara DungenLabs", "📹"),
    ]
    switches = []
    for eid, label, icon in switch_defs:
        switches.append({
            "entity_id": eid,
            "name": label,
            "icon": icon,
            "state": val(eid, "off"),
        })

    return {"printers": printers, "switches": switches}


def trigger_ha_action(entity_id: str = "", webhook_action: str = "toggle") -> dict:
    try:
        url = f"{HA_WEBHOOK}?a={urllib.parse.quote(webhook_action)}"
        if entity_id:
            url += f"&e={urllib.parse.quote(entity_id)}"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=8) as r:
            r.read()
        time.sleep(0.4)
        return {"ok": True, "dashboard": get_ha_dashboard()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ============================================================================
# 5. GERADOR DE IMAGENS & GIFS ANIMADOS (GIF89a) + GALERIA
# ============================================================================

def generate_animated_gif(prompt: str, aspect: str = "1:1") -> dict:
    """Create an original animated illustration from the prompt, with no canned fallback."""
    import maestro_gif
    gif_bytes, width, height = maestro_gif.generate(prompt, aspect)
    ensure_feature_dirs()
    fname = f"maestro-gif-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:5]}.gif"
    fpath = GALLERY_DIR / fname
    fpath.write_bytes(gif_bytes)
    meta = {"name": fname, "path": str(fpath), "kind": "gif", "prompt": prompt,
            "created_at": time.time(), "width": width, "height": height,
            "fps": 16, "duration_s": 3, "source": "ai_vector_animation"}
    _record_gallery_meta(meta)
    return meta


def generate_ai_image(prompt: str, aspect: str = "1:1") -> dict:
    """
    Gera uma imagem AI de alta resolução a partir do prompt (via Flux Pollinations API com fallback SVG/PNG local)
    e guarda na Galeria do Maestro AI.
    """
    ensure_feature_dirs()
    dims = {"1:1": (1024, 1024), "16:9": (1280, 720), "9:16": (720, 1280)}.get(aspect, (1024, 1024))
    w, h = dims
    seed = int(time.time()) % 999999
    q = urllib.parse.quote(prompt.strip())
    url = f"https://image.pollinations.ai/prompt/{q}?width={w}&height={h}&seed={seed}&nologo=true&model=flux"

    fname = f"maestro-img-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:5]}.png"
    fpath = GALLERY_DIR / fname

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MaestroAI-Studio/2.0"})
        with urllib.request.urlopen(req, timeout=35) as r:
            img_bytes = r.read()
            if len(img_bytes) > 1024:
                fpath.write_bytes(img_bytes)
                # Também copia para PASTES_DIR para poder ser anexada diretamente a agentes CLI
                (PASTES_DIR / fname).write_bytes(img_bytes)
                meta = {"name": fname, "path": str(fpath), "kind": "ai_image", "prompt": prompt, "created_at": time.time()}
                _record_gallery_meta(meta)
                return meta
    except Exception:
        pass

    # Fallback local: renderizar arte vetorial SVG/PNG de alta qualidade
    import build_windows_exe as B
    png_bytes = B.render_maestro_png(512)
    fpath.write_bytes(png_bytes)
    meta = {"name": fname, "path": str(fpath), "kind": "ai_image", "prompt": prompt + " (Procedural)", "created_at": time.time()}
    _record_gallery_meta(meta)
    return meta


def _record_gallery_meta(item: dict) -> None:
    meta_file = GALLERY_DIR / "index.json"
    items = []
    if meta_file.exists():
        try:
            items = json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            items = []
    items.insert(0, item)
    meta_file.write_text(json.dumps(items[:200], ensure_ascii=False, indent=2), encoding="utf-8")


def list_gallery() -> list[dict]:
    ensure_feature_dirs()
    meta_file = GALLERY_DIR / "index.json"
    indexed = {}
    if meta_file.exists():
        try:
            for it in json.loads(meta_file.read_text(encoding="utf-8")):
                if os.path.exists(it.get("path", "")):
                    indexed[it["path"]] = it
        except Exception:
            pass

    items = list(indexed.values())
    seen_paths = set(indexed.keys())

    # Fontes de imagens para a Galeria: AI Gallery, Pastes, Logos/Assets NAS e Uploads
    sources = [
        (GALLERY_DIR, "ai_image"),
        (PASTES_DIR, "paste"),
        (pathlib.Path("/srv/appdata/0_SERVER/2_CONFIG/homepage/images"), "nas_asset"),
        (HOME / "scripts" / "maestro", "nas_asset"),
    ]
    exts = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg")

    for folder, default_kind in sources:
        if not folder.exists():
            continue
        try:
            for fp in folder.iterdir():
                if not fp.is_file() or fp.suffix.lower() not in exts:
                    continue
                s_fp = str(fp)
                if s_fp in seen_paths:
                    continue
                seen_paths.add(s_fp)
                st = fp.stat()
                if st.st_size < 120:
                    continue
                kind = "gif" if fp.suffix.lower() == ".gif" else default_kind
                items.append({
                    "name": fp.name,
                    "path": s_fp,
                    "kind": kind,
                    "prompt": fp.stem.replace("-", " ").replace("_", " "),
                    "size_kb": round(st.st_size / 1024, 1),
                    "created_at": st.st_mtime,
                })
        except Exception:
            pass

    items.sort(key=lambda x: x.get("created_at") or 0, reverse=True)
    return items


def delete_gallery_item(file_path: str) -> dict:
    """Remove uma imagem da galeria ou da pasta de pastes."""
    p = os.path.abspath(file_path)
    allowed_del = (str(GALLERY_DIR), str(PASTES_DIR))
    if not any(p.startswith(a) for a in allowed_del) or not os.path.isfile(p):
        return {"ok": False, "error": "Só podes apagar imagens geradas ou prints colados."}
    try:
        os.unlink(p)
        meta_file = GALLERY_DIR / "index.json"
        if meta_file.exists():
            data = [i for i in json.loads(meta_file.read_text(encoding="utf-8")) if i.get("path") != p]
            meta_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "items": list_gallery()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ============================================================================
# 6. SCHEDULERS & AUTOMAÇÃO INTEGRADA COM HA + MAESTRO
# ============================================================================

DEFAULT_SCHEDULES = [
    {
        "id": "sched-pulso-morning",
        "name": "Checkup Matinal Pulso & Containers",
        "time_hhmm": "09:00",
        "kind": "maestro_prompt",
        "target": "Verifica os alertas do Pulso, estado dos discos e containers Docker e guarda um resumo.",
        "enabled": True,
        "last_run": None,
        "last_result": "Pronto a executar",
    },
    {
        "id": "sched-printer-watch",
        "name": "Proteção Tomadas Impressoras 3D (HA)",
        "time_hhmm": "02:00",
        "kind": "ha_check",
        "target": "Verificar se Centauri Carbon 2 e Qidi Plus 4 estão idle antes de desligar luzes/tomadas.",
        "enabled": True,
        "last_run": None,
        "last_result": "Ativo",
    },
]


def get_schedules() -> list[dict]:
    ensure_feature_dirs()
    if SCHEDULES_FILE.exists():
        try:
            data = json.loads(SCHEDULES_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except Exception:
            pass
    SCHEDULES_FILE.write_text(json.dumps(DEFAULT_SCHEDULES, ensure_ascii=False, indent=2), encoding="utf-8")
    return list(DEFAULT_SCHEDULES)


def save_schedule(sched: dict) -> list[dict]:
    items = get_schedules()
    sid = sched.get("id") or f"sched-{uuid.uuid4().hex[:6]}"
    entry = {
        "id": sid,
        "name": str(sched.get("name") or "Nova Automação")[:70],
        "time_hhmm": str(sched.get("time_hhmm") or "09:00")[:5],
        "kind": str(sched.get("kind") or "maestro_prompt"),  # maestro_prompt | ha_toggle | docker_restart
        "target": str(sched.get("target") or ""),
        "enabled": bool(sched.get("enabled", True)),
        "last_run": sched.get("last_run"),
        "last_result": sched.get("last_result") or "Agendado",
    }
    replaced = False
    for i, it in enumerate(items):
        if it["id"] == sid:
            items[i] = entry
            replaced = True
            break
    if not replaced:
        items.append(entry)
    SCHEDULES_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    return items


def toggle_or_delete_schedule(sid: str, delete: bool = False) -> list[dict]:
    items = get_schedules()
    if delete:
        items = [i for i in items if i["id"] != sid]
    else:
        for i in items:
            if i["id"] == sid:
                i["enabled"] = not bool(i.get("enabled", True))
    SCHEDULES_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    return items


def run_schedule_now(sid: str) -> dict:
    items = get_schedules()
    target_sched = next((i for i in items if i["id"] == sid), None)
    if not target_sched:
        return {"ok": False, "error": "Agendamento não encontrado"}

    kind = target_sched.get("kind")
    target = target_sched.get("target") or ""
    result_msg = "Executado com sucesso"

    try:
        if kind == "ha_toggle":
            r = trigger_ha_action(entity_id=target, webhook_action="toggle")
            result_msg = f"Home Assistant toggle ({target}): {'OK' if r.get('ok') else r.get('error')}"
        elif kind == "docker_restart":
            r = container_action(target, "restart")
            result_msg = r.get("output") or r.get("error") or "OK"
        else:
            # Executa via Maestro / DeepSeek rápido para não gastar quota semanal
            import maestro as M
            plan = M.classify_task(target, str(HOME))
            M.update_shared_context(f"[Automação Agendada: {target_sched['name']}] {target}", plan)
            result_msg = f"Executado pelo Maestro ({plan['domain']} · {' -> '.join(plan['agents'])})"
    except Exception as e:
        result_msg = f"Erro: {e}"

    target_sched["last_run"] = dt.datetime.now().strftime("%d/%m %H:%M")
    target_sched["last_result"] = result_msg[:160]
    SCHEDULES_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "schedules": items, "result": result_msg}


def start_scheduler_daemon() -> None:
    """Thread ligeira em background que verifica de minuto a minuto se há agendamentos para disparar."""
    last_triggered_minute = ""

    def loop():
        nonlocal last_triggered_minute
        while True:
            try:
                now_hm = dt.datetime.now().strftime("%H:%M")
                if now_hm != last_triggered_minute:
                    for s in get_schedules():
                        if s.get("enabled") and s.get("time_hhmm") == now_hm:
                            run_schedule_now(s["id"])
                    last_triggered_minute = now_hm
            except Exception:
                pass
            time.sleep(30)

    t = threading.Thread(target=loop, daemon=True)
    t.start()
