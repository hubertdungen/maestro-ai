"""Read public CLI events for activity hints without exposing reasoning or tool output."""
import json
import maestro_efficiency as E


class ActivityStream:
    def __init__(self, provider):
        self.provider = provider
        self.answer = None
        self.plain = []
        self.errors = []
        self.started_tools = False
        self.usage = None
        self.progress_text = ''

    def feed(self, line):
        self.progress_text = ''
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            self.plain.append(line)
            return None
        if not isinstance(event, dict):
            self.plain.append(line)
            return None
        kind = event.get('type')
        if (self.provider == 'codex' and kind == 'turn.completed') or (self.provider != 'codex' and kind == 'result'):
            measured = E.usage(event.get('usage'), 'codex' if self.provider == 'codex' else 'claude')
            if measured:
                if self.usage is None:
                    self.usage = measured
                else:
                    self.usage = {k: self.usage[k] + measured[k] for k in measured}
        if self.provider == 'codex':
            item = event.get('item') or {}
            if kind in ('item.started', 'item.updated', 'item.completed'):
                typ = item.get('type')
                if typ == 'agent_message' and kind == 'item.completed':
                    self.answer = item.get('text', '')
                    self.progress_text = str(self.answer or '')[:4000]
                elif typ == 'command_execution':
                    self.started_tools = True
                    state = 'Comando terminou' if kind == 'item.completed' else 'A executar comando'
                    return state + ': ' + str(item.get('command') or '')[:600]
                elif typ == 'file_change':
                    self.started_tools = True
                    return 'Alterações em ficheiros: ' + ', '.join(str(c.get('path', '')) for c in item.get('changes', []))[:600]
                elif typ == 'mcp_tool_call':
                    self.started_tools = True
                    state = 'Ferramenta terminou' if kind == 'item.completed' else 'A executar ferramenta'
                    hint = state + ': ' + str(item.get('server', '')) + ' · ' + str(item.get('tool', ''))
                    args = item.get('arguments') or {}
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except ValueError:
                            args = {}
                    if isinstance(args, dict):
                        # Only public command/path metadata, never arbitrary inputs or results.
                        target = args.get('cmd') or args.get('command') or args.get('file_path') or args.get('path')
                        if target:
                            hint += ': ' + str(target)[:600]
                    return hint
            if kind in ('error', 'turn.failed'):
                self.errors.append(str(event.get('message') or (event.get('error') or {}).get('message') or 'Erro do motor'))
        else:
            if kind == 'result':
                self.answer = event.get('result')
                self.errors.extend(str(e) for e in event.get('errors', []))
            if kind == 'assistant':
                hints = []
                for block in (event.get('message') or {}).get('content', []):
                    if block.get('type') == 'text':
                        self.answer = block.get('text', '')
                        self.progress_text += str(self.answer or '')[:4000]
                    if block.get('type') != 'tool_use':
                        continue
                    self.started_tools = True
                    name, args = block.get('name', ''), block.get('input') or {}
                    path = args.get('file_path') or args.get('path') or ''
                    if name in ('Bash', 'Shell'):
                        hints.append('A executar comando: ' + str(args.get('command') or '')[:600])
                    elif name in ('Read', 'Edit', 'Write', 'NotebookEdit'):
                        verb = {'Read': 'A ler', 'Edit': 'A editar', 'Write': 'A escrever', 'NotebookEdit': 'A editar'}[name]
                        hints.append(verb + ': ' + str(path)[:600])
                    elif name in ('Grep', 'Glob'):
                        hints.append('A pesquisar: ' + str(args.get('pattern', ''))[:300] + ' · ' + str(path)[:300])
                    else:
                        hints.append('Ferramenta: ' + name)
                if hints:
                    return '\n'.join(hints)[-1200:]
        return None

    def output(self):
        return self.answer if self.answer is not None else ''.join(self.plain) or '\n'.join(self.errors)
