"""Article identity is read from documents; familiar names are only fallbacks."""
import re

KNOWN_TITLES = {
    '0200': 'Оплата труда', '0300': 'Страховые взносы',
    '0800': 'Общепроизводственные расходы', '0900': 'Общехозяйственные расходы',
}


def explicit_codes(text):
    return sorted(set(re.findall(
        r'(?:стать[яеию]\s*(?:калькуляции|затрат|расходов)?|код\s*статьи)\s*[:№#-]?\s*(\d{4})(?!\d)',
        text, re.I)))


def title_for(code, title=None):
    return title or KNOWN_TITLES.get(code) or f'Статья {code}'
