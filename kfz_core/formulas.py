"""Bounded, read-only Decimal evaluation of a small accounting formula grammar.

No eval, Excel process, macros, external links or cached values are used.
Unsupported formulas are explicit errors, never zero or stale cached results.
"""
from decimal import Decimal, ROUND_HALF_UP, localcontext
import re
from openpyxl.formula import Tokenizer
from openpyxl.utils.cell import range_boundaries, get_column_letter
from .money import decimal_value


class FormulaError(ValueError):
    pass


class FormulaReader:
    def __init__(self, sheets):
        self.sheets = sheets
        self.cache = {}
        self.active = set()
        self.budget = 0

    def cell(self, sheet, address):
        address = address.replace('$', '').upper()
        key = (sheet, address)
        if key in self.cache:
            return self.cache[key]
        self.budget += 1
        if self.budget > 10000 or len(self.active) >= 64:
            raise FormulaError('Превышен лимит вычисления формулы')
        if key in self.active:
            raise FormulaError('Циклическая ссылка в формуле')
        if sheet not in self.sheets:
            raise FormulaError('Лист формулы не найден')
        value = self.sheets[sheet].get(address)
        # Blank/text cells are deliberately not silently coerced to zero.
        if value is None or isinstance(value, bool):
            raise FormulaError('В формуле пустая или логическая ячейка')
        self.active.add(key)
        try:
            if isinstance(value, str) and value.startswith('='):
                result, refs = self.expression(sheet, value)
            else:
                result, refs = decimal_value(value), set()
            if not result.is_finite() or abs(result) > Decimal('1e18'):
                raise FormulaError('Результат формулы вне допустимого диапазона')
            answer = result, refs | {key}
            self.cache[key] = answer
            return answer
        except (ArithmeticError, ValueError, TypeError) as error:
            raise FormulaError(str(error)) from error
        finally:
            self.active.remove(key)

    def expression(self, sheet, expression):
        if len(expression) > 4096:
            raise FormulaError('Слишком длинная формула')
        try:
            tokens = [t for t in Tokenizer(expression).items if t.type != 'WHITE-SPACE']
        except Exception as error:
            raise FormulaError('Не удалось разобрать формулу') from error
        if len(tokens) > 512:
            raise FormulaError('Слишком сложная формула')
        position = 0
        refs = set()

        def reference(value):
            target, address = sheet, value
            if '!' in value:
                target, address = value.rsplit('!', 1)
                target = target.strip("'").replace("''", "'")
            if not re.fullmatch(r'\$?[A-Z]{1,3}\$?[1-9]\d{0,4}(?::\$?[A-Z]{1,3}\$?[1-9]\d{0,4})?', address, re.I) or '[' in target or ']' in target:
                raise FormulaError('Внешняя, именованная или неподдерживаемая ссылка')
            left, top, right, bottom = range_boundaries(address.replace('$','').upper())
            if (right-left+1)*(bottom-top+1) > 10000:
                raise FormulaError('Слишком большой диапазон формулы')
            values = []
            for row in range(top, bottom+1):
                for column in range(left, right+1):
                    result, dependencies = self.cell(target, f'{get_column_letter(column)}{row}')
                    refs.update(dependencies); values.append(result)
            return values if ':' in address else values[0]

        def scalar(value):
            if isinstance(value, list):
                raise FormulaError('Диапазон допустим только в SUM')
            return value

        def parse(minimum=0, depth=0):
            nonlocal position
            if depth > 64 or position >= len(tokens):
                raise FormulaError('Некорректная вложенность формулы')
            token = tokens[position]; position += 1
            if token.type == 'OPERAND' and token.subtype == 'NUMBER':
                left = Decimal(token.value)
                if not left.is_finite() or abs(left.adjusted()) > 100:
                    raise FormulaError('Числовая константа формулы вне допустимого диапазона')
            elif token.type == 'OPERAND' and token.subtype == 'RANGE':
                left = reference(token.value)
            elif token.type == 'OPERATOR-PREFIX' and token.value in ('+', '-'):
                left = scalar(parse(3, depth+1)) * (-1 if token.value == '-' else 1)
            elif token.type == 'PAREN' and token.subtype == 'OPEN':
                left = parse(0, depth+1)
                if position >= len(tokens) or tokens[position].type != 'PAREN' or tokens[position].subtype != 'CLOSE':
                    raise FormulaError('Не закрыта скобка')
                position += 1
            elif token.type == 'FUNC' and token.subtype == 'OPEN':
                name = token.value[:-1].upper()
                if name not in ('SUM', 'ROUND'):
                    raise FormulaError('Неподдерживаемая функция: ' + name)
                args = []
                while position < len(tokens):
                    args.append(parse(0, depth+1))
                    if position < len(tokens) and tokens[position].type == 'SEP' and tokens[position].subtype == 'ARG':
                        position += 1; continue
                    break
                if position >= len(tokens) or tokens[position].type != 'FUNC' or tokens[position].subtype != 'CLOSE':
                    raise FormulaError('Некорректные аргументы функции')
                position += 1
                if name == 'SUM':
                    left = sum((number for arg in args for number in (arg if isinstance(arg,list) else [arg])), Decimal(0))
                else:
                    if len(args) != 2 or not -6 <= scalar(args[1]) <= 6 or args[1] != int(args[1]):
                        raise FormulaError('ROUND требует значение и целую точность от -6 до 6')
                    left = scalar(args[0]).quantize(Decimal(10) ** -int(args[1]), rounding=ROUND_HALF_UP)
            else:
                raise FormulaError('Неподдерживаемое выражение: ' + token.value)
            priority = {'+':1, '-':1, '*':2, '/':2}
            while position < len(tokens):
                op = tokens[position]
                if op.type != 'OPERATOR-INFIX' or op.value not in priority or priority[op.value] < minimum:
                    break
                position += 1
                right = scalar(parse(priority[op.value]+1, depth+1)); left = scalar(left)
                if op.value == '+': left += right
                elif op.value == '-': left -= right
                elif op.value == '*': left *= right
                else: left /= right
            return left

        with localcontext() as context:
            context.prec = 38
            result = scalar(parse())
        if position != len(tokens):
            raise FormulaError('Неподдерживаемый оператор формулы')
        return result, refs
