#!/usr/bin/env python3
"""Versicherungsagentur CRM — local-only Python 3.10+ application."""
import argparse
import base64
import csv
import hashlib
import hmac
import html
import io
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import sys
import tempfile
import threading
import time
import traceback
import webbrowser
import zipfile
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
import importer

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('CRM_DATA_DIR', str(ROOT / 'Daten'))).resolve()
DB = DATA / 'crm.sqlite3'
SESSIONS = {}
ATTEMPTS = []
AUTH_LOCK = threading.Lock()
# BMF, Bemessungsgrundlage und Steuersätze, checked 2026-09-10.
TAX_RULES = [
    {'lines': ['Haushalt', 'Eigenheim', 'Gebäude', 'Wohngebäude', 'Rechtsschutz', 'Haftpflicht', 'Privathaftpflicht', 'Betriebshaftpflicht', 'Unfall', 'Kfz', 'Kfz-Haftpflicht', 'Kasko', 'Teilkasko', 'Vollkasko', 'Lkw', 'Lkw-Haftpflicht', 'Nutzfahrzeug', 'Lieferwagen', 'Reise', 'Sach', 'Feuer', 'Betriebsunterbrechung', 'Cyber'], 'rate_bp': 1100},
    {'lines': ['Kranken', 'Krankenversicherung', 'Private Krankenversicherung'], 'rate_bp': 100},
    {'lines': ['Leben', 'Lebensversicherung', 'Rentenversicherung', 'Invalidität', 'Berufsunfähigkeit', 'Ableben', 'Risikoleben', 'Fondsgebundene Lebensversicherung'], 'rate_bp': 400},
    {'lines': ['Pensionskasse', 'Pensionskassenbeiträge'], 'rate_bp': 250},
]

CLAIM_STATUSES = ('Entwurf', 'Gemeldet', 'In Bearbeitung', 'Warten auf Unterlagen',
                  'Reguliert', 'Abgelehnt', 'Geschlossen')
CLAIM_DETAIL_FIELDS = (
    'vehicle_plate', 'vehicle_description', 'driver', 'opponent', 'opponent_insurer',
    'property_object', 'damage_type', 'damaged_items', 'emergency_measures',
    'injured_person', 'injury', 'doctor_hospital', 'incapacity',
    'legal_opponent', 'legal_matter', 'lawyer_reference',
    'trip_details', 'booking_reference', 'travel_reason',
    'insured_person', 'beneficiary', 'life_event',
    'affected_systems', 'downtime', 'data_affected', 'incident_response',
    'liability_allegation', 'demand', 'witnesses', 'other_insurances'
)


def tax_rate(line):
    normalized = line.strip().lower()
    for rule in TAX_RULES:
        if normalized in [alias.lower() for name in rule['lines'] for alias in (name, name + 'versicherung')]:
            return rule['rate_bp']
    return None


