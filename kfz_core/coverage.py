"""Detailed indicators are checked separately and never added to article totals."""
from decimal import Decimal
from .money import money, amount_text, decimal_value, quantity_text
from .periods import missing_months


def verify_indicators(selected,documents,evidence,rows,months,start=None,end=None):
    result=[]
    parents={r['code']:r for r in rows}
    for metric in selected.get('indicators',[]):
        field=metric.get('field')
        refs=[]; supporting=[]
        for doc in documents:
            if doc['status']!='accepted' or not field:continue
            if field.startswith(('0200.','0300.')) and doc.get('code')!=field.split('.')[0]:continue
            allowed=list(dict.fromkeys(doc['evidence_ids']+doc.get('indicator_evidence_ids',[])))
            found=[ref for ref in allowed if evidence[ref].get('field')==field]
            if not found and field.split('.')[1] in ('extra_reserve','without_reserve'):
                components=('extra','reserve') if field.endswith('extra_reserve') else ('main','extra')
                groups=[[ref for ref in doc['evidence_ids'] if evidence[ref].get('field')==field.split('.')[0]+'.'+part] for part in components]
                if all(groups):found=[ref for group in groups for ref in group]
            refs.extend(found)
            if found:supporting.append(doc)
        reasons=[]
        formatter = quantity_text if field=='labor.total' else amount_text
        confirmed=formatter(sum((decimal_value(evidence[r]['value']) for r in refs),Decimal(0))) if refs else None
        if metric['reported'] is None: reasons.append('Значение в КФЗ не распознано')
        if not field:
            reasons.append('В комплекте нет отдельного источника для проверки этого показателя')
        elif not refs or missing_months(supporting,months,start,end):
            reasons.append('Не хватает исходных начислений за все месяцы периода')
        if field:
            reasons.extend(parents.get(field.split('.')[0],{}).get('reasons',[]))
            for doc in documents:
                if doc.get('indicator_only') and field in doc['indicator_only'].split(',') and doc['status'] in ('ambiguous','error'):
                    reasons.append(doc['reason'])
        difference=formatter(decimal_value(metric['reported'])-decimal_value(confirmed)) if metric['reported'] is not None and confirmed is not None else None
        status='Недостаточно данных' if reasons else ('Совпадает' if decimal_value(difference)==0 else 'Расхождение')
        result.append({**metric,'confirmed':confirmed,'difference':difference,'status':status,'reasons':list(dict.fromkeys(reasons)),
                       'evidence_ids':refs,'included_in_total':False})
    return result
