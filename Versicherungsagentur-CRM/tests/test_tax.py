import sqlite3
import tempfile
import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app

class TaxTests(unittest.TestCase):
    def calculate(self, **values):
        return app.premium_fields(dict(premium='100', line='Haushalt', tax_mode='auto', premium_basis='net', **values))

    def test_rates_and_rounding(self):
        for line, cents in [('Haushalt',1100),('Unfallversicherung',1100),('Krankenversicherung',100),('Leben',400),('Pensionskasse',250)]:
            r=app.premium_fields(dict(premium='100',line=line,tax_mode='auto',premium_basis='net'))
            self.assertEqual(r['tax_cents'],cents)
            self.assertEqual(r['premium_cents'],10000+cents)
        self.assertEqual(app.premium_fields(dict(premium='111',line='Haushalt',tax_mode='auto'))['tax_cents'],1100)
        self.assertEqual(app.premium_fields(dict(premium='0.05',line='Haushalt',tax_mode='auto',premium_basis='net'))['tax_cents'],1)
        self.assertEqual(app.premium_fields(dict(premium='100',line='Leben',tax_mode='auto',life_tax_case='single_short'))['tax_rate_bp'],1100)

    def test_kfz_components_and_motor_tariffs(self):
        casco = app.premium_fields(dict(premium='1000', line='Vollkasko', tax_mode='auto',
                                        premium_basis='net', frequency='1', kfz_coverage='casco',
                                        motor_mode='none', fire_mode='auto', fire_rate='0.01'))
        self.assertEqual((casco['insurance_tax_cents'], casco['fire_tax_cents'], casco['motor_tax_cents']),
                         (11000, 10, 0))
        self.assertEqual(casco['premium_cents'] - casco['tax_cents'], 100000)
        casco_gross = app.premium_fields(dict(premium='1110.10', line='Kasko', tax_mode='auto',
                                              premium_basis='gross', frequency='1',
                                              kfz_coverage='casco', motor_mode='none',
                                              fire_mode='auto', fire_rate='0.01'))
        self.assertEqual(casco_gross['tax_cents'], 11010)
        self.assertEqual(casco_gross['premium_cents'] - casco_gross['tax_cents'], 100000)
        combined = app.premium_fields(dict(premium='1000', line='Kfz', tax_mode='auto',
                                           premium_basis='net', frequency='1', kfz_coverage='combined',
                                           casco_net='400', fire_rate='0.1', fire_mode='auto',
                                           motor_mode='auto', first_registration='2026-01-15',
                                           vehicle_class='M1', drive_type='combustion', power_kw='140',
                                           co2_g_km='180', wltp='yes'))
        self.assertEqual((combined['insurance_tax_cents'], combined['fire_tax_cents'], combined['motor_tax_cents']),
                         (11000, 40, 141696))
        self.assertEqual(combined['premium_cents'] - combined['tax_cents'], 100000)
        electric = app.motor_tax_monthly(dict(first_registration='2026-02-01', drive_type='electric',
                                              power_kw='60', weight_kg='1450'))
        self.assertEqual(electric, app.Decimal('12.75'))
        old = app.premium_fields(dict(premium='100', line='Kfz-Haftpflicht', tax_mode='auto',
                                      premium_basis='net', frequency='12', kfz_coverage='liability',
                                      motor_mode='auto', first_registration='2019-01-01',
                                      vehicle_class='M1', drive_type='combustion', power_kw='100'))
        self.assertEqual(old['motor_tax_cents'], 5227)  # (66*.62 + 10*.66) * 1.10
        with self.assertRaises(app.Problem):
            app.premium_fields(dict(premium='100', line='Kfz', tax_mode='auto', premium_basis='net'))

    def test_light_and_heavy_trucks_use_different_taxes(self):
        light = app.premium_fields(dict(premium='1000', line='Lkw-Haftpflicht',
            tax_mode='auto', premium_basis='net', frequency='12', kfz_coverage='liability',
            vehicle_class='N1', gross_weight_kg='3500', motor_mode='auto',
            first_registration='2026-10-15', drive_type='combustion', power_kw='120'))
        self.assertEqual(light['motor_tax_cents'], 6480)
        self.assertIsNone(light['vehicle_tax_cents'])
        self.assertEqual(light['premium_cents']-light['tax_cents'], 100000)
        electric = app.motor_tax_monthly(dict(vehicle_class='N1', gross_weight_kg='3000',
            first_registration='2026-10-15', drive_type='electric', power_kw='90'))
        self.assertEqual(electric, app.Decimal('48.50'))
        heavy = app.premium_fields(dict(premium='1000', line='Lkw-Haftpflicht',
            tax_mode='auto', premium_basis='net', frequency='1', kfz_coverage='liability',
            vehicle_class='N2', gross_weight_kg='12000', motor_mode='none',
            drive_type='combustion'))
        self.assertEqual(heavy['motor_tax_cents'], 0)
        self.assertEqual(heavy['vehicle_tax_cents'], 1860)
        self.assertEqual(heavy['tax_cents'], 11000)
        self.assertEqual(heavy['premium_cents']-heavy['tax_cents'], 100000)
        heavy_electric = app.premium_fields(dict(premium='1000', line='Lkw-Haftpflicht',
            tax_mode='auto', premium_basis='net', frequency='1', kfz_coverage='liability',
            vehicle_class='N3', gross_weight_kg='18000', motor_mode='none',
            drive_type='electric'))
        self.assertEqual(heavy_electric['vehicle_tax_cents'], 0)
        self.assertEqual(heavy_electric['motor_tax_cents'], 0)
        self.assertEqual(app.truck_tax_monthly('12100'), app.Decimal('22.10'))
        self.assertEqual(app.truck_tax_monthly('45000'), app.Decimal('80'))
        with self.assertRaises(app.Problem):
            app.premium_fields(dict(premium='1000', line='Lkw', tax_mode='auto',
                premium_basis='net', frequency='1', kfz_coverage='liability',
                vehicle_class='N2', gross_weight_kg='12000', motor_mode='manual',
                motor_amount='10', drive_type='combustion'))
        with self.assertRaises(app.Problem):
            app.motor_tax_monthly(dict(vehicle_class='N1', gross_weight_kg='4000',
                first_registration='2026-10-15', drive_type='combustion', power_kw='120'))

    def test_kfz_preview_matches_saved_components(self):
        payload = dict(customer_id=1, insurer='Test', policy_number='LKW-KASKO',
                       line='Lkw-Kasko', status='Aktiv', start_date='2026-01-01',
                       premium='1000', premium_basis='net', frequency='4',
                       tax_mode='auto', kfz_coverage='combined', vehicle_class='N2',
                       gross_weight_kg='12000', drive_type='combustion', motor_mode='none',
                       fire_mode='auto', fire_rate='0.1', casco_net='400', rate='0')
        preview = app.premium_preview(payload)
        self.assertEqual(preview['insurance_tax_cents'], 11000)
        self.assertEqual(preview['fire_tax_cents'], 40)
        self.assertEqual(preview['motor_tax_cents'], 0)
        self.assertEqual(preview['vehicle_tax_cents'], 1860)
        self.assertEqual(preview['net_cents'], 100000)
        self.assertEqual(preview['annual_tax_cents'], 44160)
        original_data, original_db = app.DATA, app.DB
        try:
            with tempfile.TemporaryDirectory() as tmp:
                app.DATA = Path(tmp); app.DB = app.DATA / 'crm.sqlite3'
                app.initialize()
                with app.connect() as c:
                    c.execute("INSERT INTO customers(first_name,last_name,created_at) VALUES('Test','Person','2026-01-01')")
                    saved = app.save_record(c, 'contracts', payload)
                    record = app.get(c, 'contracts', saved['id'])
                    for key in ('premium_cents', 'tax_cents', 'insurance_tax_cents',
                                'fire_tax_cents', 'motor_tax_cents', 'vehicle_tax_cents'):
                        self.assertEqual(record[key], preview[key])
                    gross_payload = dict(payload, policy_number='LKW-KASKO-BRUTTO',
                                         casco_premium_basis='gross', casco_premium='444.40')
                    gross_payload.pop('casco_net')
                    gross_preview = app.premium_preview(gross_payload)
                    gross_saved = app.save_record(c, 'contracts', gross_payload)
                    gross_record = app.get(c, 'contracts', gross_saved['id'])
                    self.assertEqual(gross_record['casco_premium_basis'], 'gross')
                    self.assertEqual(gross_record['casco_premium_cents'], 44440)
                    self.assertEqual(gross_record['casco_net_cents'], 40000)
                    self.assertEqual(gross_record['fire_tax_cents'], gross_preview['fire_tax_cents'])
        finally:
            app.DATA, app.DB = original_data, original_db

    def test_combined_kasko_gross_input_and_validation(self):
        base = dict(premium='1000', premium_basis='net', frequency='1',
                    line='Kfz', tax_mode='auto', kfz_coverage='combined',
                    vehicle_class='N2', gross_weight_kg='12000', drive_type='combustion',
                    motor_mode='none', fire_mode='auto', fire_rate='0.01')
        net = app.premium_preview(dict(base, casco_premium_basis='net', casco_premium='400'))
        gross = app.premium_preview(dict(base, casco_premium_basis='gross', casco_premium='444.04'))
        self.assertEqual(gross['casco_net_cents'], 40000)
        self.assertEqual(gross['casco_premium_cents'], 44404)
        self.assertEqual(gross['fire_tax_cents'], net['fire_tax_cents'])
        self.assertEqual(gross['insurance_tax_cents'], net['insurance_tax_cents'])
        self.assertEqual(gross['premium_cents'], net['premium_cents'])
        with self.assertRaises(app.Problem):
            app.premium_fields(dict(base, casco_premium_basis='gross', casco_premium='1200'))
        with self.assertRaises(app.Problem):
            app.premium_fields(dict(base, casco_premium_basis='gross'))

    def test_manual_and_validation(self):
        r=app.premium_fields(dict(premium='100',tax_mode='manual_rate',tax_rate='2,50',premium_basis='net'))
        self.assertEqual(r['premium_cents'],10250)
        for amount in ['0','12.34']:
            r=app.premium_fields(dict(premium='100',tax_mode='manual_amount',tax_amount=amount))
            self.assertEqual(r['premium_cents'],10000)
            self.assertEqual(r['tax_cents'],app.money(amount))
        for d in [dict(tax_mode='auto',line='Unbekannt'),dict(tax_mode='auto',line='Hagel'),dict(tax_mode='manual_rate',tax_rate='101'),dict(tax_mode='manual_amount',tax_amount='101'),dict(tax_mode='manual_amount',tax_amount='-1')]:
            with self.assertRaises(app.Problem):app.premium_fields(dict(premium='100',**d))

    def test_migration_and_persistence(self):
        original_data,original_db=app.DATA,app.DB
        try:
            with tempfile.TemporaryDirectory() as tmp:
                app.DATA=Path(tmp);app.DB=app.DATA/'crm.sqlite3'
                with app.connect() as c:
                    c.executescript(app.SCHEMA)
                    c.execute("INSERT INTO customers(first_name,last_name,created_at) VALUES('Test','Person','2026-01-01')")
                    c.execute("INSERT INTO contracts(customer_id,insurer,policy_number,line,status,start_date,premium_cents,frequency,rate_bp,commission_basis,created_at) VALUES(1,'Test','ALT','Haushalt','Aktiv','2026-01-01',12345,12,0,'agency','2026-01-01')")
                app.initialize();app.initialize()
                with app.connect() as c:
                    old=app.get(c,'contracts',1)
                    self.assertEqual(old['premium_cents'],12345)
                    self.assertIsNone(old['tax_cents'])
                    self.assertEqual(old['tax_mode'],'legacy')
                    payload=dict(customer_id=1,insurer='Test',policy_number='NEU',line='Kranken',status='Aktiv',start_date='2026-01-01',premium='100',frequency=12,tax_mode='auto',premium_basis='net')
                    saved=app.save_record(c,'contracts',payload)
                    record=app.get(c,'contracts',saved['id'])
                    self.assertEqual(record['premium_cents'],10100)
                    self.assertEqual(record['tax_cents'],100)
                    rid=app.save_record(c,'referrers',dict(name='Tippgeber',rate='15'))['id']
                    c.execute("UPDATE contracts SET referrer_id=?, rate_bp=1500, commission_basis='premium'", (rid,))
                    booking=dict(contract_id=saved['id'],booking_date='2026-09-10',kind='Folge',base='999999',reference='NET-PAYMENT')
                    commission=app.book_commission(c,booking)
                    self.assertEqual(commission['amount_cents'],1500)
                    row=app.get(c,'commissions',commission['id'])
                    self.assertEqual(row['base_cents'],10000)
                    annual=app.book_commission(c,dict(booking,premium_period='year',reference='NET-YEAR'))
                    self.assertEqual(annual['amount_cents'],18000)
                    with self.assertRaises(app.Problem):
                        app.book_commission(c,dict(booking,contract_id=1,reference='LEGACY'))
                    with self.assertRaises(app.Problem):
                        app.book_commission(c,dict(booking,premium_period='invalid',reference='INVALID'))
                    c.execute("UPDATE contracts SET tax_mode='manual_amount',tax_cents=1234 WHERE id=?",(saved['id'],))
                    manual=app.book_commission(c,dict(booking,reference='MANUAL-TAX'))
                    self.assertEqual(manual['amount_cents'],1330)
                    self.assertEqual(app.get(c,'commissions',commission['id'])['base_cents'],10000)
                    c.execute("UPDATE contracts SET tax_cents=0 WHERE id=?",(saved['id'],))
                    zero=app.book_commission(c,dict(booking,reference='ZERO-TAX'))
                    self.assertEqual(zero['amount_cents'],1515)
                    kfz_payload=dict(customer_id=1,insurer='Test',policy_number='KFZ-NET',
                                     line='Kfz',status='Aktiv',start_date='2026-01-01',
                                     premium='1000',frequency='1',tax_mode='auto',premium_basis='net',
                                     kfz_coverage='combined',casco_net='400',fire_mode='auto',
                                     fire_rate='0.01',motor_mode='manual',motor_amount='100',
                                     referrer_id=rid,rate='15',commission_basis='premium')
                    kfz_id=app.save_record(c,'contracts',kfz_payload)['id']
                    kfz=app.get(c,'contracts',kfz_id)
                    self.assertEqual(kfz['tax_cents'],21004)
                    self.assertEqual(kfz['premium_cents']-kfz['tax_cents'],100000)
                    kfz_commission=app.book_commission(c,dict(contract_id=kfz_id,
                        booking_date='2026-09-10',kind='Folge',base='999999',reference='KFZ-COM'))
                    self.assertEqual(kfz_commission['amount_cents'],15000)
                    self.assertEqual(app.get(c,'commissions',kfz_commission['id'])['base_cents'],100000)
                    truck_payload=dict(kfz_payload,policy_number='N2-NET',line='Lkw-Haftpflicht',
                        kfz_coverage='liability',vehicle_class='N2',gross_weight_kg='12000',
                        drive_type='combustion',motor_mode='none')
                    truck_payload.pop('casco_net')
                    truck_id=app.save_record(c,'contracts',truck_payload)['id']
                    truck=app.get(c,'contracts',truck_id)
                    self.assertEqual(truck['vehicle_tax_cents'],1860)
                    self.assertEqual(truck['tax_cents'],11000)
                    truck_commission=app.book_commission(c,dict(contract_id=truck_id,
                        booking_date='2026-09-10',kind='Folge',base='999999',reference='N2-COM'))
                    self.assertEqual(truck_commission['amount_cents'],15000)
                    self.assertEqual(app.get(c,'commissions',truck_commission['id'])['base_cents'],100000)

        finally:app.DATA,app.DB=original_data,original_db
