"""Escrita em paralelo: cada tarefa com âmbito próprio trabalha num worktree git só seu.

O Maestro cria o worktree a partir do HEAD do repositório, faz o commit do que a
tarefa mudou (só dentro dos `write_paths` declarados) e junta o ramo no checkout
principal. Um conflito nunca se resolve sozinho: o merge é abortado e o ramo fica.
"""
from __future__ import annotations

import fnmatch
import os
import pathlib
import re
import shutil
import subprocess
import threading

ROOT = pathlib.Path.home() / '.local' / 'state' / 'maestro' / 'worktrees'
IDENT = ['-c', 'user.name=Maestro AI', '-c', 'user.email=hubert@pavilion-nas']
# O serviço corre com o PATH do systemd, sem ~/.local/bin, onde está o git-lfs: sem ele o
# `worktree add` de um repo com LFS (FragorUnreal) falha no filtro e no hook post-checkout.
ENV = {**os.environ, 'PATH': f"{pathlib.Path.home() / '.local' / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}"}
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _git(repo, *args, timeout=120):
    r = subprocess.run(['git', '-C', str(repo), *IDENT, *args], capture_output=True, env=ENV, text=True, timeout=timeout)
    if r.returncode:
        raise RuntimeError(f"git {' '.join(args[:2])}: {(r.stderr or r.stdout).strip()[-400:]}")
    return r.stdout.strip()


def _lock(repo):
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(str(pathlib.Path(repo).resolve()), threading.Lock())


def _norm(path):
    return str(path).strip().lstrip('./').rstrip('/').split('*')[0].rstrip('/')


def overlaps(a, b):
    """True se dois conjuntos de caminhos relativos se sobrepõem (igual ou um contém o outro)."""
    for x in map(_norm, a):
        for y in map(_norm, b):
            if not x or not y or x == y or x.startswith(y + '/') or y.startswith(x + '/'):
                return True
    return False


def inside(path, scope):
    """Padrões ao estilo .gitignore, a qualquer profundidade: `FragorHumanoid*` apanha
    `Source/X/FragorHumanoid.cpp` e `Tests/Corpo` apanha `Source/X/Tests/Corpo/a.cpp`."""
    parts = str(path).strip().lstrip('./').split('/')
    for raw in scope:
        pat = str(raw).strip().lstrip('./').rstrip('/').replace('**/', '').replace('/**', '')
        if not pat:
            continue
        n = pat.count('/') + 1
        for i in range(len(parts)):
            if fnmatch.fnmatch('/'.join(parts[i:i + n]), pat):
                return True
    return False


def open_worktree(repo, name):
    """Cria o worktree; devolve dict(path, branch, base, resumed).

    Numa retoma o ramo da tentativa anterior continua: o trabalho já feito nunca se apaga."""
    name = re.sub(r'[^A-Za-z0-9_-]', '-', name)[:60]
    path, branch = ROOT / name, 'maestro/' + name
    with _lock(repo):
        head = _git(repo, 'rev-parse', 'HEAD')
        if _git(repo, 'branch', '--list', branch):
            if not (path / '.git').exists():
                shutil.rmtree(path, ignore_errors=True)
                _git(repo, 'worktree', 'prune')
                _git(repo, 'worktree', 'add', str(path), branch)
            return dict(path=str(path), branch=branch, base=_git(repo, 'merge-base', head, branch), resumed=True)
        if path.exists():
            subprocess.run(['git', '-C', str(repo), 'worktree', 'remove', '--force', str(path)], capture_output=True, env=ENV, timeout=60)
            shutil.rmtree(path, ignore_errors=True)
        ROOT.mkdir(parents=True, exist_ok=True)
        _git(repo, 'worktree', 'add', '-b', branch, str(path), head)
    return dict(path=str(path), branch=branch, base=head, resumed=False)


def integrate(repo, wt, scope, title):
    """Commit do que mudou no worktree e merge no checkout principal. Devolve (ok, texto)."""
    path, branch, base = wt['path'], wt['branch'], wt['base']
    if _git(path, 'status', '--porcelain'):
        _git(path, 'add', '-A')
        _git(path, 'commit', '-q', '-m', 'Maestro: ' + title[:120])
    changed = [f for f in _git(path, 'diff', '--name-only', base, 'HEAD').splitlines() if f]
    if not changed:
        close(repo, wt)
        return True, 'Sem alterações para integrar.'
    # O âmbito serve para escolher quem corre em paralelo; quem decide se dá para juntar é o merge.
    # Sair do âmbito só falha quando choca com outro escritor (conflito, mais abaixo).
    outside = [f for f in changed if not inside(f, scope)]
    with _lock(repo):
        try:
            _git(repo, 'merge', '--no-ff', '--no-edit', '-m', f'Maestro: integra {title[:100]}', branch)
        except RuntimeError as exc:
            subprocess.run(['git', '-C', str(repo), 'merge', '--abort'], capture_output=True, env=ENV, timeout=60)
            return False, f'Conflito ao integrar ({exc}). O merge foi abortado; o trabalho ficou no ramo {branch} ({path}).'
        head = _git(repo, 'rev-parse', '--short', 'HEAD')
    close(repo, wt)
    extra = f' Fora do âmbito declarado, juntos sem conflito: {outside[:10]}.' if outside else ''
    return True, f'Integrado em {repo} ({head}): ' + ', '.join(changed[:20]) + extra


def close(repo, wt):
    with _lock(repo):
        subprocess.run(['git', '-C', str(repo), 'worktree', 'remove', '--force', wt['path']], capture_output=True, env=ENV, timeout=60)
        subprocess.run(['git', '-C', str(repo), 'branch', '-D', wt['branch']], capture_output=True, env=ENV, timeout=60)
    shutil.rmtree(wt['path'], ignore_errors=True)
