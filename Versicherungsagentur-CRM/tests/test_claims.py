import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app


class ClaimTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original_data, self.original_db = app.DATA, app.DB
        app.DATA = Path(self.temp.name)
        app.DB = app.DATA / 'crm.sqlite3'
        app.initialize()
        with app.connect() as c:
            self.customer = app.save_record(c, 'customers', {
                'first_name': 'Anna', 'last_name': 'Muster'
            })['id']
            self.other_customer = app.save_record(c, 'customers', {
                'first_name': 'Berta', 'last_name': 'Beispiel'
            })['id']
            self.contract = app.save_record(c, 'contracts', {
                'customer_id': self.customer, 'insurer': 'Testversicherung',
                'policy_number': 'POL-SCHADEN', 'line': 'Kfz', 'status': 'Aktiv',
                'start_date': '2026-01-01', 'premium': '111', 'frequency': '12',
                'tax_mode': 'legacy', 'premium_basis': 'gross', 'rate': '0'
            })['id']

    def tearDown(self):
        app.DATA, app.DB = self.original_data, self.original_db
        self.temp.cleanup()

    def test_incomplete_draft_and_full_update(self):
        with app.connect() as c:
            result = app.save_claim(c, {'customer_id': self.customer})
            draft = app.get(c, 'claims', result['id'])
            self.assertRegex(draft['number'], r'^SCH-\d{4}-00001$')
            self.assertEqual(draft['status'], 'Entwurf')
            self.assertEqual(draft['title'], '')
            self.assertIsNone(draft['contract_id'])

            updated = app.save_claim(c, {
                'id': draft['id'], 'version': draft['version'],
                'customer_id': self.customer, 'contract_id': self.contract,
                'status': 'In Bearbeitung', 'title': 'Parkschaden',
                'event_date': '2026-09-10', 'event_time': '14:35',
                'reported_date': '2026-09-11', 'location': 'Wien',
                'description': 'Beschädigung am Fahrzeug', 'estimated': '1250,50',
                'deductible': '300', 'reminder_date': '2026-09-20',
                'reminder_note': 'Gutachten urgieren', 'vehicle_plate': 'W-12345X',
                'opponent_insurer': 'Gegner Versicherung'
            })
            claim = app.get(c, 'claims', updated['id'])
            self.assertEqual(claim['line'], 'Kfz')
            self.assertEqual(claim['estimated_cents'], 125050)
            self.assertEqual(claim['deductible_cents'], 30000)
            self.assertEqual(json.loads(claim['details'])['vehicle_plate'], 'W-12345X')
            self.assertEqual(claim['version'], 2)

    def test_relationships_validation_and_email(self):
        with app.connect() as c:
            with self.assertRaises(app.Problem):
                app.save_claim(c, {'customer_id': self.other_customer, 'contract_id': self.contract})
            with self.assertRaises(app.Problem):
                app.save_claim(c, {'customer_id': self.customer, 'status': 'Unbekannt'})
            with self.assertRaises(app.Problem):
                app.save_claim(c, {'customer_id': self.customer, 'event_time': '25:70'})

            claim_id = app.save_claim(c, {
                'customer_id': self.customer, 'contract_id': self.contract,
                'status': 'Gemeldet'
            })['id']
            email = app.save_claim_email(c, {
                'claim_id': claim_id, 'direction': 'Eingang',
                'email_date': '2026-09-12T08:30', 'from_address': 'schaden@example.at',
                'to_address': 'agentur@example.at', 'subject': 'Schadennummer 123',
                'body': 'Unterlagen folgen.'
            })
            row = app.get(c, 'claim_emails', email['id'])
            self.assertEqual(row['claim_id'], claim_id)
            self.assertEqual(row['email_date'], '2026-09-12T08:30')
            with self.assertRaises(app.Problem):
                app.save_claim_email(c, {'claim_id': claim_id})

    def test_migration_adds_claim_link_to_existing_documents(self):
        current_db = app.DB
        try:
            app.DB = app.DATA / 'old-crm.sqlite3'
            old = sqlite3.connect(app.DB)
            old.execute('''CREATE TABLE documents(
                id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL, contract_id INTEGER,
                filename TEXT NOT NULL, storage_name TEXT NOT NULL UNIQUE,
                size INTEGER NOT NULL, created_at TEXT NOT NULL)''')
            old.execute("INSERT INTO documents VALUES(1,1,NULL,'Alt.pdf','stored',3,'2026-01-01')")
            old.commit()
            old.close()
            app.initialize()
            with app.connect() as c:
                columns = {row['name'] for row in c.execute('PRAGMA table_info(documents)')}
                self.assertIn('claim_id', columns)
                self.assertEqual(c.execute('SELECT filename,claim_id FROM documents').fetchone()[0], 'Alt.pdf')
                self.assertEqual(c.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='claims'").fetchone()[0], 1)
        finally:
            app.DB = current_db


if __name__ == '__main__':
    unittest.main()
