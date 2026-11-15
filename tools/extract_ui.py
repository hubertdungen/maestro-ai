#!/usr/bin/env python3
"""Move o APP_HTML de maestro_web.py para ui/{index.html,app.css,app.js}.

Uso: extract_ui.py [pasta]. Corre sobre o estado atual do ficheiro (sem números
de linha) e é idempotente: se o APP_HTML já não existir, sai sem mexer em nada.
"""
import pathlib
import re
import sys

d = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else pathlib.Path(__file__).resolve().parents[1])
src = d / 'maestro_web.py'
s = src.read_text()
m = re.search(r'^APP_HTML = r"""(.*?)\n"""\n', s, re.S | re.M)
if not m:
    sys.exit('APP_HTML não encontrado (já extraído?)')
html = m.group(1)
css = re.search(r'<style>\n(.*?)\n</style>', html, re.S)
js = re.search(r'<script>\n(.*?)\n</script>', html, re.S)
assert css and js and html.count('<style>') == 1 and html.count('<script>') == 1, 'esperava 1 <style> e 1 <script>'
out = html.replace(css.group(0), '<link rel="stylesheet" href="/ui/app.css">').replace(js.group(0), '<script src="/ui/app.js"></script>')

LOADER = '''UI_DIR = pathlib.Path(__file__).resolve().parent / "ui"


def ui_file(name: str) -> str:
    """UI em ficheiros: editar ui/* só pede F5 (sem reiniciar o servidor)."""
    text = (UI_DIR / name).read_text(encoding="utf-8")
    if name == "app.css":
        return text + "\\n" + WORKFLOW_UI.CSS
    if name == "app.js":  # o módulo do workflow vive dentro da IIFE da app
        i = text.rindex("})();")
        return text[:i] + WORKFLOW_UI.JS + "\\n" + text[i:]
    return text
'''


def sub(old, new):
    global s
    assert s.count(old) == 1, 'padrão não encontrado ou repetido: ' + old[:60]
    s = s.replace(old, new)


s = s[:m.start()] + LOADER + s[m.end():]
s = re.sub(r'^APP_HTML = APP_HTML\.replace\(.*\n', '', s, flags=re.M)  # passa para ui_file()
sub('            return self._reply(200, APP_HTML, "text/html; charset=utf-8")',
    '            return self._reply(200, ui_file("index.html"), "text/html; charset=utf-8")\n'
    '        if u.path == "/ui/app.css":\n'
    '            return self._reply(200, ui_file("app.css"), "text/css; charset=utf-8")\n'
    '        if u.path == "/ui/app.js":\n'
    '            return self._reply(200, ui_file("app.js"), "application/javascript; charset=utf-8")')
# service worker: /ui/* nunca vem da cache (senão o JS ficava velho para sempre)
sub('  if (url.pathname === "/sw.js") return;', '  if (url.pathname === "/sw.js" || url.pathname.indexOf("/ui/") === 0) return;')
sub('const CACHE = "maestro-shell-v4";', 'const CACHE = "maestro-shell-v5";')
# o auto-updater passa a ver também as alterações aos ficheiros da UI
sub('    watched = [\n        MAESTRO_DIR / "maestro_web.py",',
    '    watched = [\n        *sorted(UI_DIR.glob("*")),\n        MAESTRO_DIR / "maestro_web.py",')

ui = d / 'ui'
ui.mkdir(exist_ok=True)
(ui / 'index.html').write_text(out)
(ui / 'app.css').write_text(css.group(1))
(ui / 'app.js').write_text(js.group(1))
src.write_text(s)
print('extraído:', {p.name: p.stat().st_size for p in ui.iterdir()})
