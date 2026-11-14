"""Amount candidates, explicit unit scopes and auditable human selection."""
import re
import hashlib
import json
from decimal import Decimal

from .extract import make_evidence
from .money import MONEY_PATTERN, AMOUNT_PATTERN, amount_text, decimal_value, scaled_money, resolve_units
from .tabular import spreadsheet_cells, component_plan

SOURCE_LOCATION = ('sheet','cell','table','row','paragraph','page','bbox','token_start')


def pin_source(decision, document_id, result):
    """Bind an existing decision to its source before a parser upgrade/recalculation."""
    decision=dict(decision)
    decision.pop('amount_locator',None)
    source_id=decision.get('amount_source')
    if not source_id:return decision
    doc=next((d for d in result.get('documents',[]) if d.get('id')==document_id),None)
    permitted=set(doc.get('preview_evidence_ids',[])+doc.get('evidence_ids',[])) if doc else set()
    ev=result.get('evidence',{}).get(source_id)
    if not ev or source_id not in permitted:
        raise ValueError('Выбранный источник суммы отсутствует в документе')
    locator={key:ev[key] for key in SOURCE_LOCATION if ev.get(key) is not None}
    if ev.get('raw_value') is not None:locator['raw_value']=ev['raw_value']
    if not locator:raise ValueError('У источника нет проверяемого положения в документе')
    decision['amount_locator']=locator
    return decision


def declarations(lines):
    return '\n'.join(line['text'] for line in lines if re.search(
        r'единиц[аы]\s+измер|ед\.?\s*изм|(?:все\s+)?суммы\s+(?:указаны\s+)?в\s+|валюта', line['text'], re.I))


def rank_amount(text):
    if re.search(r'без\s+НДС|сумма\s+НДС|в\s+том\s+числе|налог|аванс|предоплат|справочно|удержан', text, re.I):
        return 0
    if re.search(r'(?:итого|всего).*к\s+оплате|итоговая\s+сумма|к\s+оплате|всего\s+с\s+НДС', text, re.I):
        return 4
    if re.search(r'НДС',text,re.I) and not re.search(r'с\s+(?:уч[её]том\s+)?НДС',text,re.I):
        return 0
    if re.search(r'итого|всего', text, re.I):
        return 3
    if re.search(r'стоимост|сумм|начислен', text, re.I):
        return 2
    return 1


