"""Durable repair summaries for Pulso's weekly and monthly email digests."""
from datetime import datetime
import html
import json
from pathlib import Path

STATE = Path.home() / '.local/state/maestro/pulse-weekly.json'
MARKER = '    # Maestro: durable weekly repair summaries\n'
HOOK = '''    # Maestro: durable weekly repair summaries
    import importlib.util
    report_spec = importlib.util.spec_from_file_location(
        'maestro_pulse_report', Path.home() / 'scripts/maestro/maestro_pulse_report.py')
    report_module = importlib.util.module_from_spec(report_spec)
    report_spec.loader.exec_module(report_module)
    repair_html, repair_text = report_module.render(start.timestamp(), now.timestamp())
    body.append(section("Resolução automática do NAS", [row("info", repair_html)]))
    text.append("Resolução automática do NAS\\n" + repair_text)

'''


def render(start, now, state_path=STATE):
    try:
        state = json.loads(state_path.read_text())
    except (OSError, ValueError):
        text = 'Não foi possível consultar o resultado da revisão automática; não há correções confirmadas neste relatório.'
    else:
        runs = [(key, run) for key, run in sorted(state.get('runs', {}).items())
                if start <= max(run.get('created_at', 0), run.get('submitted_at', 0),
                                run.get('completed_at', 0)) <= now]
        parts = []
        for key, run in runs:
            if run.get('summary'):
                finished = datetime.fromtimestamp(run['completed_at']).astimezone().strftime('%d/%m %H:%M')
                parts.append(f'Revisão de {key} · concluída em {finished}\n' + run['summary'])
            elif not run.get('deleted_at'):
                parts.append(f'Revisão de {key}: aguarda execução ou conclusão. Ainda não há correções confirmadas; o resultado seguirá por email quando terminar.')
        text = '\n\n'.join(parts) or 'Sem revisão automática concluída ou em curso neste período; não há correções confirmadas para apresentar.'
    return '<div style="white-space:pre-wrap">' + html.escape(text) + '</div>', text


def install(notify_path):
    """Install the small digest hook without copying Pulso config or credentials."""
    source = notify_path.read_text()
    if MARKER in source:
        return
    anchor = '    eps = episodes(start.timestamp())\n'
    if source.count(anchor) != 1:
        raise RuntimeError('Formato inesperado de notify.py; instalação cancelada')
    updated = source.replace(anchor, HOOK + anchor)
    compile(updated, str(notify_path), 'exec')
    notify_path.write_text(updated)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', type=Path, required=True, metavar='NOTIFY_PY')
    install(parser.parse_args().install)
