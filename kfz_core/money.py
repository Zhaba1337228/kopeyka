"""Money is parsed and calculated in Decimal, never in binary floats."""
import re
from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal('0.01')


def money(value):
    if isinstance(value, Decimal):
        return value.quantize(CENT, rounding=ROUND_HALF_UP)
    if value is None:
        raise ValueError('Денежное значение отсутствует')
    text = str(value).strip().replace('\u00a0', '').replace('\u202f', '').replace(' ', '')
    negative = text.startswith('(') and text.endswith(')')
    text = re.sub(r'[^\d.,+−-]', '', text).replace('−', '-')
    if ',' in text:
        text = text.replace('.', '').replace(',', '.')
    if not text or text in ('-', '+'):
        raise ValueError(f'Не удалось прочитать сумму: {value}')
    result = Decimal(text).quantize(CENT, rounding=ROUND_HALF_UP)
    return -result if negative else result


def amount_text(value):
    return format(money(value), '.2f')


MONEY_PATTERN = re.compile(r'(?<![\d/.])\(?[-−]?\d(?:[\d \u00a0\u202f]*\d)?[,.]\d{2}(?!\d)\)?')


def unit_multiplier(text):
    """Recognize explicit numeric units, never multiply spelled-out amounts."""
    if re.search(r'млн\.?\s*руб', text, re.I):
        return Decimal('1000000')
    if re.search(r'тыс\.?\s*руб', text, re.I):
        return Decimal('1000')
    return Decimal('1')
