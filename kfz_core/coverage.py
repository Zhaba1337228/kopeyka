"""Detailed indicators are checked separately and never added to article totals."""
from decimal import Decimal
from .money import money, amount_text


def verify_indicators(selected,documents,evidence,rows,months):
    result=[]
    parents={r['code']:r for r in rows}
    for metric in selected.get('indicators',[]):
        field=metric.get('field')
        refs=[]; present=set()
        for doc in documents:
            if doc['status']!='accepted' or not field or doc.get('code')!=field.split('.')[0]:continue
            found=[ref for ref in doc['evidence_ids'] if evidence[ref].get('field')==field]
            if not found and field.split('.')[1] in ('extra_reserve','without_reserve'):
                components=('extra','reserve') if field.endswith('extra_reserve') else ('main','extra')
                groups=[[ref for ref in doc['evidence_ids'] if evidence[ref].get('field')==field.split('.')[0]+'.'+part] for part in components]
                if all(groups):found=[ref for group in groups for ref in group]
            refs.extend(found)
            if found and doc.get('period_start'):present.add(doc['period_start'][:7])
        reasons=[]
        confirmed=amount_text(sum((money(evidence[r]['value']) for r in refs),Decimal(0))) if refs else None
        if metric['reported'] is None: reasons.append('Значение в КФЗ не распознано')
        if not field:
            reasons.append('В комплекте нет отдельного источника для проверки этого показателя')
        elif not refs or any(month not in present for month in months):
            reasons.append('Не хватает исходных начислений за все месяцы периода')
        if field:
            reasons.extend(parents.get(field.split('.')[0],{}).get('reasons',[]))
        difference=amount_text(money(metric['reported'])-money(confirmed)) if metric['reported'] is not None and confirmed is not None else None
        status='Недостаточно данных' if reasons else ('Совпадает' if money(difference)==0 else 'Расхождение')
        result.append({**metric,'confirmed':confirmed,'difference':difference,'status':status,'reasons':list(dict.fromkeys(reasons)),
                       'evidence_ids':refs,'included_in_total':False})
    return result
