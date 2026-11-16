#!/usr/bin/env python3
"""Ver uma página como o Hubert a vê: captura PNG + procura de texto no DOM renderizado.

    python3 tools/ver.py URL [--texto "frase"]... [--falta "frase"]... [--movel] [--saida ficheiro.png]

Sai com 0 quando todos os --texto aparecem e nenhum --falta aparece; 1 caso contrário; 2 se o browser falhar.
Usa o chrome-headless-shell já instalado pelo Playwright (sem dependências novas).
"""
import argparse
import glob
import html
import os
import re
import subprocess
import sys
import time

MOBILE_UA = ('Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) '
             'Chrome/126.0 Mobile Safari/537.36')


def browser():
    found = sorted(glob.glob(os.path.expanduser('~/.cache/ms-playwright/chromium_headless_shell-*/chrome-*/*headless*shell*')) +
                   glob.glob(os.path.expanduser('~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome')))
    found = [p for p in found if os.access(p, os.X_OK) and os.path.isfile(p)]
    if not found:
        sys.exit('ver.py: nenhum Chromium do Playwright em ~/.cache/ms-playwright')
    return found[-1]


def visible_text(dom):
    dom = re.sub(r'(?is)<(script|style|noscript|template)\b.*?</\1>', ' ', dom)
    return ' '.join(html.unescape(re.sub(r'(?s)<[^>]+>', ' ', dom)).split())


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('url')
    ap.add_argument('--texto', action='append', default=[], help='tem de aparecer no texto visível')
    ap.add_argument('--falta', action='append', default=[], help='não pode aparecer')
    ap.add_argument('--movel', action='store_true', help='ecrã 390x844 com user-agent de telemóvel')
    ap.add_argument('--saida', help='caminho do PNG (por omissão /tmp/maestro-ver-<hora>.png)')
    ap.add_argument('--espera', type=int, default=6000, help='ms de tempo virtual para o JS assentar')
    a = ap.parse_args()
    out = a.saida or '/tmp/maestro-ver-%d.png' % (time.time() * 1000)
    base = [browser(), '--headless', '--no-sandbox', '--disable-gpu', '--hide-scrollbars', '--ignore-certificate-errors',
            '--window-size=' + ('390,844' if a.movel else '1440,900'), '--virtual-time-budget=%d' % a.espera]
    if a.movel:
        base.append('--user-agent=' + MOBILE_UA)
    try:
        subprocess.run(base + ['--screenshot=' + out, a.url], capture_output=True, timeout=90)
        dom = subprocess.run(base + ['--dump-dom', a.url], capture_output=True, text=True, timeout=90).stdout
    except subprocess.TimeoutExpired:
        print('ver.py: o browser excedeu 90 s em ' + a.url)
        return 2
    if not dom.strip() or not os.path.exists(out):
        print('ver.py: a página não carregou (' + a.url + ')')
        return 2
    text = visible_text(dom)
    folded = text.casefold()
    ok = True
    print('URL: %s (%s)' % (a.url, 'telemóvel 390x844' if a.movel else 'desktop 1440x900'))
    print('Captura: %s  ← abre este PNG para veres o resultado' % out)
    print('Título: ' + (re.search(r'(?is)<title[^>]*>(.*?)</title>', dom) or [None, ''])[1].strip()[:120])
    for wanted in a.texto:
        hit = wanted.casefold() in folded
        ok &= hit
        print(('✔ aparece: ' if hit else '✘ NÃO aparece: ') + wanted)
    for banned in a.falta:
        hit = banned.casefold() in folded
        ok &= not hit
        print(('✘ ainda aparece: ' if hit else '✔ já não aparece: ') + banned)
    if not a.texto and not a.falta:
        print('Texto visível (início): ' + text[:600])
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
