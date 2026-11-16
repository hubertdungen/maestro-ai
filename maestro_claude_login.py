"""Login do Claude dentro do Maestro: corre `claude auth login` num pty, devolve o link e recebe o código."""
from __future__ import annotations

import os
import pty
import re
import select
import subprocess
import threading
import time

import maestro as M

_LOCK = threading.Lock()
_S: dict = {}  # ponytail: uma só sessão de login de cada vez


def _binary() -> str:
    b = M.NODE_BIN / "claude"
    return str(b if b.exists() else M.NPM_BIN / "claude")


def _read(fd: int, secs: float) -> str:
    out, end = b"", time.time() + secs
    while time.time() < end:
        if select.select([fd], [], [], 0.3)[0]:
            try:
                out += os.read(fd, 8192)
            except OSError:
                break
            if b"Paste code" in out:
                break
    return out.decode(errors="replace")


def _log(event: str, who: str = "") -> None:
    """One line per attempt: when a button "does nothing", this tells whether the click reached the server."""
    try:
        with (M.STATE_DIR / "claude-login.log").open("a") as fh:
            fh.write(time.strftime("%Y-%m-%d %H:%M:%S") + " " + event + (" · " + who if who else "") + "\n")
    except OSError:
        pass


def start(who: str = "") -> dict:
    result = _start()
    _log("início: " + ("link devolvido" if result.get("ok") else "FALHOU: " + str(result.get("error"))), who)
    return result


def _start() -> dict:
    with _LOCK:
        stop()
        fd, slave = pty.openpty()
        env = {k: v for k, v in os.environ.items() if k != "CCA_TOKEN"}
        env["BROWSER"] = "true"  # não abrir nada no servidor
        proc = subprocess.Popen([_binary(), "auth", "login"], stdin=slave, stdout=slave, stderr=slave,
                                close_fds=True, env=env)
        os.close(slave)
        text = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)", "", _read(fd, 15))
        m = re.search(r"https://\S+oauth/authorize\S+", text)
        if not m:
            proc.kill()
            os.close(fd)
            return {"ok": False, "error": "O CLI não devolveu o link de login"}
        _S.update(proc=proc, fd=fd, at=time.time())
        threading.Timer(600, stop).start()
        return {"ok": True, "url": m.group(0)}


def stop() -> None:
    p = _S.pop("proc", None)
    fd = _S.pop("fd", None)
    if p and p.poll() is None:
        p.kill()
    if fd is not None:
        try:
            os.close(fd)
        except OSError:
            pass


def submit(code: str, who: str = "") -> dict:
    result = _submit(code)
    _log("código: " + ("aceite" if result.get("ok") else "RECUSADO: " + str(result.get("error"))[:120]), who)
    return result


def _submit(code: str) -> dict:
    code = (code or "").strip()
    with _LOCK:
        p, fd = _S.get("proc"), _S.get("fd")
        if not p or p.poll() is not None or not code or len(code) > 500:
            return {"ok": False, "error": "Sessão de login expirada: volta a carregar em «Iniciar sessão»"}
        os.write(fd, code.encode() + b"\r")
        for _ in range(60):
            if p.poll() is not None:
                break
            time.sleep(0.5)
        ok = p.poll() == 0
        tail = _read(fd, 0.5) if not ok else ""
        stop()
    try:
        (M.STATE_DIR / "claude-auth.json").unlink()  # força nova leitura do estado
    except OSError:
        pass
    return {"ok": ok, "error": "" if ok else (tail.strip()[-200:] or "Código recusado")}
