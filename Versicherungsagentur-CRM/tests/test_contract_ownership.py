import sqlite3
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app


class ContractOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original_data, self.original_db = app.DATA, app.DB
        app.DATA = Path(self.temp.name)
        app.DB = app.DATA / 'crm.sqlite3'
        app.initialize()
        with app.connect() as c:
            self.customer = app.save_record(c, 'customers', {
                'first_name': 'Anna', 'last_name': 'Muster',
                'birthday': '1990-09-20'
            })['id']

    def tearDown(self):
        app.DATA, app.DB = self.original_data, self.original_db
        self.temp.cleanup()

    def contract_data(self, **changes):
        data = {
            'customer_id': self.customer, 'insurer': 'Testversicherung',
            'policy_number': 'POL-EIGEN', 'line': 'Haushalt', 'status': 'Aktiv',
            'start_date': '2026-01-01', 'notice_date': '2026-10-01',
            'premium': '111', 'frequency': '12', 'tax_mode': 'auto',
            'premium_basis': 'gross', 'rate': '0'
        }
        data.update(changes)
        return data

    def test_default_eigen_and_explicit_fremd(self):
        with app.connect() as c:
            own = app.save_record(c, 'contracts', self.contract_data())
            self.assertEqual(app.get(c, 'contracts', own['id'])['ownership'], 'Eigen')
            foreign = app.save_record(c, 'contracts', self.contract_data(
                policy_number='POL-FREMD', ownership='Fremd'))
            self.assertEqual(app.get(c, 'contracts', foreign['id'])['ownership'], 'Fremd')
            with self.assertRaises(app.Problem):
                app.save_record(c, 'contracts', self.contract_data(
                    policy_number='POL-INVALID', ownership='Unbekannt'))

    def test_existing_contracts_migrate_as_eigen(self):
        old_db = app.DB
        try:
            app.DB = app.DATA / 'old.sqlite3'
            c = sqlite3.connect(app.DB)
            c.executescript('''
                CREATE TABLE customers(id INTEGER PRIMARY KEY, first_name TEXT NOT NULL,
                    last_name TEXT NOT NULL, company TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '',
                    phone TEXT NOT NULL DEFAULT '', address TEXT NOT NULL DEFAULT '', postal_code TEXT NOT NULL DEFAULT '',
                    city TEXT NOT NULL DEFAULT '', birthday TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
                    archived INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
                CREATE TABLE contracts(id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id),
                    insurer TEXT NOT NULL, policy_number TEXT NOT NULL UNIQUE, line TEXT NOT NULL,
                    status TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL DEFAULT '',
                    notice_date TEXT NOT NULL DEFAULT '', premium_cents INTEGER NOT NULL, frequency INTEGER NOT NULL,
                    referrer_id INTEGER, rate_bp INTEGER NOT NULL, commission_basis TEXT NOT NULL,
                    notes TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
                INSERT INTO customers(id,first_name,last_name,created_at) VALUES(1,'Alt','Kunde','2025-01-01');
                INSERT INTO contracts(id,customer_id,insurer,policy_number,line,status,start_date,premium_cents,
                    frequency,rate_bp,commission_basis,created_at)
                    VALUES(1,1,'Altversicherung','ALT-1','Haushalt','Aktiv','2025-01-01',10000,12,0,'agency','2025-01-01');
            ''')
            c.commit(); c.close()
            app.initialize()
            with app.connect() as c:
                self.assertEqual(app.get(c, 'contracts', 1)['ownership'], 'Eigen')
        finally:
            app.DB = old_db


if __name__ == '__main__':
    unittest.main()
