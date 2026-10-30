import base64
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock
import subprocess

import app
import importer


class ImporterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        app.DATA = Path(self.tmp.name)
        app.DB = app.DATA / 'crm.sqlite3'
        app.initialize()

    def tearDown(self):
        self.tmp.cleanup()

    def test_policy_fields_and_existing_matches(self):
        text = '''Versicherungsnehmer: Anna Muster
Musterstraße 12
1010 Wien
E-Mail: anna@example.at
Versicherer: Beispiel Versicherung AG
Polizzennummer: HH-2026-991
Haushaltsversicherung
Versicherungsbeginn: 01.10.2026
Vertragsende: 30.09.2027
Bruttoprämie: EUR 120,50
Zahlweise monatlich'''
        result = importer.suggest(text)
        self.assertEqual(result['customer']['last_name'], 'Muster')
        self.assertEqual(result['contract']['policy_number'], 'HH-2026-991')
        self.assertEqual(result['contract']['line'], 'Haushalt')
        self.assertEqual(result['contract']['start_date'], '2026-10-01')
        self.assertEqual(result['contract']['premium'], '120.50')
        with app.connect() as c:
            customer_id = app.save_record(c, 'customers', {'first_name': 'Anna', 'last_name': 'Muster', 'email': 'anna@example.at'})['id']
            matches = importer.matches(c, result)
            self.assertEqual(matches['customers'][0]['id'], customer_id)
            self.assertGreaterEqual(matches['customers'][0]['score'], 100)

    def test_policy_contact_section_excludes_insurer_email(self):
        text = '''Versicherungsnehmer: Anna Muster
Musterstraße 12
1010 Wien
Versicherer: Fremd Versicherung AG
Service E-Mail: polizzen@fremd-versicherung.at
Polizzennummer: ABC-12345'''
        result = importer.suggest(text)
        self.assertEqual(result['customer']['email'], '')
        self.assertEqual(result['customer']['postal_code'], '1010')

    def test_adjustment_policy_uses_structured_policyholder_block(self):
        text = '''Krankenversicherung
Kundenberater Patrick Beispiel, MSc
Beispielstraße 31/8
2486 Beispielort
Polizze Nr. 1088602295
Nimmt die:der Versicherungsnehmer:in die Anpassung nicht an, bleiben die Prämien unverändert.
kundenservice@versicherung.example
WIENER BEISPIEL Versicherung AG Vienna Insurance Group
Krankenversicherung
Polizze Nr. 1088602295
Versicherungsdauer
Die Änderung dieser Polizze ist gültig ab: 01.01.2026
Ursprünglicher Versicherungsbeginn: 01.12.2019
VersicherungsnehmerIn
Kundenberater Patrick Beispiel, MSc, geb. 22.01.1985
Versichert ist
Jason Patrick Beispiel, geb. 18.10.2019
Prämie (monatlich) für Jason Patrick Beispiel neu 64,74
Prämie (inkl. Versicherungssteuer) 64,74
Prämie (exkl. Versicherungssteuer) 64,10
Zahlungsweise Monatlich'''
        result = importer.suggest(text)
        self.assertEqual((result['customer']['first_name'], result['customer']['last_name']), ('Patrick', 'Beispiel'))
        self.assertEqual(result['customer']['birthday'], '1985-01-22')
        self.assertEqual(result['customer']['email'], '')
        self.assertEqual(result['customer']['address'], '')
        self.assertEqual(result['contract']['insurer'], 'WIENER BEISPIEL Versicherung AG Vienna Insurance Group')
        self.assertEqual(result['contract']['policy_number'], '1088602295')
        self.assertEqual(result['contract']['start_date'], '2019-12-01')
        self.assertEqual(result['contract']['premium'], '64.74')
        self.assertEqual(result['contract']['frequency'], '12')

    def test_swapped_and_partial_names_find_existing_customer(self):
        with app.connect() as c:
            customer_id = app.save_record(c, 'customers', {'first_name': 'Anna', 'last_name': 'Muster',
                'postal_code': '1010', 'city': 'Wien'})['id']
            swapped = importer.suggest('Versicherungsnehmer: Muster Anna\n1010 Wien\nPolizzennummer: X-100')
            result = importer.matches(c, swapped)['customers'][0]
            self.assertEqual(result['id'], customer_id)
            self.assertIn('Reihenfolge vertauscht', result['reason'])
            partial = importer.suggest('Versicherungsnehmer: Muster\n1010 Wien\nPolizzennummer: X-101')
            result = importer.matches(c, partial)['customers'][0]
            self.assertEqual(result['id'], customer_id)
            self.assertIn('gemeinsamer Namensteil', result['reason'])

    def test_existing_customer_is_unchanged_without_opt_in(self):
        raw = b'policy'
        with app.connect() as c:
            customer_id = app.save_record(c, 'customers', {'first_name': 'Anna', 'last_name': 'Muster',
                'email': 'richtig@example.at'})['id']
            app.commit_import(c, {'filename': 'Polizze.txt', 'data': base64.b64encode(raw).decode(),
                'customer_action': 'existing', 'customer_id': customer_id, 'update_customer': False,
                'contract_action': 'none', 'customer': {'email': 'service@versicherung.at', 'phone': '123'},
                'contract': {}})
            c.commit()
            stored = dict(c.execute('SELECT * FROM customers WHERE id=?', (customer_id,)).fetchone())
            self.assertEqual(stored['email'], 'richtig@example.at')
            self.assertEqual(stored['phone'], '')

    def test_existing_policy_number_wins_and_uses_contract_customer(self):
        raw = b'follow-up policy'
        with app.connect() as c:
            customer_id = app.save_record(c, 'customers', {'first_name': 'Anna', 'last_name': 'Muster'})['id']
            contract_id = app.save_record(c, 'contracts', {'customer_id': customer_id, 'insurer': 'Test',
                'policy_number': 'AB-123/45', 'line': 'Haushalt', 'status': 'Aktiv', 'start_date': '2024-01-01',
                'premium': '100', 'frequency': '12', 'rate': '0', 'commission_basis': 'agency'})['id']
            result = app.commit_import(c, {'filename': 'Folgepolizze.pdf', 'data': base64.b64encode(raw).decode(),
                'contract_action': 'new', 'customer_action': 'new', 'customer': {},
                'contract': {'policy_number': 'AB 123-45'}})
            c.commit()
            self.assertEqual(result['contract_id'], contract_id)
            self.assertEqual(result['customer_id'], customer_id)
            self.assertFalse(result['customer_created'])
            self.assertEqual(c.execute('SELECT count(*) FROM customers').fetchone()[0], 1)
            self.assertEqual(c.execute('SELECT count(*) FROM contracts').fetchone()[0], 1)

    def test_email_analysis_and_reviewed_commit(self):
        raw = b'''From: Max Beispiel <max@example.at>\nSubject: Neue Kontaktdaten\nContent-Type: text/plain; charset=utf-8\n\nFreundliche Gruesse\nMax Beispiel\nTelefon: +43 660 1234567\n'''
        with app.connect() as c:
            customer_id = app.save_record(c, 'customers', {'first_name': 'Max', 'last_name': 'Beispiel', 'email': 'max@example.at'})['id']
            analyzed = app.analyze_import(c, {'filename': 'Nachricht.eml', 'data': base64.b64encode(raw).decode()})
            self.assertEqual(analyzed['kind'], 'email')
            self.assertEqual(analyzed['matches']['customers'][0]['id'], customer_id)
            result = app.commit_import(c, {'filename': 'Nachricht.eml', 'data': base64.b64encode(raw).decode(),
                'customer_action': 'existing', 'customer_id': customer_id, 'update_customer': True,
                'contract_action': 'none', 'customer': analyzed['customer'], 'contract': {}})
            c.commit()
            self.assertFalse(result['customer_created'])
            self.assertIsNone(result['contract_id'])
            customer = dict(c.execute('SELECT * FROM customers WHERE id=?', (customer_id,)).fetchone())
            self.assertEqual(customer['phone'], '+43 660 1234567')
            document = c.execute('SELECT * FROM documents WHERE id=?', (result['document_id'],)).fetchone()
            self.assertEqual(document['customer_id'], customer_id)
            self.assertIsNone(document['contract_id'])

    def test_new_customer_contract_and_tax_are_created_together(self):
        raw = b'original policy'
        data = {'filename': 'Polizze.txt', 'data': base64.b64encode(raw).decode(),
            'customer_action': 'new', 'contract_action': 'new',
            'customer': {'first_name': 'Eva', 'last_name': 'Neu'},
            'contract': {'insurer': 'Test Versicherung', 'policy_number': 'NEW-42', 'line': 'Haushalt',
                         'ownership': 'Eigen', 'status': 'Aktiv', 'start_date': '2026-09-01',
                         'end_date': '', 'notice_date': '', 'premium': '111', 'frequency': '12'}}
        with app.connect() as c:
            result = app.commit_import(c, data); c.commit()
            contract = dict(c.execute('SELECT * FROM contracts WHERE id=?', (result['contract_id'],)).fetchone())
            self.assertTrue(result['customer_created'])
            self.assertTrue(result['contract_created'])
            self.assertEqual(contract['tax_mode'], 'auto')
            self.assertEqual(contract['tax_cents'], 1100)
            stored = c.execute('SELECT storage_name FROM documents WHERE id=?', (result['document_id'],)).fetchone()[0]
            self.assertEqual((app.DATA / 'Dokumente' / stored).read_bytes(), raw)

    def test_linux_text_pdf_uses_poppler(self):
        completed = subprocess.CompletedProcess([], 0, 'Polizzennummer: SERVER-123', '')
        with mock.patch('importer.shutil.which', side_effect=lambda name: '/usr/bin/' + name), \
             mock.patch('importer._run', return_value=completed) as run:
            result = importer._server_extract(Path('/tmp/policy.pdf'), '.pdf')
        self.assertEqual(result.stdout, completed.stdout)
        self.assertEqual(run.call_args.args[0][0], '/usr/bin/pdftotext')


if __name__ == '__main__':
    unittest.main()
