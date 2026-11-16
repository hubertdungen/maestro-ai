"""Regex PowerShell do app.js (botão Autorizar) tem de coincidir com NOT_NAS do backend."""
import re, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from maestro_actions import NOT_NAS

_m = re.search(r"!/(\(\^\|\\n\)\\s\*\(pwsh\|.*?)/\.test\(code\)", (ROOT / "ui/app.js").read_text())
assert _m, "regex não encontrada em app.js"
JS = re.compile(_m.group(1))

BLOCKED = ["pwsh -c x", "powershell -File a", "Install-Module X", "Connect-ExchangeOnline",
           "New-DistributionGroup -Name a", "Remove-Item x", "Remove-Mailbox y",
           "echo ok\nConnect-ExchangeOnline"]
ALLOWED = ["ls", "systemctl status x", "docker ps\ncat /etc/hosts"]

def test_same_source():
    assert JS.pattern == NOT_NAS.pattern

def test_blocked():
    for c in BLOCKED:
        assert JS.search(c) and NOT_NAS.search(c), c

def test_allowed():
    for c in ALLOWED:
        assert not JS.search(c) and not NOT_NAS.search(c), c
