import unittest
from pathlib import Path
from kfz_core.amounts import read_amounts
from kfz_core.money import scaled_money
from kfz_core.metadata import contracts_in


class AmountTests(unittest.TestCase):
    def parse(self, lines, decision=None):
        extracted={'kind':'docx','text':'\n'.join(lines),'lines':[{'text':v,'paragraph':i+1} for i,v in enumerate(lines)],'tables':[],'sheets':{}}
        return read_amounts(Path('source.docx'),'doc',extracted,{},decision)

    def test_global_thousands(self):
        r=self.parse(['Единица измерения: тыс. руб.','Сумма: 100,00'])
        self.assertEqual(r['amount'],'100000.00')

    def test_round_after_conversion_and_negative_sign(self):
        self.assertEqual(scaled_money('0,12345',1000),scaled_money('123,45'))
        self.assertEqual(self.parse(['Ед. изм.: тыс. руб.','Итого: (0,12345)'])['amount'],'-123.45')
        self.assertEqual(self.parse(['Итого: –100,00 руб.'])['amount'],'-100.00')

    def test_million_units_and_docx_table_header(self):
        self.assertEqual(self.parse(['Все суммы указаны в млн руб.','Итого: 0,000125'])['amount'],'125.00')
        extracted={'kind':'docx','lines':[{'text':'Итого: 0,12345','table':1,'row':2}],
                   'tables':[{'text':'Сумма, тыс. руб.','table':1,'row':1}],'sheets':{}}
        self.assertEqual(read_amounts(Path('table.docx'),'table',extracted,{})['amount'],'123.45')

    def test_vat_does_not_compete_with_grand_total(self):
        r=self.parse(['Стоимость без НДС: 1000,00 руб.','Сумма НДС: 200,00 руб.','Итого к оплате: 1200,00 руб.'])
        self.assertEqual(r['amount'],'1200.00')
        self.assertEqual(len(r['candidates']),3)

    def test_conflicting_units_require_confirmation(self):
        lines=['Единица измерения: тыс. руб.','Итого: 100,00 руб.']
        r=self.parse(lines)
        self.assertIsNone(r['amount'])
        confirmed=self.parse(lines,{'amount_source':r['candidates'][0]['id'],'amount_raw':'100,00','amount_multiplier':'1000','reason':'Единицы проверены по оригиналу'})
        self.assertEqual(confirmed['amount'],'100000.00')
        self.assertTrue(confirmed['evidence'][-1]['manual'])
        self.assertIsNone(confirmed['evidence'][0]['value'])

    def test_two_totals_require_source_selection(self):
        r=self.parse(['Итого: 100,00','Итого: 200,00'])
        self.assertIsNone(r['amount'])
        fixed=self.parse(['Итого: 100,00','Итого: 200,00'],{'amount_source':r['candidates'][1]['id'],'amount_multiplier':'1','reason':'Выбран итог акта'})
        self.assertEqual(fixed['amount'],'200.00')

    def test_contract_formats(self):
        self.assertEqual(contracts_in('Договор № МФТИ-2026/01 от 15.03.2026'),['МФТИ-2026/01'])
        self.assertEqual(contracts_in('Контракт № 24 / 03 / 15 - 01'),['24/03/15-01'])
        self.assertEqual(contracts_in('Договор 123\nДоговор 1234'),['123','1234'])

    def test_date_on_amount_line_is_not_a_money_candidate(self):
        result=self.parse(['Дата 15.03.2026; Итого: 100,00 руб.'])
        self.assertEqual(result['amount'],'100.00')
        self.assertEqual(len(result['candidates']),1)


if __name__=='__main__': unittest.main()
