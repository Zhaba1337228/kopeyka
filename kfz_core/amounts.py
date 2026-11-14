"""Amount candidates, explicit unit scopes and auditable human selection."""
import re
from decimal import Decimal
from openpyxl.utils.cell import coordinate_from_string, column_index_from_string, get_column_letter

from .extract import make_evidence
from .money import MONEY_PATTERN, amount_text, decimal_value, scaled_money, resolve_units


def declarations(lines):
    return '\n'.join(line['text'] for line in lines if re.search(
        r'единиц[аы]\s+измер|ед\.?\s*изм|(?:все\s+)?суммы\s+(?:указаны\s+)?в\s+|валюта', line['text'], re.I))


def rank_amount(text):
    if re.search(r'без\s+НДС|сумма\s+НДС|в\s+том\s+числе|налог|аванс|предоплат', text, re.I):
        return 0
    if re.search(r'(?:итого|всего).*к\s+оплате|итоговая\s+сумма|к\s+оплате|всего\s+с\s+НДС', text, re.I):
        return 4
    if re.search(r'итого|всего', text, re.I):
        return 3
    if re.search(r'стоимост|сумм|начислен', text, re.I):
        return 2
    return 1


def read_amounts(path, doc_id, extracted, meta, decision=None):
    decision = decision or {}
    candidates, evidence = [], []
    global_units = declarations(extracted['lines'])
    template_indices = []

    def add(line, raw, local='', field=None, rank=None):
        scopes = [global_units, local]
        if line.get('table'):
            scopes += ['\n'.join(r['text'] for r in extracted['tables'] if r.get('table')==line['table'] and not MONEY_PATTERN.search(r['text']))]
        multiplier, issue = resolve_units(*scopes)
        value = scaled_money(raw, multiplier) if multiplier is not None else None
        ident = f'amount-{len(candidates)}'
        ev = make_evidence(path, doc_id, line, value if value is not None else raw, line['text'][:150], ident)
        ev.update(value=amount_text(value) if value is not None else None, raw_value=str(decimal_value(raw)),
                  multiplier=str(multiplier) if multiplier is not None else None, field=field, fragment_text=line['text'])
        evidence.append(ev)
        candidates.append({'id':ev['id'], 'raw_value':ev['raw_value'], 'value':ev['value'],
                           'multiplier':ev['multiplier'], 'issue':issue, 'label':line['text'][:300],
                           'rank':rank_amount(line['text']) if rank is None else rank, 'field':field})

    if extracted['kind'] == 'xlsx':
        sheets = extracted['sheets']
        template = ('Ведомость', ('C6','C7'), 'C5','0200') if 'Ведомость' in sheets else (
            ('Расчет взносов', ('B6',), 'B5','0300') if 'Расчет взносов' in sheets else None)
        if template:
            name, cells, unit_cell, code = template
            meta['code'] = code
            for cell in cells:
                raw = sheets[name].get(cell)
                if raw is None or isinstance(raw,str) and raw.startswith('='):
                    continue
                field = {'C6':'0200.main','C7':'0200.extra_reserve','B6':'0300.total'}[cell]
                add({'text':f'{name}!{cell}: {raw}', 'sheet':name, 'cell':cell}, raw,
                    str(sheets[name].get(unit_cell,'')),field,3)
                template_indices.append(len(candidates)-1)
        else:
            for name, sheet in sheets.items():
                for cell, raw in sheet.items():
                    if isinstance(raw,bool) or not isinstance(raw,(int,float,str)):
                        continue
                    try:
                        decimal_value(raw)
                    except (ValueError, ArithmeticError):
                        continue
                    column, row = coordinate_from_string(cell)
                    left = get_column_letter(max(1,column_index_from_string(column)-1))+str(row)
                    label = str(sheet.get(left,'')) if left!=cell else ''
                    # Column headings carry units independently of the row label.
                    headers = '\n'.join(str(sheet.get(column+str(i),'')) for i in range(1,int(row))
                                        if not MONEY_PATTERN.search(str(sheet.get(column+str(i),''))))
                    add({'text':f'{label} | {raw}', 'sheet':name, 'cell':cell},raw,label+'\n'+headers,
                        rank=rank_amount(label) if label else 0)
    else:
        for line in extracted['lines']:
            matches = list(MONEY_PATTERN.finditer(line['text']))
            dates = list(re.finditer(r'\b\d{2}\.\d{2}\.\d{4}\b',line['text']))
            if re.search(r'\d{2}\.\d{2}\.\d{4}',line['text']) and not re.search(r'руб|сумм|стоим|итого|оплат',line['text'],re.I):
                continue
            for match in matches:
                if any(date.start() <= match.start() < date.end() for date in dates):
                    continue
                add(line,match[0],line['text'])

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
    elif extracted['kind']=='xlsx' and template:
        if len(template_indices)!=len(template[1]) or any(candidates[i]['issue'] for i in template_indices):
            reason='Не все исходные начисления прочитаны или единицы противоречат друг другу'
        else:
            selected=[candidates[i]['id'] for i in template_indices]
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
    amount=amount_text(sum((Decimal(ev['value']) for ev in evidence if ev['id'] in selected),Decimal(0))) if selected else None
    return {'amount':amount,'evidence_ids':selected,'candidates':candidates,'evidence':evidence,'reason':reason}