def read_amounts(path, doc_id, extracted, meta, decision=None):
    decision = decision or {}
    candidates, evidence = [], []
    global_units = declarations(extracted['lines'])

    def add(line, raw, local='', field=None, rank=None):
        scopes = [global_units, local]
        if line.get('table'):
            scopes += ['\n'.join(r['text'] for r in extracted['tables'] if r.get('table')==line['table'] and not MONEY_PATTERN.search(r['text']))]
        multiplier, issue = resolve_units(*scopes)
        value = scaled_money(raw, multiplier) if multiplier is not None else None
        location={key:line[key] for key in SOURCE_LOCATION if key in line}
        location['raw_value']=str(decimal_value(raw))
        ident='amount-'+hashlib.sha256(json.dumps(location,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:16]
        ev = make_evidence(path, doc_id, line, value if value is not None else raw, line['text'][:150], ident)
        ev.update(value=amount_text(value) if value is not None else None, raw_value=str(decimal_value(raw)),
                  multiplier=str(multiplier) if multiplier is not None else None, field=field, fragment_text=line['text'])
        evidence.append(ev)
        candidates.append({'id':ev['id'], 'raw_value':ev['raw_value'], 'value':ev['value'],
                           'multiplier':ev['multiplier'], 'issue':issue, 'label':line['text'][:300],
                           'rank':rank_amount(line['text']) if rank is None else rank, 'field':field})

    if extracted['kind'] == 'xlsx':
        for item in spreadsheet_cells(extracted):
            if item['formula']:
                continue  # No guessed/cached evaluation of arbitrary spreadsheet formulas.
            add(item['line'],item['raw'],item['local'],item['field'],
                0 if item['excluded'] else rank_amount(item['label']))
    else:
        for line in extracted['lines']:
            has_amount_label = re.search(r'руб|₽|сумм|стоим|итого|всего|оплат|начислен',line['text'],re.I)
            matches = list((AMOUNT_PATTERN if has_amount_label else MONEY_PATTERN).finditer(line['text']))
            dates = list(re.finditer(r'\b\d{2}\.\d{2}\.\d{4}\b',line['text']))
            if re.search(r'\d{2}\.\d{2}\.\d{4}',line['text']) and not re.search(r'руб|сумм|стоим|итого|оплат',line['text'],re.I):
                continue
            for index,match in enumerate(matches):
                if any(date.start() <= match.start() < date.end() for date in dates):
                    continue
                prefix=line['text'][matches[index-1].end() if index else 0:match.start()]
                label=prefix if len(matches)>1 and re.search(r'итого|всего|оплат|НДС|налог|аванс|сумм|стоим|начислен',prefix,re.I) else line['text']
                add(dict(line,token_start=match.start()),match[0],line['text'],rank=rank_amount(label))

    # Even an unreadable amount can be corrected against an explicit source fragment.
    if not candidates:
        for index,line in enumerate(extracted['lines'][:200]):
            ev = make_evidence(path,doc_id,line,0,line['text'][:150],f'source-{index}')
            ev.update(value=None,raw_value=None,multiplier=None,fragment_text=line['text'])
            evidence.append(ev)
            candidates.append({'id':ev['id'],'raw_value':None,'value':None,'multiplier':None,
                               'issue':'Сумма не распознана; подтвердите по источнику','label':line['text'][:300],'rank':0,'field':None})
    selected = []
    reason = None
    source_id = decision.get('amount_source')
    if source_id:
        chosen = next((c for c in candidates if c['id']==source_id),None)
        if not chosen and decision.get('amount_locator'):
            locator=decision['amount_locator']
            matching=[ev for ev in evidence if all(ev.get(k)==v for k,v in locator.items())]
            if len(matching)==1:
                source_id=matching[0]['id']
                chosen=next(c for c in candidates if c['id']==source_id)
                decision['amount_source']=source_id
        if not chosen:
            raise ValueError('Выбранный источник суммы отсутствует в документе')
        raw = decision.get('amount_raw',chosen['raw_value'])
        multiplier = str(decision.get('amount_multiplier',chosen['multiplier']))
        if multiplier not in ('1','1000','1000000') or raw is None:
            raise ValueError('Подтвердите сумму и единицу измерения')
        value = amount_text(scaled_money(raw,multiplier))
        source = next(e for e in evidence if e['id']==source_id)
        # Keep the parsed candidate intact; the accepted manual value is a separate evidence record.
        reviewed = dict(source,id=source_id+'-reviewed',value=value,raw_value=str(decimal_value(raw)),
                        multiplier=multiplier,manual=True,original_value=source.get('value'),
                        review_reason=decision.get('reason',''))
        evidence.append(reviewed)
        selected = [reviewed['id']]
    else:
        rank=max((c['rank'] for c in candidates if c['raw_value'] is not None),default=0)
        best=[c for c in candidates if c['rank']==rank and c['raw_value'] is not None]
        if rank==0 or not best:
            reason='Не найдена однозначная итоговая сумма; выберите источник и подтвердите значение'
        elif any(c['issue'] for c in best):
            reason='Единицы измерения противоречат друг другу; требуется подтверждение'
        elif len({c['value'] for c in best})!=1:
            reason='Найдено несколько итоговых сумм; выберите нужную по источнику'
        else:
            selected=[best[-1]['id']]
        if extracted['kind']=='xlsx':
            code,parts,part_error=component_plan(candidates)
            if code:
                if meta.get('code') and meta['code']!=code:
                    selected=[];reason='Назначение начислений противоречит статье документа'
                else:
                    meta['code']=code
                    totals=[c for c in candidates if (c['rank']>=3 and not c['field']) or c['field']==code+'.total']
                    if parts:
                        unknown=[c for c in candidates if c not in parts and c not in totals and c['rank']>0 and c.get('value') not in (None,'0.00')]
                        invalid=any(c['issue'] or c['value'] is None for c in parts)
                        expected=sum((Decimal(c['value']) for c in parts),Decimal(0)) if not invalid else None
                        if invalid or unknown or any(t['value'] is None or Decimal(t['value'])!=expected for t in totals):
                            selected=[];reason='Состав начислений, единицы или итог противоречат друг другу; проверьте источники'
                        else:
                            selected=[c['id'] for c in parts];reason=None
                    elif not totals or reason:
                        selected=[];reason=part_error
    amount=amount_text(sum((Decimal(ev['value']) for ev in evidence if ev['id'] in selected),Decimal(0))) if selected else None
    return {'amount':amount,'evidence_ids':selected,'candidates':candidates,'evidence':evidence,'reason':reason}
