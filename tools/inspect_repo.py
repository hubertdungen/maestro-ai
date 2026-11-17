#!/usr/bin/env python3
"""Fixed, non-interactive Git inspections for restricted Maestro workers."""
import argparse
import os
import pathlib
import subprocess


COMMANDS = {
    'status': ['status', '--short', '--untracked-files=normal'],
    'log': ['log', '-20', '--oneline'],
    'diff': ['diff', '--no-ext-diff', '--no-textconv'],
    'staged': ['diff', '--cached', '--no-ext-diff', '--no-textconv'],
    'branches': ['branch', '--list', '--all', '--no-color'],
}


def inspect(action, repo):
    root = pathlib.Path(repo).resolve(strict=True)
    if not root.is_dir():
        raise ValueError('O repositório tem de ser um diretório')
    # Disable inherited overrides, external pagers, fsmonitor hooks and diff
    # drivers. The CLI accepts no arbitrary Git flags or shell expressions.
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0', GIT_PAGER='cat')
    return subprocess.run(
        ['git', '--no-pager', '-c', 'core.fsmonitor=false',
         '-c', 'core.hooksPath=' + os.devnull, '-c', 'color.ui=false',
         '-C', str(root), *COMMANDS[action]],
        env=env, timeout=30, check=False,
    ).returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=COMMANDS)
    parser.add_argument('repo', help='Caminho do repositório; não usa cd nem shell')
    args = parser.parse_args()
    try:
        return inspect(args.action, args.repo)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())
