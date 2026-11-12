"""Regression coverage for high-precision multilingual supervisor routing."""
import ast
from pathlib import Path
import re
import unittest


def load_alias_router():
    path = Path(__file__).parents[1] / 'domain' / 'archetypes' / 'supervisor_agent.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
              and node.name == '_multilingual_alias_route')
    aliases = {
        'hr': ('leave balance', 'ලීව් බැලන්ස්', 'නිවාඩු දින', 'விடுப்பு', 'loan types', 'ණය වර්ග', 'கடன் வகை'),
        'finance': ('vat', 'invoice', 'ඉන්වොයිස්', 'விலைப்பட்டியல்'),
        'admin': ('visitor pass', 'gate pass', 'ගේට් පාස්', 'விருந்தினர் அனுமதி'),
        'it': ('password reset', 'vpn', 'wifi', 'මුරපද', 'கடவுச்சொல்'),
        'legal': ('contract', 'agreement', 'ගිවිසුම', 'ஒப்பந்த'),
        'network': ('ftth', 'gpon', 'mpls', 'fiber', 'ෆයිබර්', 'நார்'),
    }
    namespace = {'re': re, 'MULTILINGUAL_INTENT_ALIASES': aliases}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace['_multilingual_alias_route']


class MultilingualSupervisorRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.route = load_alias_router()

    def assertRoute(self, query, agent):
        self.assertEqual(type(self).route(query), agent, query)

    def test_hr_leave_balance_equivalents(self):
        for query in (
            'What is my remaining leave balance?',
            'මට කියනවද මගේ ලීව් බැලන්ස් එක කීයද කියලා?',
            'මගේ ඉතිරි නිවාඩු දින කීයද?',
            'Mage leave balance eka keeyada?',
            'என்னுடைய மீதமுள்ள விடுப்பு எவ்வளவு?',
            'Ennudaya leave balance evvalavu?',
        ):
            self.assertRoute(query, 'hr')

    def test_hr_loans(self):
        self.assertRoute('ණය වර්ග මොනවද?', 'hr')
        self.assertRoute('என்ன கடன் வகை உள்ளது?', 'hr')

    def test_other_specialists(self):
        self.assertRoute('My VPN is not working', 'it')
        self.assertRoute('මගේ මුරපදය reset කරන්න', 'it')
        self.assertRoute('என் கடவுச்சொல் வேலை செய்யவில்லை', 'it')
        self.assertRoute('How do I process a VAT invoice?', 'finance')
        self.assertRoute('ඉන්වොයිස් එක ගැන විස්තර දෙන්න', 'finance')
        self.assertRoute('මට ගේට් පාස් එකක් අවශ්‍යයි', 'admin')
        self.assertRoute('ගිවිසුම අත්සන් කිරීම ගැන දැනගන්න ඕන', 'legal')
        self.assertRoute('FTTH ෆයිබර් දෝෂයක් තියෙනවා', 'network')

    def test_genuine_out_of_scope_and_ambiguous_remain_unmatched(self):
        self.assertIsNone(type(self).route('මට අද කෑම වට්ටෝරුවක් කියන්න'))
        self.assertIsNone(type(self).route('இன்று சமையல் குறிப்பு சொல்லுங்கள்'))
        self.assertIsNone(type(self).route('මට උදව් කරන්න'))


if __name__ == '__main__':
    unittest.main()
