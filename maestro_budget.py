"""Persistent conservative DeepSeek admission budget, shared across Maestro processes.

API and CLI share a ledger; CLI requests pass through maestro_deepseek_gateway.
Rates: https://api-docs.deepseek.com/quick_start/pricing/ verified 2026-11-13.
EUR accounting uses 1.10 EUR/USD as an explicit protective allowance, not live FX.
Unknown HTTP outcomes retain the full reservation. No automatic top-ups.
"""
import datetime as dt
import fcntl
import json
import pathlib
from contextlib import contextmanager
from zoneinfo import ZoneInfo

STATE = pathlib.Path.home()/'.local/state/maestro/deepseek-budget.json'
TZ = ZoneInfo('Europe/Lisbon')
CAP = 15.0
EUR_PER_USD = 1.10
RATES_VALID_UNTIL = dt.date(2026, 12, 12)
RATES = {'deepseek-flash': (.30, 1.20), 'deepseek-v4-flash': (.30, 1.20),
         'deepseek-v4-flash-vision-exp': (.30, 1.20), 'deepseek-v4-pro': (1.32, 3.96)}
YELLOW_EXTRA = .50
RED_RESERVE = .20
PRICING_SOURCE = 'https://api-docs.deepseek.com/quick_start/pricing/'


HEADROOM = .1


def daily_limit(daily, necessity='normal'):
    return daily + {'normal': 0, 'essential': YELLOW_EXTRA-RED_RESERVE,
                    'critical': YELLOW_EXTRA}.get(necessity, 0)


def cycle(now):
    month = now.year*12+now.month-1-(now.day < 20)
    start = dt.date(month//12, month%12+1, 20)
    month += 1
    end = dt.date(month//12, month%12+1, 20)
    return start, end


def _daily(data, now):
    # Elastic: what is left of the cycle (before today) spread over the days left, today included.
    start, end = cycle(now)
    before = sum(v for k, v in data['days'].items() if start.isoformat() <= k < min(end.isoformat(), now.date().isoformat()))
    return max(0, CAP-before)/(end-now.date()).days


@contextmanager
def ledger(path=None):
    path = pathlib.Path(path or STATE)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        # Corruption fails closed instead of silently resetting spending.
        data = json.loads(path.read_text()) if path.exists() else {'started_at': dt.datetime.now(TZ).isoformat(), 'days': {}}
        yield data
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        tmp.replace(path)


def _snapshot(data, now):
    start, end = cycle(now)
    spent = sum(v for k, v in data['days'].items() if start.isoformat() <= k < end.isoformat())
    daily = _daily(data, now)
    today = data['days'].get(now.date().isoformat(), 0)
    return dict(cap_eur=CAP, daily_eur=round(daily, 6), spent_eur=round(spent, 6),
                today_eur=round(today, 6), remaining_eur=round(max(0, CAP-spent), 6),
                # Reservations are conservative: with under 10% of the day left the next request is refused,
                # so the engine must stop being offered before that (it failed 44 times as "available").
                available=spent < CAP-1e-9 and today < daily*(1-HEADROOM) and now.date() <= RATES_VALID_UNTIL,
                reserve_available=spent < CAP-1e-9 and today < daily+YELLOW_EXTRA-RED_RESERVE-1e-9 and now.date() <= RATES_VALID_UNTIL,
                critical_available=spent < CAP-1e-9 and today < daily+YELLOW_EXTRA-1e-9 and now.date() <= RATES_VALID_UNTIL,
                cycle_start=start.isoformat(), reset_at=end.isoformat(),
                started_at=data['started_at'], eur_per_usd=EUR_PER_USD,
                accounting='Reserva conservadora: pico/sem cache; 1 USD = 1,10 EUR de orçamento',
                cli_allowed=True, rates_valid_until=RATES_VALID_UNTIL.isoformat(),
                yellow_extra_eur=YELLOW_EXTRA, red_reserve_eur=RED_RESERVE,
                yellow_limit_eur=round(daily+YELLOW_EXTRA-RED_RESERVE, 6),
                red_limit_eur=round(daily+YELLOW_EXTRA, 6),
                zone='red' if today >= daily+YELLOW_EXTRA-RED_RESERVE else 'yellow' if today >= daily else 'normal',
                pricing_source=PRICING_SOURCE, pricing_verified_at='2026-11-13')


def status(path=None, now=None):
    with ledger(path) as data:
        return _snapshot(data, now or dt.datetime.now(TZ))


def require_bounded_cli():
    if not status()['critical_available']:
        raise RuntimeError('DeepSeek: orçamento normal esgotado; reserva exige necessidade justificada')


def request(payload, send, path=None, now=None, necessity='normal', reason='', request_fee_usd=0):
    now = now or dt.datetime.now(TZ)
    rates = RATES.get(payload.get('model'))
    if not rates or now.date() > RATES_VALID_UNTIL:
        raise RuntimeError('DeepSeek: preço do modelo por validar; chamada bloqueada pelo orçamento')
    if payload.get('stream') or payload.get('n', 1) != 1:
        raise ValueError('DeepSeek: formato sem controlo de custo')
    maximum = payload.get('max_tokens')
    if not isinstance(maximum, int) or maximum < 1 or maximum > 32768:
        raise ValueError('DeepSeek: limite de saída obrigatório (1 a 32768 tokens)')
    if necessity not in ('normal', 'essential', 'critical') or (necessity != 'normal' and not str(reason).strip()):
        raise ValueError('DeepSeek: reserva requer necessidade e justificação explícitas')
    # UTF-8 bytes upper-bound text tokens; allowance covers message/tool framing.
    input_bound = len(json.dumps(payload, ensure_ascii=False).encode('utf-8'))+4096
    if request_fee_usd not in (0, .00075):
        raise ValueError('DeepSeek: tarifa de ferramenta não validada')
    reserved = EUR_PER_USD*((input_bound*rates[0]+maximum*rates[1])/1_000_000+request_fee_usd)
    day = now.date().isoformat()
    with ledger(path) as data:
        snap = _snapshot(data, now)
        start, end = cycle(now)
        spent = sum(v for k, v in data['days'].items() if start.isoformat() <= k < end.isoformat())
        today = data['days'].get(day, 0)
        limit = daily_limit(snap['daily_eur'], necessity)
        if (reserved > CAP-spent or reserved > limit-today):
            raise RuntimeError('DeepSeek: orçamento diário/mensal insuficiente; aguarda reposição ou usa outro fornecedor')
        data['days'][day] = data['days'].get(day, 0)+reserved
        if today+reserved > snap['daily_eur']:
            data.setdefault('exceptions', []).append(dict(at=now.isoformat(), necessity=necessity,
                                                         reason=str(reason)[:600], reserved_eur=reserved))
    result = send()  # Exceptions retain reservation: server may have billed it.
    usage = result.get('usage') or {}
    prompt, completion = usage.get('prompt_tokens'), usage.get('completion_tokens')
    if isinstance(prompt, int) and isinstance(completion, int) and 0 <= prompt <= input_bound and 0 <= completion <= maximum:
        actual = EUR_PER_USD*((prompt*rates[0]+completion*rates[1])/1_000_000+request_fee_usd)
        with ledger(path) as data:
            data['days'][day] = max(0, data['days'].get(day, 0)-(reserved-actual))
    return result