def premium_fields(d):
    amount = money(d.get('premium', '0'))
    mode = d.get('tax_mode', 'legacy')
    basis = d.get('premium_basis', 'gross')
    if mode not in ('legacy', 'auto', 'manual_rate', 'manual_amount') or basis not in ('net', 'gross'):
        raise Problem('Ungültige Steuerberechnung oder Prämienbasis.')
    bp = None
    if mode == 'legacy':
        return dict(premium_cents=amount, premium_basis='gross', tax_mode=mode, tax_rate_bp=None, tax_cents=None)
    if mode == 'auto':
        line = txt(d, 'line', True)
        bp = 1100 if is_kfz(line) else tax_rate(line)
        if bp is None:
            raise Problem('Für diese Sparte bitte die Versicherungssteuer manuell eingeben.')
        if bp == 400 and d.get('life_tax_case', 'standard') == 'single_short':
            bp = 1100
    elif mode == 'manual_rate':
        bp = money(d.get('tax_rate', ''))
        if bp > 10000:
            raise Problem('Der Steuersatz muss zwischen 0 und 100 % liegen.')
    if is_kfz(d.get('line', '')) and mode != 'legacy':
        return kfz_premium_fields(d, amount, basis, mode, bp)
    if bp is not None:
        divisor = 10000 if basis == 'net' else 10000 + bp
        tax = (amount * bp + divisor // 2) // divisor
    else:
        tax = money(d.get('tax_amount', ''))
    if basis == 'gross' and tax > amount:
        raise Problem('Die Versicherungssteuer darf die Bruttoprämie nicht überschreiten.')
    return dict(premium_cents=amount + tax if basis == 'net' else amount,
                premium_basis=basis, tax_mode=mode, tax_rate_bp=bp, tax_cents=tax)


def is_kfz(line):
    return any(word in line.strip().lower() for word in ('kfz', 'kraftfahr', 'kasko', 'autohaftpflicht', 'lkw', 'nutzfahr', 'lieferwagen', 'pkw'))


def premium_preview(d):
    fields = premium_fields(d)
    frequency = integer(d.get('frequency', 12), 'Zahlungsrhythmus')
    if frequency not in (1, 2, 4, 12):
        raise Problem('Ungültiger Zahlungsrhythmus.')
    gross = fields['premium_cents']
    tax = fields['tax_cents']
    return {**fields, 'net_cents': None if tax is None else gross - tax,
            'annual_gross_cents': gross * frequency,
            'annual_tax_cents': None if tax is None else tax * frequency,
            'annual_net_cents': None if tax is None else (gross - tax) * frequency}


def rounded_cents(value):
    return int((Decimal(value) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def truck_tax_monthly(gross_weight_kg):
    """Separate Kraftfahrzeugsteuer for N2/N3; never part of the premium tax."""
    weight = integer(gross_weight_kg, 'Höchstes zulässiges Gesamtgewicht in kg')
    if weight <= 3500:
        raise Problem('Schwere Lkw müssen mehr als 3,5 Tonnen zulässiges Gesamtgewicht haben.')
    tonnes = (weight + 999) // 1000
    if weight <= 12000:
        return max(Decimal(tonnes) * Decimal('1.55'), Decimal('15'))
    if weight <= 18000:
        return Decimal(tonnes) * Decimal('1.70')
    return min(Decimal(tonnes) * Decimal('1.90'), Decimal('80'))


def motor_tax_monthly(d):
    """BMF motorbezogene VersSt tariffs for M1 cars and N1 light trucks."""
    vehicle_class = d.get('vehicle_class', 'M1')
    if vehicle_class not in ('M1', 'N1'):
        raise Problem('Für diese Fahrzeugklasse gibt es hier keine automatische Motorsteuer.')
    if vehicle_class == 'N1':
        gross_weight = integer(d.get('gross_weight_kg'), 'Höchstes zulässiges Gesamtgewicht in kg')
        if not 0 < gross_weight <= 3500:
            raise Problem('Lkw N1 müssen höchstens 3,5 Tonnen zulässiges Gesamtgewicht haben.')
    elif d.get('gross_weight_kg'):
        gross_weight = integer(d['gross_weight_kg'], 'Höchstes zulässiges Gesamtgewicht in kg')
        if gross_weight > 3500:
            raise Problem('Der Pkw-Tarif gilt nur bis 3,5 Tonnen zulässiges Gesamtgewicht.')
    first = iso(d.get('first_registration', ''))
    year = int(first[:4])
    drive = d.get('drive_type')
    if drive not in ('combustion', 'phev', 'electric'):
        raise Problem('Für die Motorsteuer bitte die Motorart wählen.')
    power = integer(d.get('power_kw'), 'Leistung in kW')
    if power <= 0:
        raise Problem('Die Leistung muss größer als null sein.')
    if vehicle_class == 'N1':
        deduction = 16 if drive == 'electric' else 24
        p = max(power - deduction, 0)
        if drive == 'electric' or first >= '2020-10-01':
            monthly = (Decimal(min(p, 66)) * Decimal('.65') +
                       Decimal(min(max(p - 66, 0), 20)) * Decimal('.70') +
                       Decimal(max(p - 86, 0)) * Decimal('.79'))
            return min(max(monthly, Decimal('6.50')), Decimal('76'))
        if first < '1987-01-01':
            raise Problem('Für Erstzulassungen vor 1987 bitte die Motorsteuer wegen möglicher Sonderzuschläge laut Polizze eintragen.')
        monthly = (Decimal(min(p, 66)) * Decimal('.62') +
                   Decimal(min(max(p - 66, 0), 20)) * Decimal('.66') +
                   Decimal(max(p - 86, 0)) * Decimal('.75'))
        return min(max(monthly, Decimal('6.20')), Decimal('72'))
    if drive == 'electric':
        weight = integer(d.get('weight_kg'), 'Eigengewicht in kg')
        if weight <= 0:
            raise Problem('Bitte das Eigengewicht eintragen.')
        p, w = max(power - 45, 10), max(weight - 900, 200)
        return (Decimal(min(p, 35)) * Decimal('.25') +
                Decimal(min(max(p - 35, 0), 25)) * Decimal('.35') +
                Decimal(max(p - 60, 0)) * Decimal('.45') +
                Decimal(min(w, 500)) * Decimal('.015') +
                Decimal(min(max(w - 500, 0), 700)) * Decimal('.030') +
                Decimal(max(w - 1200, 0)) * Decimal('.045'))
    if first < '2020-10-01':
        if first < '1987-01-01':
            raise Problem('Für Erstzulassungen vor 1987 bitte die Motorsteuer wegen möglicher Sonderzuschläge laut Polizze eintragen.')
        p = max(power - 24, 0)
        monthly = (Decimal(min(p, 66)) * Decimal('.62') +
                   Decimal(min(max(p - 66, 0), 20)) * Decimal('.66') +
                   Decimal(max(p - 86, 0)) * Decimal('.75'))
        return max(monthly, Decimal('6.20'))
    if d.get('wltp', 'yes') == 'no':
        p = max(power - 24, 0)
        monthly = (Decimal(min(p, 66)) * Decimal('.65') +
                   Decimal(min(max(p - 66, 0), 20)) * Decimal('.70') +
                   Decimal(max(p - 86, 0)) * Decimal('.79'))
        return max(monthly, Decimal('6.50'))
    if year > 2026:
        raise Problem('Für künftige Erstzulassungen bitte die Motorsteuer laut Polizze manuell eintragen.')
    co2 = integer(d.get('co2_g_km'), 'CO₂-Wert in g/km')
    if co2 < 0:
        raise Problem('Der CO₂-Wert darf nicht negativ sein.')
    kw_deduction = 65 - (year - 2020)
    co2_deduction = (24 if year == 2026 else 17 - (year - 2020)) if drive == 'phev' else 115 - 3 * (year - 2020)
    return Decimal(max(power - kw_deduction, 5) + max(co2 - co2_deduction, 5)) * Decimal('.72')


def kfz_premium_fields(d, amount, basis, mode, bp):
    coverage = d.get('kfz_coverage', '')
    if coverage not in ('liability', 'casco', 'combined'):
        raise Problem('Bei Kfz bitte Haftpflicht, Kasko oder Kombination auswählen.')
    frequency = integer(d.get('frequency', 12), 'Zahlungsrhythmus')
    if frequency not in (1, 2, 4, 12):
        raise Problem('Ungültiger Zahlungsrhythmus.')
    fire_mode = d.get('fire_mode', 'auto')
    motor_mode = d.get('motor_mode', 'manual' if coverage != 'casco' else 'none')
    if fire_mode not in ('auto', 'manual') or motor_mode not in ('none', 'auto', 'manual'):
        raise Problem('Ungültige Kfz-Steuereinstellung.')
    vehicle_class = d.get('vehicle_class', 'M1')
    if vehicle_class not in ('M1', 'N1', 'N2', 'N3', 'other'):
        raise Problem('Ungültige Fahrzeugklasse.')
    heavy = vehicle_class in ('N2', 'N3')
    if coverage == 'casco' and motor_mode != 'none':
        raise Problem('Motorbezogene Versicherungssteuer gehört zur Kfz-Haftpflicht, nicht zur reinen Kasko.')
    if heavy and motor_mode != 'none':
        raise Problem('Lkw über 3,5 Tonnen zahlen keine motorbezogene Versicherungssteuer auf der Polizze.')
    if coverage != 'casco' and motor_mode == 'none' and not heavy:
        raise Problem('Für Kfz-Haftpflicht bitte die Motorsteuer berechnen oder laut Polizze eintragen.')
    if coverage == 'liability' and fire_mode == 'auto':
        fire_bp = 0
    else:
        fire_bp = money(d.get('fire_rate', '0.01')) if fire_mode == 'auto' else None
        if fire_bp is not None and fire_bp > 10000:
            raise Problem('Der Feuerschutzsteuersatz muss zwischen 0 und 100 % liegen.')
    motor = 0
    if motor_mode == 'manual':
        motor = money(d.get('motor_amount', ''))
    elif motor_mode == 'auto':
        if vehicle_class not in ('M1', 'N1'):
            raise Problem('Für andere Fahrzeugklassen bitte die Motorsteuer laut Polizze manuell eintragen.')
        monthly = motor_tax_monthly(d)
        if d.get('first_registration', '') < '2020-10-01' and d.get('drive_type') != 'electric':
            monthly *= Decimal({1: '1', 2: '1.06', 4: '1.08', 12: '1.10'}[frequency])
        motor = rounded_cents(monthly * Decimal(12) / Decimal(frequency))
    gross_weight_kg = d.get('gross_weight_kg', '')
    vehicle_tax_cents = None
    vehicle_tax_case = d.get('vehicle_tax_case', 'standard')
    if vehicle_tax_case not in ('standard', 'exempt'):
        raise Problem('Ungültige Einstellung zur Kraftfahrzeugsteuer.')
    if heavy:
        if not gross_weight_kg:
            raise Problem('Für Lkw N2/N3 bitte das zulässige Gesamtgewicht für die separate Kraftfahrzeugsteuer eintragen.')
        gross_weight = integer(gross_weight_kg, 'Höchstes zulässiges Gesamtgewicht in kg')
        if vehicle_class == 'N2' and gross_weight > 12000:
            raise Problem('N2-Lkw dürfen höchstens 12 Tonnen zulässiges Gesamtgewicht haben.')
        if vehicle_class == 'N3' and gross_weight <= 12000:
            raise Problem('N3-Lkw müssen mehr als 12 Tonnen zulässiges Gesamtgewicht haben.')
        if d.get('drive_type') not in ('combustion', 'phev', 'electric'):
            raise Problem('Für schwere Lkw bitte die Antriebsart wählen.')
        vehicle_tax_cents = 0 if d['drive_type'] == 'electric' or vehicle_tax_case == 'exempt' else rounded_cents(truck_tax_monthly(gross_weight))
    if mode == 'manual_amount':
        insurance = money(d.get('tax_amount', ''))
    elif bp is None:
        raise Problem('Für die Kfz-Versicherungssteuer bitte einen Satz eingeben.')
    else:
        insurance = None
    casco_premium_basis = d.get('casco_premium_basis', 'net')
    if casco_premium_basis not in ('net', 'gross'):
        raise Problem('Ungültige Eingabeart für die Kaskoprämie.')
    casco_input = d.get('casco_premium', d.get('casco_net', ''))
    casco_premium = money(casco_input) if coverage == 'combined' and casco_input != '' else None
    casco_net = None
    if coverage == 'combined' and fire_mode == 'auto' and casco_premium is None:
        raise Problem('Bei Haftpflicht und Kasko bitte die Kaskoprämie eintragen.')
    if casco_premium is not None:
        if casco_premium_basis == 'net':
            casco_net = casco_premium
        else:
            # A Kasko gross amount contains its own insurance tax and fire tax,
            # but no motor-related tax from the liability coverage.
            casco_bp = bp if bp is not None else 1100
            casco_fire = 0 if fire_mode == 'auto' else money(d.get('fire_amount', ''))
            if casco_premium < casco_fire:
                raise Problem('Die Feuerschutzsteuer überschreitet die Kasko-Bruttoprämie.')
            divisor = 10000 + casco_bp + (fire_bp if fire_mode == 'auto' else 0)
            casco_net = ((casco_premium - casco_fire) * 10000 + divisor // 2) // divisor
    if coverage == 'liability':
        fire = 0
    elif fire_mode == 'manual':
        fire = money(d.get('fire_amount', ''))
    elif coverage == 'combined':
        fire = (casco_net * fire_bp + 5000) // 10000
    else:
        fire = None  # pure casco: derive from its net premium
    if basis == 'net':
        if fire is None:
            fire = (amount * fire_bp + 5000) // 10000
        if insurance is None:
            insurance = (amount * bp + 5000) // 10000
        gross = amount + insurance + fire + motor
    else:
        if amount < motor + (fire or 0):
            raise Problem('Die Steuerbestandteile überschreiten die Bruttoprämie.')
        if fire is None and insurance is None:
            base = amount - motor
            divisor = 10000 + bp + fire_bp
            fire = (base * fire_bp + divisor // 2) // divisor
            insurance = (base * bp + divisor // 2) // divisor
        elif fire is None:
            net_casco = amount - motor - insurance
            divisor = 10000 + fire_bp
            fire = (net_casco * fire_bp + divisor // 2) // divisor
        elif insurance is None:
            base = amount - motor - fire
            insurance = (base * bp + (10000 + bp) // 2) // (10000 + bp)
        gross = amount
    total = insurance + fire + motor
    if total > gross:
        raise Problem('Die Steuerbestandteile überschreiten die Bruttoprämie.')
    if casco_net is not None and casco_net > gross - total:
        raise Problem('Die Kasko-Nettoprämie darf die gesamte Nettoprämie nicht überschreiten.')
    if casco_premium_basis == 'gross' and casco_premium is not None and casco_premium > gross - motor:
        raise Problem('Die Kasko-Bruttoprämie darf die gesamte Bruttoprämie ohne Motorsteuer nicht überschreiten.')
    return dict(premium_cents=gross, premium_basis=basis, tax_mode=mode,
                tax_rate_bp=bp, tax_cents=total, insurance_tax_cents=insurance,
                fire_tax_cents=fire, motor_tax_cents=motor, kfz_coverage=coverage,
                fire_mode=fire_mode, fire_rate_bp=fire_bp, motor_mode=motor_mode,
                vehicle_class=vehicle_class, drive_type=d.get('drive_type', ''),
                first_registration=d.get('first_registration', ''),
                power_kw=d.get('power_kw', ''), co2_g_km=d.get('co2_g_km', ''),
                weight_kg=d.get('weight_kg', ''), wltp=d.get('wltp', 'yes'),
                gross_weight_kg=gross_weight_kg, vehicle_tax_cents=vehicle_tax_cents,
                vehicle_tax_case=vehicle_tax_case if heavy else 'standard',
                casco_net_cents=casco_net, casco_premium_basis=casco_premium_basis if coverage == 'combined' else 'net',
                casco_premium_cents=casco_premium)

SCHEMA = '''
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS customers(
 id INTEGER PRIMARY KEY,
 customer_type TEXT NOT NULL DEFAULT 'natural' CHECK(customer_type IN ('natural','legal')),
 first_name TEXT NOT NULL, last_name TEXT NOT NULL,
 company TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '',
 address TEXT NOT NULL DEFAULT '', postal_code TEXT NOT NULL DEFAULT '', city TEXT NOT NULL DEFAULT '',
 birthday TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', archived INTEGER NOT NULL DEFAULT 0,
 version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS customer_relationships(
 id INTEGER PRIMARY KEY,
 customer_id INTEGER NOT NULL REFERENCES customers(id),
 related_customer_id INTEGER NOT NULL REFERENCES customers(id),
 relationship TEXT NOT NULL, reciprocal_relationship TEXT NOT NULL,
 notes TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
 CHECK(customer_id<>related_customer_id), UNIQUE(customer_id,related_customer_id));
CREATE TABLE IF NOT EXISTS referrers(
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '',
 address TEXT NOT NULL DEFAULT '', iban TEXT NOT NULL DEFAULT '', rate_bp INTEGER NOT NULL CHECK(rate_bp BETWEEN 0 AND 10000),
 notes TEXT NOT NULL DEFAULT '', archived INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS contracts(
 id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id),
 insurer TEXT NOT NULL, policy_number TEXT NOT NULL UNIQUE, line TEXT NOT NULL,
 ownership TEXT NOT NULL DEFAULT 'Eigen' CHECK(ownership IN ('Eigen','Fremd')),
 status TEXT NOT NULL CHECK(status IN ('Beantragt','Aktiv','Gekündigt','Storniert','Abgelaufen')),
 start_date TEXT NOT NULL, end_date TEXT NOT NULL DEFAULT '', notice_date TEXT NOT NULL DEFAULT '',
 premium_cents INTEGER NOT NULL CHECK(premium_cents>=0), frequency INTEGER NOT NULL CHECK(frequency IN(1,2,4,12)),
 referrer_id INTEGER REFERENCES referrers(id), rate_bp INTEGER NOT NULL CHECK(rate_bp BETWEEN 0 AND 10000),
 commission_basis TEXT NOT NULL CHECK(commission_basis IN('agency','premium','fixed')),
 notes TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settlements(
 id INTEGER PRIMARY KEY, number TEXT NOT NULL UNIQUE, referrer_id INTEGER NOT NULL REFERENCES referrers(id),
 period_start TEXT NOT NULL, period_end TEXT NOT NULL, total_cents INTEGER NOT NULL,
 snapshot TEXT NOT NULL, created_at TEXT NOT NULL, paid_at TEXT NOT NULL DEFAULT '', payment_reference TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS commissions(
 id INTEGER PRIMARY KEY, contract_id INTEGER NOT NULL REFERENCES contracts(id),
 referrer_id INTEGER NOT NULL REFERENCES referrers(id), booking_date TEXT NOT NULL, kind TEXT NOT NULL,
 basis TEXT NOT NULL CHECK(basis IN('agency','premium','fixed')), base_cents INTEGER NOT NULL,
 rate_bp INTEGER NOT NULL, amount_cents INTEGER NOT NULL, reference TEXT NOT NULL UNIQUE,
 memo TEXT NOT NULL DEFAULT '', settlement_id INTEGER REFERENCES settlements(id),
 reverses_id INTEGER UNIQUE REFERENCES commissions(id), voided INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS claims(
 id INTEGER PRIMARY KEY, number TEXT NOT NULL UNIQUE,
 customer_id INTEGER NOT NULL REFERENCES customers(id), contract_id INTEGER REFERENCES contracts(id),
 insurer_claim_number TEXT NOT NULL DEFAULT '', line TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL CHECK(status IN ('Entwurf','Gemeldet','In Bearbeitung','Warten auf Unterlagen','Reguliert','Abgelehnt','Geschlossen')),
 event_date TEXT NOT NULL DEFAULT '', event_time TEXT NOT NULL DEFAULT '', reported_date TEXT NOT NULL DEFAULT '',
 location TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '', cause TEXT NOT NULL DEFAULT '',
 claimant TEXT NOT NULL DEFAULT '', third_party TEXT NOT NULL DEFAULT '', police_reference TEXT NOT NULL DEFAULT '',
 estimated_cents INTEGER CHECK(estimated_cents IS NULL OR estimated_cents>=0),
 deductible_cents INTEGER CHECK(deductible_cents IS NULL OR deductible_cents>=0),
 paid_cents INTEGER CHECK(paid_cents IS NULL OR paid_cents>=0),
 handler TEXT NOT NULL DEFAULT '', contact TEXT NOT NULL DEFAULT '',
 reminder_date TEXT NOT NULL DEFAULT '', reminder_note TEXT NOT NULL DEFAULT '',
 coverage_status TEXT NOT NULL DEFAULT '', details TEXT NOT NULL DEFAULT '{}',
 version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS claim_emails(
 id INTEGER PRIMARY KEY, claim_id INTEGER NOT NULL REFERENCES claims(id),
 direction TEXT NOT NULL CHECK(direction IN ('Eingang','Ausgang','Notiz')),
 email_date TEXT NOT NULL DEFAULT '', from_address TEXT NOT NULL DEFAULT '',
 to_address TEXT NOT NULL DEFAULT '', cc_address TEXT NOT NULL DEFAULT '',
 subject TEXT NOT NULL DEFAULT '', body TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS documents(
 id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id), contract_id INTEGER REFERENCES contracts(id),
 filename TEXT NOT NULL, storage_name TEXT NOT NULL UNIQUE, size INTEGER NOT NULL, created_at TEXT NOT NULL,
 claim_id INTEGER REFERENCES claims(id));
CREATE TABLE IF NOT EXISTS audit(
 id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, action TEXT NOT NULL, entity TEXT NOT NULL, entity_id INTEGER, detail TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_contracts_customer ON contracts(customer_id);
CREATE INDEX IF NOT EXISTS idx_customer_relationships_customer ON customer_relationships(customer_id);
CREATE INDEX IF NOT EXISTS idx_customer_relationships_related ON customer_relationships(related_customer_id);
CREATE INDEX IF NOT EXISTS idx_commissions_referrer_date ON commissions(referrer_id,booking_date);
CREATE INDEX IF NOT EXISTS idx_documents_customer ON documents(customer_id);
CREATE INDEX IF NOT EXISTS idx_claims_customer ON claims(customer_id);
CREATE INDEX IF NOT EXISTS idx_claims_contract ON claims(contract_id);
CREATE INDEX IF NOT EXISTS idx_claims_reminder ON claims(status,reminder_date);
CREATE INDEX IF NOT EXISTS idx_claim_emails_claim ON claim_emails(claim_id,email_date);
'''

class Problem(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def connect():
    c = sqlite3.connect(DB, timeout=20)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    return c


def initialize():
    os.umask(0o077)
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / 'Dokumente').mkdir(exist_ok=True)
    (DATA / 'Sicherungen').mkdir(exist_ok=True)
    with connect() as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript(SCHEMA)
        customer_columns = {r['name'] for r in c.execute('PRAGMA table_info(customers)')}
        if 'customer_type' not in customer_columns:
            c.execute("ALTER TABLE customers ADD COLUMN customer_type TEXT NOT NULL DEFAULT 'natural' CHECK(customer_type IN ('natural','legal'))")
        columns = {r['name'] for r in c.execute('PRAGMA table_info(contracts)')}
        for name, definition in {
            'premium_basis': "TEXT NOT NULL DEFAULT 'gross'",
            'tax_mode': "TEXT NOT NULL DEFAULT 'legacy'",
            'tax_rate_bp': 'INTEGER', 'tax_cents': 'INTEGER',
            'life_tax_case': "TEXT NOT NULL DEFAULT 'standard'",
            'ownership': "TEXT NOT NULL DEFAULT 'Eigen' CHECK(ownership IN ('Eigen','Fremd'))",
            'insurance_tax_cents': 'INTEGER', 'fire_tax_cents': 'INTEGER',
            'motor_tax_cents': 'INTEGER', 'kfz_coverage': "TEXT NOT NULL DEFAULT ''",
            'fire_mode': "TEXT NOT NULL DEFAULT 'auto'", 'fire_rate_bp': 'INTEGER',
            'motor_mode': "TEXT NOT NULL DEFAULT 'none'", 'vehicle_class': "TEXT NOT NULL DEFAULT 'M1'",
            'drive_type': "TEXT NOT NULL DEFAULT ''", 'first_registration': "TEXT NOT NULL DEFAULT ''",
            'power_kw': "TEXT NOT NULL DEFAULT ''", 'co2_g_km': "TEXT NOT NULL DEFAULT ''",
            'weight_kg': "TEXT NOT NULL DEFAULT ''", 'wltp': "TEXT NOT NULL DEFAULT 'yes'",
            'gross_weight_kg': "TEXT NOT NULL DEFAULT ''", 'vehicle_tax_cents': 'INTEGER',
            'vehicle_tax_case': "TEXT NOT NULL DEFAULT 'standard'",
            'casco_net_cents': 'INTEGER',
            'casco_premium_basis': "TEXT NOT NULL DEFAULT 'net'",
            'casco_premium_cents': 'INTEGER',
        }.items():
            if name not in columns:
                c.execute(f'ALTER TABLE contracts ADD COLUMN {name} {definition}')
        document_columns = {r['name'] for r in c.execute('PRAGMA table_info(documents)')}
        if 'claim_id' not in document_columns:
            c.execute('ALTER TABLE documents ADD COLUMN claim_id INTEGER REFERENCES claims(id)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_documents_claim ON documents(claim_id)')
        c.execute("INSERT OR IGNORE INTO settings VALUES('schema_version','1')")
        c.execute("INSERT OR IGNORE INTO settings VALUES('agency_name','Versicherungsagentur Bogaards')")
        c.execute("INSERT OR IGNORE INTO settings VALUES('agency_address','')")


def setting(c, key, default=''):
    r = c.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    return r[0] if r else default


def audit(c, action, entity, entity_id=None, detail=''):
    c.execute('INSERT INTO audit(created_at,action,entity,entity_id,detail) VALUES(?,?,?,?,?)',
              (now(), action, entity, entity_id, detail))


def txt(d, key, required=False, maximum=4000):
    value = d.get(key, '')
    if not isinstance(value, str):
        raise Problem('Ungültiger Text: ' + key)
    value = value.strip()
    if (required and not value) or len(value) > maximum:
        raise Problem('Bitte das Feld „' + key + '“ korrekt ausfüllen.')
    return value


def integer(value, label='Kennung'):
    if isinstance(value, bool) or not re.fullmatch(r'\d+', str(value)):
        raise Problem(label + ' ist ungültig.')
    return int(value)


def money(value):
    s = str(value).strip().replace(',', '.')
    if not re.fullmatch(r'\d{1,10}(\.\d{1,2})?', s):
        raise Problem('Beträge bitte positiv mit höchstens zwei Nachkommastellen eingeben (z. B. 1250,50).')
    return int(Decimal(s) * 100)


def rate(value):
    n = money(value)
    if n > 10000:
        raise Problem('Der Provisionssatz muss zwischen 0 und 100 % liegen.')
    return n


def iso(value, optional=False):
    if not value and optional:
        return ''
    try:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(value)):
            raise ValueError()
        return date.fromisoformat(value).isoformat()
    except (ValueError, TypeError):
        raise Problem('Bitte ein gültiges Datum eingeben.')


def get(c, table, ident):
    r = c.execute(f'SELECT * FROM {table} WHERE id=?', (integer(ident),)).fetchone()
    if not r:
        raise Problem('Eintrag nicht gefunden.', 404)
    return dict(r)


def calculated(base, bp):
    return int((Decimal(base) * Decimal(bp) / 10000).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def optional_money(value):
    return None if value in (None, '') else money(value)


def local_datetime(value):
    if not value:
        return ''
    try:
        return datetime.strptime(str(value), '%Y-%m-%dT%H:%M').strftime('%Y-%m-%dT%H:%M')
    except (ValueError, TypeError):
        raise Problem('Bitte ein gültiges Datum mit Uhrzeit eingeben.')


def password_hash(password, salt=None):
    if not isinstance(password, str) or len(password) < 12 or len(password) > 256:
        raise Problem('Bitte ein Passwort mit 12 bis 256 Zeichen verwenden.')
    salt = salt or secrets.token_hex(24)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600000).hex()
    return salt + ':' + digest


def verify_password(c, password):
    stored = setting(c, 'password_hash')
    if not stored:
        return False
    try:
        return hmac.compare_digest(password_hash(password, stored.split(':')[0]), stored)
    except Problem:
        return False


def save_record(c, table, d):
    ident = d.get('id')
    old = get(c, table, ident) if ident else None
    if table == 'customers':
        customer_type = d.get('customer_type', old['customer_type'] if old else 'natural')
        if customer_type not in ('natural', 'legal'):
            raise Problem('Bitte „Natürliche Person“ oder „Juristische Person“ auswählen.')
        fields = {k: txt(d,k) for k in
                  ('first_name','last_name','company','email','phone','address','postal_code','city','notes')}
        if customer_type == 'natural' and (not fields['first_name'] or not fields['last_name']):
            raise Problem('Für eine natürliche Person bitte Vor- und Nachname ausfüllen.')
        if customer_type == 'legal' and not fields['company']:
            raise Problem('Für eine juristische Person bitte den Firmennamen ausfüllen.')
        fields['customer_type'] = customer_type
        fields['birthday'] = iso(d.get('birthday',''), True) if customer_type == 'natural' else ''
        if fields['birthday'] and fields['birthday'] > date.today().isoformat():
            raise Problem('Das Geburtsdatum darf nicht in der Zukunft liegen.')
        fields['archived'] = int(bool(d.get('archived',False)))
    elif table == 'customer_relationships':
        customer_id = get(c, 'customers', d.get('customer_id'))['id']
        related_customer_id = get(c, 'customers', d.get('related_customer_id'))['id']
        if customer_id == related_customer_id:
            raise Problem('Ein Kunde kann nicht mit sich selbst verknüpft werden.')
        duplicate = c.execute('''SELECT id FROM customer_relationships
            WHERE id<>? AND ((customer_id=? AND related_customer_id=?)
                         OR (customer_id=? AND related_customer_id=?))''',
            (integer(ident) if ident else 0, customer_id, related_customer_id,
             related_customer_id, customer_id)).fetchone()
        if duplicate:
            raise Problem('Zwischen diesen Kunden besteht bereits eine Verbindung.', 409)
        fields = dict(customer_id=customer_id, related_customer_id=related_customer_id,
                      relationship=txt(d, 'relationship', True, 100),
                      reciprocal_relationship=txt(d, 'reciprocal_relationship', True, 100),
                      notes=txt(d, 'notes', False, 1000))
    elif table == 'referrers':
        fields = {k: txt(d,k,k=='name') for k in ('name','email','phone','address','iban','notes')}
        fields['rate_bp'] = rate(d.get('rate','0'))
        fields['archived'] = int(bool(d.get('archived',False)))
    elif table == 'contracts':
        cust = get(c,'customers',d.get('customer_id'))
        if cust['archived'] and (not old or old['customer_id'] != cust['id']):
            raise Problem('Für archivierte Kunden können keine neuen Verträge angelegt werden.')
        fields = {k: txt(d,k,k in ('insurer','policy_number','line','status')) for k in
                  ('insurer','policy_number','line','status','notes')}
        fields['ownership'] = d.get('ownership', 'Eigen')
        if fields['ownership'] not in ('Eigen', 'Fremd'):
            raise Problem('Bitte bei Vertragsherkunft „Eigen“ oder „Fremd“ wählen.')
        if fields['status'] not in ('Beantragt','Aktiv','Gekündigt','Storniert','Abgelaufen'):
            raise Problem('Ungültiger Vertragsstatus.')
        fields['customer_id'] = cust['id']
        fields['start_date'] = iso(d.get('start_date'))
        fields['end_date'] = iso(d.get('end_date',''),True)
        fields['notice_date'] = iso(d.get('notice_date',''),True)
        if fields['end_date'] and fields['end_date'] < fields['start_date']:
            raise Problem('Das Vertragsende muss nach dem Beginn liegen.')
        if fields['end_date'] and fields['notice_date'] and fields['notice_date'] > fields['end_date']:
            raise Problem('Die Kündigungsfrist darf nicht nach dem Vertragsende liegen.')
        if old and old['tax_mode'] != 'legacy' and 'tax_mode' not in d:
            raise Problem('Bitte das Vertragsformular neu laden, um die Steuerangaben zu erhalten.')
        fields.update(premium_fields(d))
        if not is_kfz(fields['line']) or fields['tax_mode'] == 'legacy':
            fields.update(insurance_tax_cents=fields['tax_cents'], fire_tax_cents=None,
                          motor_tax_cents=None, kfz_coverage='', fire_mode='auto',
                          fire_rate_bp=None, motor_mode='none', vehicle_class='M1',
                          drive_type='', first_registration='', power_kw='', co2_g_km='',
                          weight_kg='', wltp='yes', gross_weight_kg='',
                          vehicle_tax_cents=None, vehicle_tax_case='standard', casco_net_cents=None,
                          casco_premium_basis='net', casco_premium_cents=None)
        fields['life_tax_case'] = d.get('life_tax_case', 'standard')
        if fields['life_tax_case'] not in ('standard', 'single_short'):
            raise Problem('Ungültige Steuerregel für Lebensversicherungen.')
        fields['frequency'] = integer(d.get('frequency',12),'Zahlungsrhythmus')
        if fields['frequency'] not in (1,2,4,12):
            raise Problem('Ungültiger Zahlungsrhythmus.')
        rid = d.get('referrer_id')
        fields['referrer_id'] = get(c,'referrers',rid)['id'] if rid else None
        fields['rate_bp'] = rate(d.get('rate','0'))
        fields['commission_basis'] = d.get('commission_basis','agency')
        if fields['commission_basis'] not in ('agency','premium','fixed'):
            raise Problem('Ungültige Provisionsbasis.')
    else:
        raise Problem('Unbekannter Datensatz.')
    if old:
        version = integer(d.get('version'),'Version')
        fields['version'] = version + 1
        cur = c.execute(f"UPDATE {table} SET " + ','.join(k+'=?' for k in fields) + ' WHERE id=? AND version=?',
                        (*fields.values(),ident,version))
        if cur.rowcount != 1:
            raise Problem('Der Eintrag wurde inzwischen geändert. Bitte neu laden und erneut bearbeiten.',409)
        audit(c,'Geändert',table,ident)
    else:
        fields['created_at'] = now()
        cur = c.execute(f"INSERT INTO {table} (" + ','.join(fields) + ') VALUES(' + ','.join('?' for _ in fields) + ')',tuple(fields.values()))
        ident = cur.lastrowid
        audit(c,'Angelegt',table,ident)
    return {'id':ident}


def book_commission(c,d):
    contract = get(c,'contracts',d.get('contract_id'))
    rid = contract['referrer_id']
    if not rid:
        raise Problem('Bitte zuerst einen Tippgeber im Vertrag hinterlegen.')
    referrer = get(c,'referrers',rid)
    if referrer['archived']:
        raise Problem('Dieser Tippgeber ist archiviert.')
    if contract['status'] == 'Storniert':
        raise Problem('Für stornierte Verträge sind nur Gegenbuchungen zu bestehenden Provisionen möglich.')
    basis = contract['commission_basis']
    if basis == 'premium':
        if contract['tax_cents'] is None:
            raise Problem('Bitte zuerst im Vertrag die Versicherungssteuer aufschlüsseln, damit die Nettoprämie feststeht.')
        period = d.get('premium_period', 'payment')
        if period not in ('payment', 'year'):
            raise Problem('Ungültiger Prämienzeitraum.')
        base = (contract['premium_cents'] - contract['tax_cents']) * (contract['frequency'] if period == 'year' else 1)
    else:
        base = money(d.get('base','0'))
    bp = contract['rate_bp']
    amount = base if basis == 'fixed' else calculated(base,bp)
    if amount <= 0:
        raise Problem('Die berechnete Provision muss größer als 0 € sein.')
    kind = txt(d,'kind',True,50)
    if kind not in ('Abschluss','Folge','Sondervergütung'):
        raise Problem('Ungültige Provisionsart.')
    cur = c.execute('''INSERT INTO commissions(contract_id,referrer_id,booking_date,kind,basis,base_cents,
      rate_bp,amount_cents,reference,memo,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
      (contract['id'],rid,iso(d.get('booking_date')),kind,basis,base,bp,amount,
       txt(d,'reference',True,120),txt(d,'memo'),now()))
    audit(c,'Provision gebucht','commissions',cur.lastrowid)
    return {'id':cur.lastrowid,'amount_cents':amount}


def save_claim(c, d):
    ident = d.get('id')
    old = get(c, 'claims', ident) if ident else None
    customer = get(c, 'customers', d.get('customer_id'))
    contract = get(c, 'contracts', d['contract_id']) if d.get('contract_id') else None
    if contract and contract['customer_id'] != customer['id']:
        raise Problem('Dieser Vertrag gehört zu einem anderen Kunden.')
    status = d.get('status', 'Entwurf')
    if status not in CLAIM_STATUSES:
        raise Problem('Ungültiger Schadenstatus.')
    event_time = txt(d, 'event_time', False, 5)
    if event_time and not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', event_time):
        raise Problem('Bitte eine gültige Schadenzeit eingeben.')
    details = {key: txt(d, key) for key in CLAIM_DETAIL_FIELDS if d.get(key)}
    fields = {
        'customer_id': customer['id'], 'contract_id': contract['id'] if contract else None,
        'insurer_claim_number': txt(d, 'insurer_claim_number', False, 200),
        'line': contract['line'] if contract else txt(d, 'line', False, 200),
        'status': status, 'event_date': iso(d.get('event_date', ''), True),
        'event_time': event_time, 'reported_date': iso(d.get('reported_date', ''), True),
        'location': txt(d, 'location'), 'title': txt(d, 'title', False, 300),
        'description': txt(d, 'description'), 'cause': txt(d, 'cause'),
        'claimant': txt(d, 'claimant'), 'third_party': txt(d, 'third_party'),
        'police_reference': txt(d, 'police_reference', False, 300),
        'estimated_cents': optional_money(d.get('estimated', '')),
        'deductible_cents': optional_money(d.get('deductible', '')),
        'paid_cents': optional_money(d.get('paid', '')),
        'handler': txt(d, 'handler', False, 300), 'contact': txt(d, 'contact', False, 500),
        'reminder_date': iso(d.get('reminder_date', ''), True),
        'reminder_note': txt(d, 'reminder_note', False, 1000),
        'coverage_status': txt(d, 'coverage_status', False, 200),
        'details': json.dumps(details, ensure_ascii=False, separators=(',', ':')),
        'updated_at': now()
    }
    if old:
        version = integer(d.get('version'), 'Version')
        fields['version'] = version + 1
        cur = c.execute('UPDATE claims SET ' + ','.join(k+'=?' for k in fields) +
                        ' WHERE id=? AND version=?', (*fields.values(), ident, version))
        if cur.rowcount != 1:
            raise Problem('Der Schaden wurde inzwischen geändert. Bitte neu laden und erneut bearbeiten.', 409)
        audit(c, 'Geändert', 'claims', ident)
    else:
        seq = c.execute('SELECT COALESCE(MAX(id),0)+1 FROM claims').fetchone()[0]
        fields = {'number': f'SCH-{date.today().year}-{seq:05d}', **fields,
                  'created_at': now()}
        cur = c.execute('INSERT INTO claims (' + ','.join(fields) + ') VALUES(' +
                        ','.join('?' for _ in fields) + ')', tuple(fields.values()))
        ident = cur.lastrowid
        audit(c, 'Angelegt', 'claims', ident, fields['number'])
    return {'id': ident}


def save_claim_email(c, d):
    claim = get(c, 'claims', d.get('claim_id'))
    direction = d.get('direction', 'Eingang')
    if direction not in ('Eingang', 'Ausgang', 'Notiz'):
        raise Problem('Ungültige E-Mail-Richtung.')
    fields = (claim['id'], direction, local_datetime(d.get('email_date', '')),
              txt(d, 'from_address', False, 500), txt(d, 'to_address', False, 500),
              txt(d, 'cc_address', False, 500), txt(d, 'subject', False, 500),
              txt(d, 'body', False, 12000), now())
    if not fields[6] and not fields[7]:
        raise Problem('Bitte mindestens Betreff oder Inhalt der E-Mail eintragen.')
    cur = c.execute('''INSERT INTO claim_emails(claim_id,direction,email_date,from_address,to_address,
                     cc_address,subject,body,created_at) VALUES(?,?,?,?,?,?,?,?,?)''', fields)
    audit(c, 'E-Mail abgelegt', 'claims', claim['id'], fields[6])
    return {'id': cur.lastrowid, 'claim_id': claim['id']}


def reverse_commission(c,d):
    original = get(c,'commissions',d.get('id'))
    if original['voided'] or original['reverses_id'] or c.execute('SELECT 1 FROM commissions WHERE reverses_id=?',(original['id'],)).fetchone():
        raise Problem('Diese Provision wurde bereits aufgehoben oder ist selbst eine Gegenbuchung.',409)
    reason = txt(d,'reason',True)
    if not original['settlement_id']:
        c.execute('UPDATE commissions SET voided=1 WHERE id=?',(original['id'],))
        audit(c,'Offene Provision aufgehoben','commissions',original['id'],reason)
    else:
        cur = c.execute('''INSERT INTO commissions(contract_id,referrer_id,booking_date,kind,basis,base_cents,
           rate_bp,amount_cents,reference,memo,reverses_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
           (original['contract_id'],original['referrer_id'],date.today().isoformat(),'Gegenbuchung',original['basis'],
            -original['base_cents'],original['rate_bp'],-original['amount_cents'],
            'STORNO-'+str(original['id'])+'-'+secrets.token_hex(3),reason,original['id'],now()))
        audit(c,'Gegenbuchung erstellt','commissions',cur.lastrowid,reason)
    return {'ok':True}


def settle(c,d):
    rid = integer(d.get('referrer_id'))
    referrer = get(c,'referrers',rid)
    start,end = iso(d.get('period_start')),iso(d.get('period_end'))
    if end < start:
        raise Problem('Das Ende des Zeitraums liegt vor dem Beginn.')
    rows = [dict(r) for r in c.execute('''SELECT m.*, v.policy_number, v.insurer,
      k.customer_type, k.first_name, k.last_name, k.company
      FROM commissions m JOIN contracts v ON v.id=m.contract_id JOIN customers k ON k.id=v.customer_id
      WHERE m.referrer_id=? AND m.booking_date BETWEEN ? AND ? AND m.settlement_id IS NULL AND m.voided=0
      ORDER BY m.booking_date,m.id''',(rid,start,end))]
    if not rows:
        raise Problem('Keine offenen Provisionen für diesen Tippgeber im gewählten Zeitraum.')
    total = sum(r['amount_cents'] for r in rows)
    seq = c.execute('SELECT COALESCE(MAX(id),0)+1 FROM settlements').fetchone()[0]
    number = f'TG-{date.today().year}-{seq:05d}'
    snapshot = {'referrer':referrer,'agency':setting(c,'agency_name'),'address':setting(c,'agency_address'),'items':rows}
    cur = c.execute('''INSERT INTO settlements(number,referrer_id,period_start,period_end,total_cents,snapshot,created_at)
           VALUES(?,?,?,?,?,?,?)''',(number,rid,start,end,total,json.dumps(snapshot,ensure_ascii=False),now()))
    ident = cur.lastrowid
    c.executemany('UPDATE commissions SET settlement_id=? WHERE id=?',[(ident,r['id']) for r in rows])
    audit(c,'Abrechnung erstellt','settlements',ident,number)
    return {'id':ident,'number':number}


def state(c):
    result = {t:[dict(r) for r in c.execute(f'SELECT * FROM {t} ORDER BY id DESC')] for t in
              ('customers','customer_relationships','contracts','referrers','commissions','documents','claims','claim_emails')}
    for claim in result['claims']:
        try:
            claim['details'] = json.loads(claim['details'])
        except (ValueError, TypeError):
            claim['details'] = {}
    result['settlements'] = [dict(r) for r in c.execute('''SELECT id,number,referrer_id,period_start,period_end,total_cents,
                                         created_at,paid_at,payment_reference FROM settlements ORDER BY id DESC''')]
    result['audit'] = [dict(r) for r in c.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 100')]
    result['settings'] = {k:setting(c,k) for k in ('agency_name','agency_address')}
    result['tax_rules'] = TAX_RULES
    return result


DELETABLE = ('customers', 'customer_relationships', 'contracts', 'claims', 'referrers', 'commissions',
             'settlements', 'documents', 'claim_emails')


def delete_record(c, table, ident):
    if table not in DELETABLE:
        raise Problem('Dieser Datensatz kann nicht gelöscht werden.')
    record = get(c, table, ident)
    ident = record['id']
    def count(child, column):
        return c.execute(f'SELECT count(*) FROM {child} WHERE {column}=?', (ident,)).fetchone()[0]
    def blocked(links):
        present = [f'{count(child, column)} {label}' for child, column, label in links
                   if count(child, column)]
        if present:
            raise Problem('Bitte zuerst die verknüpften Einträge klären: ' + ', '.join(present) + '.', 409)
    files = []
    if table == 'customers':
        blocked((('contracts', 'customer_id', 'Verträge'),
                 ('claims', 'customer_id', 'Schäden')))
        documents = c.execute('SELECT id,storage_name FROM documents WHERE customer_id=?', (ident,)).fetchall()
        files = [row['storage_name'] for row in documents]
        c.execute('DELETE FROM documents WHERE customer_id=?', (ident,))
        c.execute('DELETE FROM customer_relationships WHERE customer_id=? OR related_customer_id=?', (ident, ident))
    elif table == 'contracts':
        blocked((('claims', 'contract_id', 'Schäden'),
                 ('commissions', 'contract_id', 'Provisionen')))
        documents = c.execute('SELECT storage_name FROM documents WHERE contract_id=?', (ident,)).fetchall()
        files = [row['storage_name'] for row in documents]
        c.execute('DELETE FROM documents WHERE contract_id=?', (ident,))
    elif table == 'claims':
        documents = c.execute('SELECT storage_name FROM documents WHERE claim_id=?', (ident,)).fetchall()
        files = [row['storage_name'] for row in documents]
        c.execute('DELETE FROM documents WHERE claim_id=?', (ident,))
        c.execute('DELETE FROM claim_emails WHERE claim_id=?', (ident,))
    elif table == 'referrers':
        blocked((('contracts', 'referrer_id', 'Verträge'),
                 ('commissions', 'referrer_id', 'Provisionen'),
                 ('settlements', 'referrer_id', 'Abrechnungen')))
    elif table == 'commissions':
        blocked((('commissions', 'reverses_id', 'Gegenbuchungen'),))
        if record['settlement_id']:
            raise Problem('Diese Provision gehört zu einer Abrechnung. Bitte zuerst die Abrechnung löschen.', 409)
    elif table == 'settlements':
        c.execute('UPDATE commissions SET settlement_id=NULL WHERE settlement_id=?', (ident,))
    elif table == 'documents':
        files = [record['storage_name']]
    c.execute(f'DELETE FROM {table} WHERE id=?', (ident,))
    audit(c, 'Gelöscht', table, ident,
          record.get('filename') or record.get('number') or record.get('policy_number') or '')
    return {'ok': True, 'files': files}


def document_filename(d):
    filename = txt(d, 'filename', True, 200)
    if '/' in filename or '\\' in filename or any(ord(x) < 32 for x in filename):
        raise Problem('Ungültiger Dateiname.')
    return filename


def uploaded_file(d):
    filename = document_filename(d)
    try:
        raw = base64.b64decode(d.get('data', ''), validate=True)
    except (ValueError, TypeError):
        raise Problem('Ungültige Datei.')
    if not raw or len(raw) > 20 * 1024 * 1024:
        raise Problem('Bitte eine Datei zwischen 1 Byte und 20 MB wählen.')
    return filename, raw


def rename_document(c, d):
    document = get(c, 'documents', d.get('id'))
    filename = document_filename(d)
    if filename != document['filename']:
        c.execute('UPDATE documents SET filename=? WHERE id=?', (filename, document['id']))
        audit(c, 'Dokument umbenannt', 'documents', document['id'],
              document['filename'] + ' → ' + filename)
    return {'id': document['id'], 'filename': filename}


def store_document(c, customer_id, contract_id, filename, raw):
    storage = secrets.token_hex(24)
    target = DATA / 'Dokumente' / storage
    target.write_bytes(raw)
    try:
        cur = c.execute('''INSERT INTO documents(customer_id,contract_id,filename,storage_name,size,created_at)
                           VALUES(?,?,?,?,?,?)''',
                        (customer_id, contract_id, filename, storage, len(raw), now()))
        audit(c, 'Dokument abgelegt', 'documents', cur.lastrowid)
        return cur.lastrowid, target
    except Exception:
        target.unlink(missing_ok=True)
        raise


def analyze_import(c, d):
    filename, raw = uploaded_file(d)
    try:
        text, meta, kind = importer.extract_text(filename, raw, ROOT / 'native_extract.swift')
    except ValueError as exc:
        raise Problem(str(exc))
    result = importer.suggest(text, meta, kind)
    result['filename'] = filename
    result['matches'] = importer.matches(c, result)
    return result


def commit_import(c, d):
    filename, raw = uploaded_file(d)
    customer_data = d.get('customer') if isinstance(d.get('customer'), dict) else {}
    contract_data = d.get('contract') if isinstance(d.get('contract'), dict) else {}
    contract_action = d.get('contract_action', 'none')
    contract_id = None
    contract_created = False
    policy_key = importer.normalized(str(contract_data.get('policy_number', '')))
    policy_match = next((dict(row) for row in c.execute('SELECT * FROM contracts')
                         if policy_key and importer.normalized(row['policy_number']) == policy_key), None)
    if policy_match:
        contract_action = 'existing'
    if contract_action == 'existing':
        existing = policy_match or get(c, 'contracts', d.get('contract_id'))
        contract_id = existing['id']
        customer_id = existing['customer_id']
        customer_created = False
    else:
        if d.get('customer_action') == 'existing':
            customer = get(c, 'customers', d.get('customer_id'))
            if customer['archived']:
                raise Problem('Der ausgewählte Kunde ist archiviert.')
            if d.get('update_customer'):
                updates = {}
                for key in ('company', 'email', 'phone', 'address', 'postal_code', 'city', 'birthday'):
                    value = txt(customer_data, key, False, 4000)
                    if value and not customer[key]:
                        updates[key] = iso(value, True) if key == 'birthday' else value
                if updates:
                    c.execute('UPDATE customers SET ' + ','.join(k + '=?' for k in updates) +
                              ',version=version+1 WHERE id=?', (*updates.values(), customer['id']))
                    audit(c, 'Durch Import ergänzt', 'customers', customer['id'], filename)
            customer_id = customer['id']
            customer_created = False
        elif d.get('customer_action') == 'new':
            customer_data = dict(customer_data)
            customer_data['notes'] = txt(customer_data, 'notes', False) or 'Angelegt durch Dokumentimport: ' + filename
            customer_id = save_record(c, 'customers', customer_data)['id']
            customer_created = True
        else:
            raise Problem('Bitte bestehenden oder neuen Kunden auswählen.')

    if contract_action == 'new':
        payload = dict(contract_data)
        payload.update({'customer_id': customer_id, 'referrer_id': '', 'rate': '0',
                        'commission_basis': 'agency', 'premium_basis': 'gross',
                        'tax_mode': 'auto' if not is_kfz(str(payload.get('line', ''))) and tax_rate(str(payload.get('line', ''))) is not None else 'legacy'})
        payload['notes'] = txt(payload, 'notes', False) or 'Angelegt durch Dokumentimport: ' + filename
        contract_id = save_record(c, 'contracts', payload)['id']
        contract_created = True
    elif contract_action not in ('existing', 'none'):
        raise Problem('Bitte die Vertragszuordnung prüfen.')

    document_id, stored_path = store_document(c, customer_id, contract_id, filename, raw)
    try:
        audit(c, 'Import geprüft und übernommen', 'documents', document_id,
              ('Kunde neu; ' if customer_created else 'Kunde zugeordnet; ') +
              ('Vertrag neu' if contract_created else 'Vertrag zugeordnet' if contract_id else 'Kundenakte'))
    except Exception:
        stored_path.unlink(missing_ok=True)
        raise
    return {'customer_id': customer_id, 'contract_id': contract_id, 'document_id': document_id,
            'customer_created': customer_created, 'contract_created': contract_created}


def backup(c, automatic=False):
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+secrets.token_hex(2)
    target = DATA / 'Sicherungen' / ('CRM-'+stamp+'.zip')
    with tempfile.TemporaryDirectory() as td:
        snap = Path(td)/'crm.sqlite3'
        dest = sqlite3.connect(snap)
        try:
            c.backup(dest)
        finally:
            dest.close()
        with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as z:
            z.write(snap,'crm.sqlite3')
            for f in (DATA/'Dokumente').iterdir():
                if f.is_file():
                    z.write(f,'Dokumente/'+f.name)
    return target


def eur(n):
    return f'{n/100:,.2f}'.replace(',','X').replace('.',',').replace('X','.')+' €'


def de_date(value):
    if not value:
        return '—'
    try:
        parsed = date.fromisoformat(value[:10])
        return f'{parsed.day:02d}.{parsed.month:02d}.{parsed.year}'
    except (TypeError, ValueError):
        return str(value)


def customer_overview(c, ident):
    customer = get(c, 'customers', ident)
    contracts = [dict(row) for row in c.execute(
        "SELECT * FROM contracts WHERE customer_id=? ORDER BY CASE status WHEN 'Aktiv' THEN 0 WHEN 'Beantragt' THEN 1 ELSE 2 END, line, insurer",
        (customer['id'],))]
    agency = setting(c, 'agency_name')
    agency_address = setting(c, 'agency_address')
    esc = lambda value: html.escape(str(value if value not in (None, '') else '—'))
    name = (customer['company'] if customer['customer_type'] == 'legal' else
            (customer['first_name'] + ' ' + customer['last_name']).strip())
    customer_number = f"K-{customer['id']:05d}"
    address = ', '.join(part for part in
                        (customer['address'], (customer['postal_code'] + ' ' + customer['city']).strip()) if part)
    frequencies = {1: 'jährlich', 2: 'halbjährlich', 4: 'vierteljährlich', 12: 'monatlich'}
    coverage_names = {'liability': 'Kfz-Haftpflicht', 'casco': 'Kasko',
                      'combined': 'Kfz-Haftpflicht und Kasko'}
    vehicle_names = {'M1': 'Pkw M1', 'N1': 'Leichter Lkw N1', 'N2': 'Lkw N2',
                     'N3': 'Schwerer Lkw N3', 'other': 'Andere Fahrzeugklasse'}
    drive_names = {'combustion': 'Verbrenner / Hybrid', 'phev': 'Plug-in-Hybrid',
                   'electric': 'Elektro'}

    def fact(label, value, wide=False):
        return f'<div class="fact{" wide" if wide else ""}"><dt>{esc(label)}</dt><dd>{esc(value)}</dd></div>'

    cards = []
    for contract in contracts:
        gross = contract['premium_cents']
        tax = contract.get('tax_cents')
        net = None if tax is None else gross - tax
        facts = [
            fact('Polizzennummer', contract['policy_number']),
            fact('Versicherer', contract['insurer']),
            fact('Sparte', contract['line']),
            fact('Vertragsherkunft', contract.get('ownership') or 'Eigen'),
            fact('Versicherungsbeginn', de_date(contract['start_date'])),
            fact('Vertragsende', de_date(contract['end_date'])),
            fact('Kündigungstermin', de_date(contract['notice_date'])),
            fact('Zahlungsweise', frequencies.get(contract['frequency'], str(contract['frequency']))),
            fact('Bruttoprämie je Zahlung', eur(gross)),
            fact('Jahresbruttoprämie', eur(gross * contract['frequency'])),
            fact('Nettoprämie je Zahlung', eur(net) if net is not None else 'Nicht aufgeschlüsselt'),
            fact('Steuern je Zahlung', eur(tax) if tax is not None else 'Nicht aufgeschlüsselt'),
        ]
        if contract.get('kfz_coverage'):
            facts.extend((
                fact('Deckung', coverage_names.get(contract['kfz_coverage'], contract['kfz_coverage'])),
                fact('Fahrzeugklasse', vehicle_names.get(contract.get('vehicle_class'), contract.get('vehicle_class'))),
                fact('Antriebsart', drive_names.get(contract.get('drive_type'), contract.get('drive_type'))),
                fact('Erstzulassung', de_date(contract.get('first_registration'))),
                fact('Leistung', (contract.get('power_kw') + ' kW') if contract.get('power_kw') else '—'),
                fact('CO₂-Ausstoß', (contract.get('co2_g_km') + ' g/km') if contract.get('co2_g_km') else '—'),
                fact('Motorbezogene Steuer je Zahlung', eur(contract.get('motor_tax_cents') or 0)),
            ))
            if contract.get('gross_weight_kg'):
                facts.append(fact('Höchstes Gesamtgewicht', contract['gross_weight_kg'] + ' kg'))
            if contract.get('vehicle_tax_cents') is not None:
                facts.append(fact('Separate Kraftfahrzeugsteuer', eur(contract['vehicle_tax_cents']) + ' pro Monat'))
        cards.append(f'''<section class="contract-card">
          <div class="contract-head"><div><span class="contract-line">{esc(contract['line'])}</span><h3>{esc(contract['insurer'])}</h3></div>
          <span class="status status-{html.escape(contract['status'].lower())}">{esc(contract['status'])}</span></div>
          <dl class="contract-facts">{''.join(facts)}</dl></section>''')

    active_total = sum(item['premium_cents'] * item['frequency'] for item in contracts if item['status'] == 'Aktiv')
    contact_name = ((customer['first_name'] + ' ' + customer['last_name']).strip()
                    if customer['customer_type'] == 'legal' else '')
    return f'''<!doctype html><html lang="de"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1"><title>Vertragsübersicht - {esc(name)}</title>
    <link rel="stylesheet" href="/static/print.css"></head><body class="customer-overview">
    <div class="tools"><button id="print">Drucken / als PDF speichern</button></div>
    <header class="overview-header"><div><div class="print-eyebrow">VERTRAGSÜBERSICHT</div><h1>{esc(name)}</h1>
    <p>{esc(customer_number)} · Stand {esc(de_date(date.today().isoformat()))}</p></div><div class="agency"><strong>{esc(agency)}</strong><span>{esc(agency_address)}</span></div></header>
    <section class="customer-card"><h2>Kundendaten</h2><dl class="customer-facts">
    {fact('Kundentyp', 'Juristische Person' if customer['customer_type'] == 'legal' else 'Natürliche Person')}
    {fact('Kontaktperson', contact_name) if contact_name else ''}
    {fact('Adresse', address)}{fact('Telefon', customer['phone'])}{fact('E-Mail', customer['email'])}
    {fact('Geburtsdatum', de_date(customer['birthday'])) if customer['customer_type'] != 'legal' else ''}
    </dl></section>
    <section class="overview-summary"><div><span>Verträge gesamt</span><strong>{len(contracts)}</strong></div><div><span>Davon aktiv</span><strong>{sum(1 for item in contracts if item['status'] == 'Aktiv')}</strong></div><div><span>Aktive Jahresbruttoprämie</span><strong>{esc(eur(active_total))}</strong></div></section>
    <div class="section-title"><h2>Versicherungsverträge</h2><span>{len(contracts)} {"Vertrag" if len(contracts) == 1 else "Verträge"}</span></div>
    {''.join(cards) if cards else '<div class="print-empty">Zu diesem Kunden sind derzeit keine Versicherungsverträge erfasst.</div>'}
    <p class="print-note">Diese Übersicht bildet den im CRM gespeicherten Stand ab. Maßgeblich sind die jeweiligen Polizzen und Bedingungen des Versicherers. Separate Kraftfahrzeugsteuer ist nicht Bestandteil der ausgewiesenen Versicherungsprämie.</p>
    <footer class="print-footer"><span>{esc(agency)} · {esc(customer_number)}</span><span>Vertragsübersicht · Stand {esc(de_date(date.today().isoformat()))}</span></footer>
    <script src="/static/print.js"></script></body></html>'''.encode()


def statement(c,ident):
    s = get(c,'settlements',ident)
    snap = json.loads(s['snapshot'])
    esc = lambda x: html.escape(str(x))
    rows = ''.join('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in
               (r['booking_date'],r['reference'],r['policy_number'],
                r.get('company', '') if r.get('customer_type') == 'legal' else r['first_name']+' '+r['last_name'],
                {'agency':'Agenturprovision','premium':'Prämie','fixed':'Fixbetrag'}[r['basis']],eur(r['base_cents']),
                '—' if r['basis']=='fixed' else f"{r['rate_bp']/100:g} %",eur(r['amount_cents'])))+'</tr>' for r in snap['items'])
    return f'''<!doctype html><html lang="de"><meta charset="utf-8"><title>{esc(s['number'])}</title>
    <link rel="stylesheet" href="/static/print.css"><body><div class="tools"><button id="print">Drucken / als PDF speichern</button></div>
    <h1>{esc(snap['agency'])}</h1><p class="lines">{esc(snap['address'])}</p><hr>
    <h2>Tippgeber-Abrechnung {esc(s['number'])}</h2><p>Empfänger: <strong>{esc(snap['referrer']['name'])}</strong></p>
    <p class="lines">{esc(snap['referrer']['address'])}</p><p>Zeitraum: {esc(s['period_start'])} bis {esc(s['period_end'])}<br>
    Erstellt: {esc(s['created_at'][:10])}</p><table><thead><tr><th>Datum</th><th>Referenz</th><th>Polizze</th><th>Kunde</th><th>Basis</th><th>Betrag Basis</th><th>Satz</th><th>Provision</th></tr></thead><tbody>{rows}</tbody></table>
    <h2 class="total">Abrechnungssumme: {esc(eur(s['total_cents']))}</h2><p>IBAN: {esc(snap['referrer']['iban'] or 'Nicht hinterlegt')}</p>
    <p>Status: {esc('Erledigt am '+s['paid_at'] if s['paid_at'] else 'Offen')}<br> Zahlungs-/Ausgleichsreferenz: {esc(s['payment_reference'] or '—')}</p>
    <p class="note">Interne Provisionsabrechnung in EUR. Keine automatische Zahlung. Ein steuerlicher Umsatzsteuerausweis ist nicht konfiguriert.</p>
    <script src="/static/print.js"></script></body></html>'''.encode()


class Handler(BaseHTTPRequestHandler):
    server_version = 'LocalCRM/1.0'

    def log_message(self,fmt,*args):
        # Do not write customer-bearing URLs or request bodies to console.
        pass

    def send(self,status,data,kind='application/json; charset=utf-8',headers=None):
        if isinstance(data,(dict,list)):
            data = json.dumps(data,ensure_ascii=False).encode()
        elif isinstance(data,str):
            data = data.encode()
        self.send_response(status)
        self.send_header('Content-Type',kind)
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','DENY')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        for k,v in (headers or {}).items():
            self.send_header(k,v)
        self.end_headers()
        self.wfile.write(data)

    def local_request(self,write=False):
        expected = {'127.0.0.1:'+str(self.server.server_port),'localhost:'+str(self.server.server_port)}
        if self.headers.get('Host') not in expected:
            raise Problem('Nur lokaler Zugriff ist erlaubt.',403)
        if write and self.headers.get('Origin') != 'http://'+self.headers.get('Host',''):
            raise Problem('Ungültiger Anfrage-Ursprung.',403)
        if write and self.headers.get('Content-Type','').split(';')[0] != 'application/json':
            raise Problem('JSON-Anfrage erforderlich.',415)

    def token(self):
        try:
            cookie = SimpleCookie(self.headers.get('Cookie',''))
            return cookie['crm_session'].value if 'crm_session' in cookie else ''
        except Exception:
            return ''

    def session(self,write=False):
        token = self.token()
        with AUTH_LOCK:
            session = SESSIONS.get(token)
            if not session or session['until'] < time.time():
                SESSIONS.pop(token,None)
                raise Problem('Bitte anmelden.',401)
            if write and not hmac.compare_digest(self.headers.get('X-CSRF-Token',''),session['csrf']):
                raise Problem('Bitte die Seite neu laden.',403)
            session['until'] = time.time()+3600
            return session.copy()

    def body(self):
        length = integer(self.headers.get('Content-Length','0'),'Anfragelänge')
        if length > 29_000_000:
            raise Problem('Datei zu groß. Maximal 20 MB je Dokument.',413)
        try:
            value = json.loads(self.rfile.read(length))
        except (ValueError,UnicodeError):
            raise Problem('Ungültige Anfrage.')
        if not isinstance(value,dict):
            raise Problem('Ungültige Anfrage.')
        return value

    def do_GET(self):
        try:
            self.local_request()
            path = urlparse(self.path).path
            if path in ('/','/static/app.js','/static/kfz.js','/static/claims.js','/static/imports.js','/static/style.css','/static/kfz.css','/static/print.css','/static/print.js'):
                name = 'index.html' if path=='/' else path.split('/')[-1]
                mime = 'text/html' if name.endswith('html') else 'text/css' if name.endswith('css') else 'text/javascript'
                return self.send(200,(ROOT/'static'/name).read_bytes(),mime+'; charset=utf-8')
            with connect() as c:
                if path == '/api/session':
                    try:
                        session = self.session()
                    except Problem:
                        session = None
                    return self.send(200,{'setup_required':not bool(setting(c,'password_hash')),
                              'authenticated':bool(session),'csrf':session['csrf'] if session else ''})
                self.session()
                if path == '/api/state':
                    return self.send(200,state(c))
                if re.fullmatch(r'/api/statements/\d+',path):
                    return self.send(200,statement(c,path.split('/')[-1]),'text/html; charset=utf-8')
                if re.fullmatch(r'/api/customer-overviews/\d+',path):
                    return self.send(200,customer_overview(c,path.split('/')[-1]),'text/html; charset=utf-8')
                if re.fullmatch(r'/api/documents/\d+',path):
                    doc = get(c,'documents',path.split('/')[-1])
                    f = DATA/'Dokumente'/doc['storage_name']
                    if not f.exists():
                        raise Problem('Dokumentdatei fehlt. Bitte Sicherung prüfen.',404)
                    from urllib.parse import quote
                    return self.send(200,f.read_bytes(),'application/octet-stream',
                             {'Content-Disposition':"attachment; filename*=UTF-8''"+quote(doc['filename'])})
                if re.fullmatch(r'/api/export/(customers|contracts|referrers|commissions|claims)',path):
                    table = path.split('/')[-1]
                    rows = [dict(r) for r in c.execute(f'SELECT * FROM {table} ORDER BY id')]
                    stream = io.StringIO()
                    if rows:
                        writer = csv.writer(stream,delimiter=';')
                        writer.writerow(rows[0].keys())
                        for r in rows:
                            writer.writerow([("'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@','\t','\r')) else v) for v in r.values()])
                    return self.send(200,('\ufeff'+stream.getvalue()).encode(),'text/csv; charset=utf-8',{'Content-Disposition':f'attachment; filename="{table}.csv"'})
            raise Problem('Seite nicht gefunden.',404)
        except Problem as e:
            self.send(e.status,{'error':e.message})
        except Exception:
            traceback.print_exc()
            self.send(500,{'error':'Die Anfrage konnte nicht verarbeitet werden. Bitte Datenverzeichnis und freien Speicher prüfen.'})

    def do_POST(self):
        try:
            self.local_request(True)
            d = self.body()
            path = urlparse(self.path).path
            with connect() as c:
                if path in ('/api/setup','/api/login'):
                    with AUTH_LOCK:
                        ATTEMPTS[:] = [t for t in ATTEMPTS if time.time()-t<300]
                        if len(ATTEMPTS)>=10:
                            raise Problem('Zu viele Anmeldeversuche. Bitte in fünf Minuten erneut versuchen.',429)
                        ATTEMPTS.append(time.time())
                        c.execute('BEGIN IMMEDIATE')
                        if path == '/api/setup':
                            if setting(c,'password_hash'):
                                raise Problem('Das CRM ist bereits eingerichtet.',409)
                            hashed = password_hash(d.get('password'))
                            agency = txt(d,'agency_name',True,200)
                            c.execute("INSERT INTO settings VALUES('password_hash',?)",(hashed,))
                            c.execute("UPDATE settings SET value=? WHERE key='agency_name'",(agency,))
                            audit(c,'Ersteinrichtung','settings')
                        elif not verify_password(c,d.get('password','')):
                            raise Problem('Das Passwort stimmt nicht.',401)
                        c.commit()
                        ATTEMPTS.clear()
                        token,csrf = secrets.token_urlsafe(32),secrets.token_urlsafe(32)
                        SESSIONS[token]={'csrf':csrf,'until':time.time()+3600}
                    return self.send(200,{'csrf':csrf},headers={'Set-Cookie':f'crm_session={token}; HttpOnly; SameSite=Strict; Path=/'})
                self.session(True)
                if path == '/api/logout':
                    with AUTH_LOCK:
                        SESSIONS.pop(self.token(),None)
                    return self.send(200,{'ok':True},headers={'Set-Cookie':'crm_session=; Max-Age=0; HttpOnly; SameSite=Strict; Path=/'})
                if path == '/api/backup':
                    f = backup(c)
                    return self.send(200,f.read_bytes(),'application/zip',{'Content-Disposition':f'attachment; filename="{f.name}"'})
                if path == '/api/imports/analyze':
                    return self.send(200, analyze_import(c, d))
                if path == '/api/contracts/preview':
                    return self.send(200, premium_preview(d))
                c.execute('BEGIN IMMEDIATE')
                if path in ('/api/customers','/api/customer_relationships','/api/contracts','/api/referrers'):
                    result = save_record(c,path.split('/')[-1],d)
                    if path == '/api/customers' and not d.get('id') and d.get('related_customer_id'):
                        relation = save_record(c, 'customer_relationships', {
                            'customer_id': result['id'],
                            'related_customer_id': d['related_customer_id'],
                            'relationship': d.get('relationship', ''),
                            'reciprocal_relationship': d.get('reciprocal_relationship', ''),
                            'notes': d.get('relationship_notes', '')})
                        result['relationship_id'] = relation['id']
                elif path == '/api/claims':
                    result = save_claim(c, d)
                elif path == '/api/claim-emails':
                    result = save_claim_email(c, d)
                elif path == '/api/commissions':
                    result = book_commission(c,d)
                elif path == '/api/commissions/reverse':
                    result = reverse_commission(c,d)
                elif path == '/api/settlements':
                    result = settle(c,d)
                elif path == '/api/settlements/pay':
                    s = get(c,'settlements',d.get('id'))
                    if s['paid_at']:
                        raise Problem('Diese Abrechnung wurde bereits erledigt.',409)
                    paid = iso(d.get('paid_at'))
                    if paid > date.today().isoformat():
                        raise Problem('Das Zahlungsdatum darf nicht in der Zukunft liegen.')
                    reference = txt(d,'payment_reference',True,200)
                    c.execute('UPDATE settlements SET paid_at=?,payment_reference=? WHERE id=?',(paid,reference,s['id']))
                    audit(c,'Zahlung / Ausgleich erfasst','settlements',s['id'],reference)
                    result = {'ok':True}
                elif path == '/api/settings':
                    for k in ('agency_name','agency_address'):
                        c.execute('UPDATE settings SET value=? WHERE key=?',(txt(d,k,k=='agency_name'),k))
                    if d.get('new_password'):
                        if not verify_password(c,d.get('current_password','')):
                            raise Problem('Das bisherige Passwort stimmt nicht.',401)
                        c.execute("UPDATE settings SET value=? WHERE key='password_hash'",(password_hash(d['new_password']),))
                        current_token = self.token()
                        with AUTH_LOCK:
                            for t in list(SESSIONS):
                                if t != current_token:
                                    del SESSIONS[t]
                    audit(c,'Einstellungen geändert','settings')
                    result = {'ok':True}
                elif path == '/api/documents':
                    claim = get(c,'claims',d['claim_id']) if d.get('claim_id') else None
                    cust = get(c,'customers',claim['customer_id'] if claim else d.get('customer_id'))
                    contract_id = claim['contract_id'] if claim else d.get('contract_id')
                    contract = get(c,'contracts',contract_id) if contract_id else None
                    if contract and contract['customer_id'] != cust['id']:
                        raise Problem('Dieser Vertrag gehört zu einem anderen Kunden.')
                    filename, raw = uploaded_file(d)
                    storage = secrets.token_hex(24)
                    f = DATA/'Dokumente'/storage
                    f.write_bytes(raw)
                    try:
                        cur = c.execute('''INSERT INTO documents(customer_id,contract_id,filename,storage_name,size,created_at,claim_id)
                                           VALUES(?,?,?,?,?,?,?)''',
                            (cust['id'],contract['id'] if contract else None,filename,storage,len(raw),now(),
                             claim['id'] if claim else None))
                        audit(c,'Dokument abgelegt','documents',cur.lastrowid)
                        c.commit()
                    except Exception:
                        f.unlink(missing_ok=True)
                        raise
                    result = {'id':cur.lastrowid}
                elif path == '/api/documents/rename':
                    result = rename_document(c, d)
                elif path == '/api/imports/commit':
                    result = commit_import(c, d)
                elif path == '/api/records/delete':
                    result = delete_record(c, d.get('table'), d.get('id'))
                else:
                    raise Problem('Funktion nicht gefunden.',404)
                c.commit()
                if path == '/api/records/delete':
                    failed_files = []
                    for storage in result.pop('files'):
                        try:
                            (DATA / 'Dokumente' / storage).unlink(missing_ok=True)
                        except OSError:
                            failed_files.append(storage)
                    if failed_files:
                        result['warning'] = ('Der Datensatz wurde gelöscht, aber ' +
                            str(len(failed_files)) + ' gespeicherte Datei(en) konnten nicht entfernt werden. ' +
                            'Bitte die Dateiberechtigungen im Datenverzeichnis prüfen.')
                return self.send(200,result)
        except Problem as e:
            self.send(e.status,{'error':e.message})
        except sqlite3.IntegrityError:
            self.send(409,{'error':'Polizzennummer oder Buchungsreferenz existiert bereits, oder die Zuordnung ist ungültig.'})
        except Exception:
            traceback.print_exc()
            self.send(500,{'error':'Speichern fehlgeschlagen. Bitte freien Speicher und Schreibrechte prüfen; Eingaben bleiben im Formular.'})


def main():
    parser=argparse.ArgumentParser(description='Lokales Versicherungsagentur CRM')
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--no-browser',action='store_true')
    args=parser.parse_args()
    initialize()
    try:
        server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    except OSError:
        print('Port belegt. Läuft das CRM bereits? Öffne http://127.0.0.1:'+str(args.port))
        sys.exit(1)
    # One daily automatic backup at startup, never silently delete backups.
    with connect() as c:
        if setting(c,'password_hash'):
            today=datetime.now().strftime('%Y%m%d')
            if not list((DATA/'Sicherungen').glob('CRM-'+today+'-*.zip')):
                try:
                    backup(c,True)
                except OSError as exc:
                    print('Automatische Sicherung fehlgeschlagen:',exc)
    url='http://127.0.0.1:'+str(args.port)
    print('\nVersicherungsagentur CRM\n'+url+'\nDaten: '+str(DATA)+'\nBeenden mit Strg+C.\n')
    if not args.no_browser:
        threading.Timer(0.7,lambda:webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

if __name__=='__main__':
    main()
