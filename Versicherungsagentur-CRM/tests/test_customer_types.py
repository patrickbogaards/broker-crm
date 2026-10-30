import sqlite3
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import importer


class CustomerTypeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original_data, self.original_db = app.DATA, app.DB
        app.DATA = Path(self.temp.name)
        app.DB = app.DATA / 'crm.sqlite3'
        app.initialize()

    def tearDown(self):
        app.DATA, app.DB = self.original_data, self.original_db
        self.temp.cleanup()

    def test_natural_and_legal_customer_validation(self):
        with app.connect() as c:
            natural = app.save_record(c, 'customers', {
                'customer_type': 'natural', 'first_name': 'Anna', 'last_name': 'Muster'
            })
            legal = app.save_record(c, 'customers', {
                'customer_type': 'legal', 'company': 'Muster Bau GmbH'
            })
            self.assertEqual(app.get(c, 'customers', natural['id'])['customer_type'], 'natural')
            stored = app.get(c, 'customers', legal['id'])
            self.assertEqual(stored['customer_type'], 'legal')
            self.assertEqual(stored['company'], 'Muster Bau GmbH')
            self.assertEqual(stored['first_name'], '')
            with self.assertRaisesRegex(app.Problem, 'Vor- und Nachname'):
                app.save_record(c, 'customers', {'customer_type': 'natural', 'company': 'Ohne Name'})
            with self.assertRaisesRegex(app.Problem, 'Firmennamen'):
                app.save_record(c, 'customers', {'customer_type': 'legal', 'first_name': 'Kontakt'})

    def test_existing_database_customers_migrate_as_natural_people(self):
        old_db = app.DB
        try:
            app.DB = app.DATA / 'old-customers.sqlite3'
            c = sqlite3.connect(app.DB)
            c.executescript('''
                CREATE TABLE customers(id INTEGER PRIMARY KEY, first_name TEXT NOT NULL,
                    last_name TEXT NOT NULL, company TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '',
                    phone TEXT NOT NULL DEFAULT '', address TEXT NOT NULL DEFAULT '', postal_code TEXT NOT NULL DEFAULT '',
                    city TEXT NOT NULL DEFAULT '', birthday TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
                    archived INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
                INSERT INTO customers(id,first_name,last_name,created_at)
                    VALUES(1,'Alt','Kunde','2025-01-01');
            ''')
            c.commit(); c.close()
            app.initialize()
            with app.connect() as c:
                self.assertEqual(app.get(c, 'customers', 1)['customer_type'], 'natural')
        finally:
            app.DB = old_db

    def test_policy_company_is_detected_and_matches_existing_company(self):
        text = '''Versicherungsnehmer
Muster Bau GmbH
Anschrift: Ringstraße 4
1010 Wien
Polizzennummer: FIRMA-100
Versicherungsbeginn: 01.09.2026
Prämie: EUR 100,00'''
        suggestion = importer.suggest(text)
        self.assertEqual(suggestion['customer']['customer_type'], 'legal')
        self.assertEqual(suggestion['customer']['company'], 'Muster Bau GmbH')
        self.assertEqual(suggestion['customer']['first_name'], '')
        with app.connect() as c:
            customer_id = app.save_record(c, 'customers', {
                'customer_type': 'legal', 'company': 'Muster Bau GmbH',
                'address': 'Ringstraße 4', 'postal_code': '1010', 'city': 'Wien'
            })['id']
            match = importer.matches(c, suggestion)['customers'][0]
            self.assertEqual(match['id'], customer_id)
            self.assertIn('gleicher Firmenname', match['reason'])

    def test_email_sender_company_is_detected(self):
        suggestion = importer.suggest('Anfrage zur Versicherung',
                                      {'from': 'Muster Handel OG <office@muster.at>',
                                       'email': 'office@muster.at'}, 'email')
        self.assertEqual(suggestion['customer']['customer_type'], 'legal')
        self.assertEqual(suggestion['customer']['company'], 'Muster Handel OG')


if __name__ == '__main__':
    unittest.main()
