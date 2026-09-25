"""Diagnósticos de preços medidos; nenhum preço ausente é execução."""
import statistics
import hashlib
import json
from dataclasses import dataclass

from .config import production_thresholds
from .production_policy import finite_number
from .production_policy import selection_reasons
from .config import production_policy_fingerprint
from .markets import validate_selection_line
from .timeutil import parse_kickoff, utc_key


@dataclass(frozen=True)
class PricedSignal:
    payload_json: str

    def to_dict(self):
        return json.loads(self.payload_json)


def build_priced_signal(*, quote, selected_quote, model_prob, fair_prob, ev,
                        spread, books, decision_timestamp, gate, model_fingerprint):
    decision = utc_key(decision_timestamp)
    scope = lambda q:(q.event_id,q.market,q.selection,q.line)
    if scope(quote)!=scope(selected_quote):
        raise ValueError('QUOTE_MISMATCH')
    for q in (quote,selected_quote):
        if not q.event_id or not q.provider or not q.bookmaker:
            raise ValueError('MISSING_PROVENANCE')
        if utc_key(q.timestamp)>decision:
            raise ValueError('FUTURE_QUOTE')
        if decision>=utc_key(q.kickoff):
            raise ValueError('EXPIRED_EVENT')
        problem = validate_selection_line(q.market,q.selection,q.line)
        if problem:
            raise ValueError(problem)
    latest = {}
    for q in books:
        if scope(q)!=scope(quote) or not q.provider or not q.bookmaker:
            continue
        age = (parse_kickoff(decision)-parse_kickoff(q.timestamp)).total_seconds()
        if not 0<=age<=900:
            continue
        if q.bookmaker not in latest or utc_key(q.timestamp)>utc_key(latest[q.bookmaker].timestamp):
            latest[q.bookmaker]=q
    selected_age = (parse_kickoff(decision)-parse_kickoff(selected_quote.timestamp)).total_seconds()
    fresh = selected_age<=900
    valid_model = finite_number(model_prob) and 0<=model_prob<=1 and bool(model_fingerprint)
    valid_fair = finite_number(fair_prob) and 0<fair_prob<1
    edge = model_prob-fair_prob if valid_model and valid_fair else None
    # Recompute dispersion from the eligible quotes; caller cannot lower it.
    actual_spread = statistics.pstdev(q.price for q in latest.values()) if latest else None
    reasons = selection_reasons(edge=edge,ev=ev,spread=actual_spread,books_count=len(latest),
        market=quote.market,gate=gate,quote_valid=fresh and bool(quote.provider))
    if not valid_model:
        reasons += ('MODEL_UNAVAILABLE',)
    identity = dict(event=quote.event_id,market=quote.market,selection=quote.selection,
        observed=quote.price,selected=selected_quote.price,book=selected_quote.bookmaker,
        observed_at=quote.timestamp,selected_at=selected_quote.timestamp,decision=decision,
        model=model_fingerprint,policy=production_policy_fingerprint())
    digest = hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    sharp = {name:None for name in ('Pinnacle','Betfair','Bet365')}
    for q in latest.values():
        for name in sharp:
            if q.bookmaker.casefold()==name.casefold():
                sharp[name]=dict(price=q.price,timestamp=q.timestamp,book=q.bookmaker)
    payload = dict(signal_id=digest,alpha_id='calibrated_value',event_id=quote.event_id,
        market=quote.market,selection=quote.selection,line=quote.line,decision_timestamp=decision,
        observed_price=quote.price,selected_price=selected_quote.price,executed_price=None,
        closing_price=None,book=selected_quote.bookmaker,model_prob=model_prob if valid_model else None,
        market_prob=1/quote.price,fair_prob=fair_prob if valid_fair else None,edge=edge,ev=ev,
        spread=actual_spread,books_count=len(latest),execution_status='UNKNOWN',
        freshness='LIVE' if fresh else 'STALE',evidence_status='RESEARCH' if reasons else 'FORTE',
        production='NO_BET' if reasons else 'REVIEW',stake=0.,research_reasons=list(reasons),
        policy_fingerprint=production_policy_fingerprint(),model_fingerprint=model_fingerprint,
        provenance=dict(provider=quote.provider,observed_at=quote.timestamp,
                        selected_provider=selected_quote.provider,selected_at=selected_quote.timestamp),
        sharp_reference=sharp,bookmaker_close=None,exchange_close=None,consensus_close=None)
    return PricedSignal(json.dumps(payload,allow_nan=False))


def execution_diagnostics(observed_price, executed_price, closing_price):
    for name,value in (('observed',observed_price),('executed',executed_price),('closing',closing_price)):
        if value is None and name != 'observed':
            continue
        if not finite_number(value) or value <= 1:
            raise ValueError(f'invalid {name} price')
    absolute = executed_price-observed_price if executed_price is not None else None
    return dict(execution_status='MEASURED' if executed_price is not None else 'UNKNOWN',
                absolute=absolute, relative=absolute/observed_price if absolute is not None else None,
                clv_before=observed_price/closing_price-1 if closing_price is not None else None,
                clv_after=executed_price/closing_price-1 if executed_price is not None and closing_price is not None else None)


def execution_erosion(rows):
    paired = [r for r in rows if r.get('execution_status')=='MEASURED'
              and finite_number(r.get('clv_before')) and finite_number(r.get('clv_after'))]
    if not paired:
        return dict(n=0,ratio=None,status='UNKNOWN',clv_before=None,clv_after=None)
    before = statistics.fmean(r['clv_before'] for r in paired)
    after = statistics.fmean(r['clv_after'] for r in paired)
    ratio = (before-after)/before if before>0 else None
    return dict(n=len(paired), ratio=ratio, clv_before=before, clv_after=after,
                status=('UNKNOWN' if ratio is None else 'EXECUTION_EROSION'
                        if ratio>production_thresholds()['max_execution_erosion'] else 'MEASURED'))
