"""Memória textual das imagens das conversas.

As capturas em /srv/appdata/0_SERVER/pastes são apagadas ao fim de 30 dias
(scripts/clean-pastes.sh). Antes disso, cada imagem referenciada por uma mensagem
ganha uma descrição em texto (message["image_notes"][caminho]); o histórico passa a
usá-la quando o ficheiro já não existe, para o chat saber o que a imagem continha.
"""
import os
import pathlib
import subprocess
import time

MIN_AGE_DAYS = 20      # descreve a partir daqui; o apagamento é aos 30 (10 dias de tentativas)
PER_PASS = 5           # limite por passagem: poupa quota
PROMPT = (
    "Lê a imagem em {path} e escreve uma descrição que sirva de memória, em português europeu, "
    "para quem não a puder voltar a ver: transcreve o texto visível, números, estados, erros e "
    "nomes; indica o que é (ecrã de que app, foto, esquema) e o que importa nela. "
    "Máximo 1500 caracteres, sem preâmbulo."
)


def describe_with_claude(path, claude_bin, pastes_dir):
    """Haiku a ler a imagem (só ferramenta Read, só a pasta dos pastes)."""
    out = subprocess.run(
        [claude_bin, "-p", PROMPT.format(path=path), "--model", "haiku",
         "--allowedTools", "Read", "--add-dir", str(pastes_dir)],
        capture_output=True, text=True, timeout=180, cwd=str(pastes_dir),
        env={k: v for k, v in os.environ.items() if k != "CCA_TOKEN"})  # sem o router do cca
    text = (out.stdout or "").strip()
    if out.returncode != 0 or len(text) <= 20 or "blocked by hook" in text:
        return None
    return text[:1800]


def pending_images(chats, now=None, min_age_days=MIN_AGE_DAYS):
    """(chat_id, path) das imagens que existem, têm >= min_age_days e ainda não têm nota."""
    now = now or time.time()
    found = []
    for chat in chats:
        for m in chat.get("messages") or []:
            notes = m.get("image_notes") or {}
            for p in m.get("images") or []:
                try:
                    old = now - os.stat(p).st_mtime >= min_age_days * 86400
                except OSError:
                    continue
                if old and p not in notes:
                    found.append((chat["id"], p))
    return found


def run_pass(list_chats, load_chat, save_chat, describe, now=None, limit=PER_PASS, min_age_days=MIN_AGE_DAYS):
    """Descreve até `limit` imagens. O modelo é chamado ANTES de reler a conversa, para a
    janela entre ler e gravar ser mínima (o servidor também grava a conversa)."""
    done = 0
    for chat_id, path in pending_images(list_chats(), now, min_age_days)[:limit]:
        text = describe(path)
        if not text:
            continue
        chat = load_chat(chat_id)
        if not chat:
            continue
        for m in chat.get("messages") or []:
            if path in (m.get("images") or []):
                m.setdefault("image_notes", {})[path] = text
        save_chat(chat)
        done += 1
    return done


def note_for_history(message):
    """Texto para o histórico: usa a nota das imagens que já não existem em disco."""
    notes = message.get("image_notes") or {}
    return " ".join(f"[Imagem {pathlib.Path(p).name} já apagada; descrição guardada: {notes[p]}]"
                    for p in message.get("images") or [] if p in notes and not os.path.exists(p))
