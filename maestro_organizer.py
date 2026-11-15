"""Local, conservative chat filing. No model requests or recursive home scans."""
from __future__ import annotations

import hashlib
import pathlib
import re
import unicodedata

IDLE_SECONDS = 45 * 60


def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(text)).lower()
                   if not unicodedata.combining(c))


def discover_candidates(home: pathlib.Path, folders: list[dict]) -> list[dict]:
    """Discover immediate project roots; create sidebar folders only when used."""
    aliases = {
        'nas': ['pavilion-nas', 'docker', 'pulso', 'servidor', 'nas'],
        'polistrat': ['polistrat', 'unity'],
        'modding': ['spacebourne', 'modding', 'unreal', 'mods'],
        'web': ['dungenlabs', 'portfolio', 'portefolio'],
        'media': ['radarr', 'sonarr', 'jellyfin', 'bazarr', 'filmes', 'filme', 'series', 'movies'],
        'casa': ['home assistant', 'centauri', 'qidi', 'impressao 3d', 'impressora', 'domotica'],
    }
    candidates = []
    for folder in folders:
        words = aliases.get(folder['id'])
        if folder.get('cwd') == str(home / 'scripts' / 'maestro'):
            words = ['maestro', 'orquestrador', 'orchestrator']
        if words is None:
            words = [folder['name'], pathlib.Path(folder.get('cwd') or '/').name]
            words = [w for w in words if len(w) >= 4 and normalized(w) != normalized(home.name)]
        candidates.append(dict(folder, aliases=words))
    maestro = home / 'scripts' / 'maestro'
    roots = [maestro] if maestro.is_dir() else []
    try:
        roots += [p for p in home.iterdir() if not p.name.startswith('.') and p.is_dir()
                  and not p.is_symlink() and any((p / marker).exists() for marker in
                  ('.git', 'package.json', 'pyproject.toml', 'Cargo.toml', 'compose.yaml', 'docker-compose.yml'))]
    except OSError:
        pass
    used_paths = {str(pathlib.Path(f.get('cwd') or '/')) for f in folders
                  if f.get('cwd') and str(f['cwd']) != str(home)}
    for path in sorted(roots):
        if str(path) in used_paths:
            continue
        # Worktrees, archives and backups belong to their parent project's folder.
        if re.search(r'(?i)(?:[-_](?:qa|archive|arquivo|backup|refresh|check))', path.name):
            continue
        slug = re.sub(r'[^a-z0-9]+', '-', normalized(path.name)).strip('-')
        fid = 'auto-' + slug + '-' + hashlib.sha256(str(path).encode()).hexdigest()[:6]
        if any(f['id'] == fid for f in candidates):
            continue
        is_maestro = path == maestro
        candidates.append({'id': fid, 'name': 'Maestro AI' if is_maestro else path.name,
                           'icon': '🧠' if is_maestro else '📂', 'color': '#a78bfa',
                           'cwd': str(path), 'desc': 'Projeto detetado no pavilion-nas',
                           'aliases': ['maestro', 'orquestrador', 'orchestrator'] if is_maestro else [path.name]})
    return candidates


def classify_chat(chat: dict, candidates: list[dict]) -> dict | None:
    # User requests only: generated handovers and assistant source dumps bias routing.
    requests = [m.get('text', '') for m in chat.get('messages', []) if m.get('role') == 'user']
    if not requests:
        return None
    samples = [(normalized(chat.get('title', '')), 2)]
    for text in requests[-8:]:
        text = text.split('MENSAGEM ATUAL DO HUBERT:')[-1]
        samples.append((normalized(text[:6000]), 3))
    scores = []
    for candidate in candidates:
        score = 0
        for text, weight in samples:
            if any(re.search(r'(?<!\w)' + re.escape(normalized(alias)) + r'(?!\w)', text)
                   for alias in candidate.get('aliases', []) if alias):
                score += weight
        scores.append((score, candidate))
    scores.sort(key=lambda item: item[0], reverse=True)
    if not scores or scores[0][0] < 3:
        return None
    if len(scores) > 1 and scores[0][0] - scores[1][0] < 2:
        return None
    return scores[0][1]
