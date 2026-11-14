"""Explicit, source-bound corrections of recognised report cells."""
from copy import deepcopy
from decimal import Decimal
from .money import scaled_money, amount_text, quantity_text


def apply_report_decisions(candidate, decisions):
    selected = deepcopy(candidate)
    if not isinstance(decisions, dict) or len(decisions)>100:
        raise ValueError('Недопустимые исправления КФЗ')
    metrics = {metric['code']:metric for metric in selected.get('indicators',[])}
    for code, decision in decisions.items():
        ev = selected['evidence'].get(code)
        if not isinstance(decision,dict) or not ev or decision.get('source') not in (ev['id'],ev['id']+'-reviewed'):
            raise ValueError('Фрагмент КФЗ не принадлежит выбранному показателю и строке отчёта')
        reason = decision.get('reason','')
        if not isinstance(reason,str) or not reason.strip() or len(reason)>1000:
            raise ValueError('Укажите основание исправления КФЗ (до 1000 символов)')
        multiplier = str(decision.get('multiplier','1'))
        labor = metrics.get(code,{}).get('field')=='labor.total'
        if multiplier not in ('1','1000','1000000') or (labor and multiplier!='1'):
            raise ValueError('Проверьте единицу показателя КФЗ')
        value = quantity_text(decision.get('raw')) if labor else amount_text(scaled_money(decision.get('raw'),multiplier))
        old_id = ev['id']
        reviewed = dict(ev,id=old_id+'-reviewed',manual=True,review_reason=reason.strip(),
                        original_value=ev.get('value'),raw_value=str(decision.get('raw')),
                        multiplier=multiplier,value=value,unit='чел.-мес.' if labor else '₽')
        selected['evidence'][code+'-original'] = ev
        selected['evidence'][code] = reviewed
        if code in selected['values']:
            selected['values'][code] = value
            selected.get('column_errors',{}).pop(code,None)
            if code=='total':
                selected['errors']=[error for error in selected['errors'] if not error.startswith('Не найден единственный итог КФЗ')]
        elif code in metrics:
            metrics[code]['reported'] = value
        else:
            raise ValueError('Показатель КФЗ недоступен для исправления')
        for metric in metrics.values():
            metric['report_evidence_ids'] = [reviewed['id'] if ref==old_id else ref for ref in metric.get('report_evidence_ids',[])]
    # Derived figures follow their source cells; they cannot be corrected independently.
    composite=metrics.get('detail-extra-reserve')
    if composite and all(code in metrics for code in ('detail-8','detail-9')):
        parts=[metrics[code] for code in ('detail-8','detail-9')]
        composite['reported']=amount_text(sum(Decimal(part['reported']) for part in parts)) if all(part['reported'] is not None for part in parts) else None
    if selected['values'] and all(code in decisions for code in selected['values']):
        selected['errors']=[error for error in selected['errors'] if not error.startswith('Противоречивые единицы измерения')]
    return selected
