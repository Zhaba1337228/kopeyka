"""Money is parsed and calculated in Decimal, never in binary floats."""
import re
from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal('0.01')


def decimal_value(value):
    if isinstance(value, Decimal):
        return value
    if value is None:
        raise ValueError('Денежное значение отсутствует')
    text = str(value).strip().replace('\u00a0', '').replace('\u202f', '').replace(' ', '')
    negative = text.startswith('(') and text.endswith(')')
    text = re.sub(r'(?i)(?:руб(?:лей|ля|ль)?\.?|₽)$', '', text).strip()
    if negative:
        text = text[1:-1]
    text = text.replace('−', '-').replace('–', '-').replace('—', '-')
    if ',' in text:
        text = text.replace('.', '').replace(',', '.')
    if not re.fullmatch(r'[+-]?\d+(?:\.\d+)?', text):
        raise ValueError(f'Не удалось прочитать сумму: {value}')
    result = Decimal(text)
    return -result if negative else result


def money(value):
    return decimal_value(value).quantize(CENT, rounding=ROUND_HALF_UP)


def scaled_money(value, multiplier=1):
    return (decimal_value(value) * Decimal(str(multiplier))).quantize(CENT, rounding=ROUND_HALF_UP)


def amount_text(value):
    return format(money(value), '.2f')


MONEY_PATTERN = re.compile(r'(?<![\d/.])\(?[-−–—]?\d(?:[\d \u00a0\u202f]*\d)?[,.]\d{2,6}(?!\d)\)?')
AMOUNT_PATTERN = re.compile(r'(?<![\w/.])\(?[-−–—]?\d+(?:[ \u00a0\u202f]\d{3})*(?:[,.]\d{1,6})?(?![\w/.])\)?')


def unit_values(text):
    return {Decimal('1000000') if m[1] and m[1].lower() == 'млн' else Decimal('1000') if m[1] else Decimal('1')
            for m in re.finditer(r'(?i)\b(?:(тыс|млн)\.?\s*)?(?:руб(?:лей|ля|ль)?\.?)(?![а-я])|₽', text)}


def resolve_units(*contexts):
    values = set().union(*(unit_values(text or '') for text in contexts))
    if len(values) > 1:
        return None, 'Противоречивые единицы измерения: подтвердите рубли, тысячи или миллионы'
    return next(iter(values), Decimal('1')), None


def unit_multiplier(text):
    """Recognize explicit numeric units, never multiply spelled-out amounts."""
    if re.search(r'млн\.?\s*руб', text, re.I):
        return Decimal('1000000')
    if re.search(r'тыс\.?\s*руб', text, re.I):
        return Decimal('1000')
    return Decimal('1')
