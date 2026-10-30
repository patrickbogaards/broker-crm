import base64
import http.client
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app
import restore

class CRMTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        app.DATA=Path(cls.tmp.name)
        app.DB=app.DATA/'crm.sqlite3'
        app.initialize()
        cls.server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        cls.port=cls.server.server_port
        cls.cookie=''
        cls.csrf=''

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def req(self,path,data=None,csrf=True,origin=True,cookie=True,host=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.port)
        headers={}
        if cookie:
            headers['Cookie']=self.cookie
        if data is not None:
            headers['Content-Type']='application/json'
            if origin:
                headers['Origin']=f'http://127.0.0.1:{self.port}'
            if csrf:
                headers['X-CSRF-Token']=self.csrf
        if host:
            headers['Host']=host
        connection.request('GET' if data is None else 'POST',path,json.dumps(data) if data is not None else None,headers)
        res=connection.getresponse()
        raw=res.read()
        h=dict(res.getheaders())
        result=json.loads(raw) if 'application/json' in h.get('Content-Type','') else raw
        connection.close()
        return res.status,result,h

    def ok(self,path,data=None):
        status,result,headers=self.req(path,data)
        self.assertEqual(status,200,result)
        return result

    def test_01_access_and_setup(self):
        self.assertEqual(self.req('/api/state')[0],401)
        self.assertEqual(self.req('/api/session',host='evil.example')[0],403)
        self.assertEqual(self.req('/api/setup',{'password':'long-password-test','agency_name':'Test'},origin=False)[0],403)
        status,body,headers=self.req('/api/setup',{'password':'long-password-test','agency_name':'Testagentur'})
        self.assertEqual(status,200,body)
        type(self).cookie=headers['Set-Cookie'].split(';')[0]
        type(self).csrf=body['csrf']
        self.assertEqual(self.req('/api/setup',{'password':'long-password-test','agency_name':'Test'})[0],409)
        self.assertEqual(self.req('/api/customers',{},csrf=False)[0],403)
        self.assertEqual(self.req('/api/customers',{},origin=False)[0],403)
        self.assertIn('Content-Security-Policy',self.req('/')[2])

    def test_02_customer_contract_and_precision(self):
        c=self.ok('/api/customers',{'first_name':'Anna','last_name':'Muster','email':'anna@example.test','notes':'<script>alert(1)</script>'})
        r=self.ok('/api/referrers',{'name':'Max Beispiel','rate':'15','iban':'TEST'})
        d={'customer_id':c['id'],'insurer':'Beispielversicherung','policy_number':'POL-001','line':'Haushalt','status':'Aktiv',
            'start_date':'2026-01-01','end_date':'2027-01-01','notice_date':'2026-11-01','premium':'100,50','frequency':'12',
            'referrer_id':r['id'],'rate':'15','commission_basis':'agency'}
        v=self.ok('/api/contracts',d)
        type(self).customer_id=c['id'];type(self).referrer_id=r['id'];type(self).contract_id=v['id'];type(self).contract_data=d
        state=self.ok('/api/state')
        self.assertEqual(state['contracts'][0]['premium_cents']*12,120600)
        self.assertEqual(self.req('/api/contracts',d)[0],409)
        self.assertEqual(self.req('/api/contracts',{**d,'policy_number':'INVALID','end_date':'2025-01-01'})[0],400)
        self.assertEqual(self.req('/api/contracts',{**d,'policy_number':'INVALID','rate':'101'})[0],400)
        old=state['customers'][0]
        self.ok('/api/customers',{**old,'phone':'012345'})
        self.assertEqual(self.req('/api/customers',{**old,'phone':'different'})[0],409)
        for amount,bp,expected in [(5,1000,1),(200000,1500,30000),(999,1250,125),(1,5000,1)]:
            self.assertEqual(app.calculated(amount,bp),expected)
        for value in ('-1','1.234','NaN','1e3','100.000,00'):
            with self.assertRaises(app.Problem):
                app.money(value)

    def test_02b_customer_relationships_are_bidirectional_and_deletable(self):
        property_customer = self.ok('/api/customers', {
            'customer_type': 'legal', 'company': 'Wohnhaus Hauptplatz 1',
            'related_customer_id': self.customer_id,
            'relationship': 'Verwaltete Immobilie',
            'reciprocal_relationship': 'Hausverwaltung',
            'relationship_notes': 'Verwaltungsobjekt'})
        property_relation = next(r for r in self.ok('/api/state')['customer_relationships']
                                 if r['customer_id'] == property_customer['id'])
        self.assertEqual((property_relation['relationship'], property_relation['reciprocal_relationship']),
                         ('Verwaltete Immobilie', 'Hausverwaltung'))
        self.ok('/api/records/delete', {'table': 'customers', 'id': property_customer['id']})
        self.assertFalse(self.ok('/api/state')['customer_relationships'])

        daughter = self.ok('/api/customers', {'first_name': 'Lena', 'last_name': 'Muster'})['id']
        relation = self.ok('/api/customer_relationships', {
            'customer_id': self.customer_id, 'related_customer_id': daughter,
            'relationship': 'Elternteil', 'reciprocal_relationship': 'Tochter',
            'notes': 'Familienverbindung'})
        saved = next(r for r in self.ok('/api/state')['customer_relationships'] if r['id'] == relation['id'])
        self.assertEqual((saved['customer_id'], saved['related_customer_id']), (self.customer_id, daughter))
        self.assertEqual((saved['relationship'], saved['reciprocal_relationship']), ('Elternteil', 'Tochter'))
        self.assertEqual(self.req('/api/customer_relationships', {
            'customer_id': daughter, 'related_customer_id': self.customer_id,
            'relationship': 'Tochter', 'reciprocal_relationship': 'Elternteil'})[0], 409)
        self.assertEqual(self.req('/api/customer_relationships', {
            'customer_id': daughter, 'related_customer_id': daughter,
            'relationship': 'Geschwister', 'reciprocal_relationship': 'Geschwister'})[0], 400)
        self.ok('/api/customer_relationships', {**saved, 'relationship': 'Mutter',
                                                'reciprocal_relationship': 'Tochter'})
        updated = next(r for r in self.ok('/api/state')['customer_relationships'] if r['id'] == relation['id'])
        self.assertEqual(updated['relationship'], 'Mutter')
        self.ok('/api/records/delete', {'table': 'customer_relationships', 'id': relation['id']})
        self.assertFalse(self.ok('/api/state')['customer_relationships'])
        self.ok('/api/records/delete', {'table': 'customers', 'id': daughter})

        person = self.ok('/api/customers', {'first_name': 'Max', 'last_name': 'Leitung'})['id']
        company = self.ok('/api/customers', {'customer_type': 'legal', 'company': 'Beispiel GmbH'})['id']
        self.ok('/api/customer_relationships', {'customer_id': person, 'related_customer_id': company,
            'relationship': 'Geschäftsführer/in', 'reciprocal_relationship': 'Unternehmen'})
        self.ok('/api/records/delete', {'table': 'customers', 'id': person})
        self.assertFalse(self.ok('/api/state')['customer_relationships'])
        self.ok('/api/records/delete', {'table': 'customers', 'id': company})

    def test_03_ledger_and_immutable_settlement(self):
        d={'contract_id':self.contract_id,'booking_date':'2026-09-10','kind':'Abschluss','base':'2000','reference':'EINGANG-001'}
        m=self.ok('/api/commissions',d)
        self.assertEqual(m['amount_cents'],30000)
        self.assertEqual(self.req('/api/commissions',d)[0],409)
        self.assertEqual(self.req('/api/commissions',{**d,'base':'0','reference':'ZERO'})[0],400)
        sd={'referrer_id':self.referrer_id,'period_start':'2026-09-01','period_end':'2026-09-30'}
        s=self.ok('/api/settlements',sd)
        self.assertEqual(self.req('/api/settlements',sd)[0],400)
        before=self.ok('/api/statements/'+str(s['id']))
        self.assertIn('300,00 €'.encode(),before)
        self.ok('/api/contracts',{**self.contract_data,'id':self.contract_id,'version':1,'rate':'25'})
        after=self.ok('/api/statements/'+str(s['id']))
        self.assertEqual(before,after)
        self.ok('/api/commissions/reverse',{'id':m['id'],'reason':'Vertrag rückabgewickelt'})
        self.assertEqual(self.req('/api/commissions/reverse',{'id':m['id'],'reason':'Erneut'})[0],409)
        state=self.ok('/api/state')
        reverse=next(x for x in state['commissions'] if x['reverses_id'])
        self.assertEqual(reverse['amount_cents'],-30000)
        self.assertEqual(reverse['rate_bp'],1500)
        negative=self.ok('/api/settlements',{'referrer_id':self.referrer_id,'period_start':reverse['booking_date'],'period_end':reverse['booking_date']})
        self.assertEqual(self.ok('/api/state')['settlements'][0]['total_cents'],-30000)
        self.ok('/api/settlements/pay',{'id':s['id'],'paid_at':'2026-01-01','payment_reference':'Bankbeleg-123'})
        self.assertEqual(self.req('/api/settlements/pay',{'id':s['id'],'paid_at':'2026-01-01','payment_reference':'Doppelt'})[0],409)

    def test_04_fixed_void_and_archived(self):
        self.ok('/api/contracts',{**self.contract_data,'id':self.contract_id,'version':2,'commission_basis':'fixed','rate':'15'})
        d={'contract_id':self.contract_id,'booking_date':'2026-09-10','kind':'Folge','base':'12,34','reference':'FIX-001'}
        m=self.ok('/api/commissions',d)
        self.assertEqual(m['amount_cents'],1234)
        self.ok('/api/commissions/reverse',{'id':m['id'],'reason':'Fehler bei Erfassung'})
        self.assertEqual(next(x for x in self.ok('/api/state')['commissions'] if x['id']==m['id'])['voided'],1)
        r=self.ok('/api/state')['referrers'][0]
        self.ok('/api/referrers',{**r,'rate':'15','archived':True})
        self.assertEqual(self.req('/api/commissions',{**d,'reference':'ARCHIVED'})[0],400)

    def test_05_documents_backup_restore_export(self):
        overview=self.ok('/api/customer-overviews/'+str(self.customer_id)).decode()
        self.assertIn('Anna Muster',overview)
        self.assertIn('POL-001',overview)
        self.assertIn('Jahresbruttoprämie',overview)
        self.assertIn('Drucken / als PDF speichern',overview)
        self.assertNotIn('EINGANG-001',overview)
        doc={'customer_id':self.customer_id,'contract_id':self.contract_id,'filename':'Testpolizze.txt','data':base64.b64encode(b'contract document').decode()}
        result=self.ok('/api/documents',doc)
        self.assertEqual(self.ok('/api/documents/'+str(result['id'])),b'contract document')
        renamed=self.ok('/api/documents/rename',{'id':result['id'],'filename':'Polizze 2026 – geprüft.txt'})
        self.assertEqual(renamed['filename'],'Polizze 2026 – geprüft.txt')
        saved=next(x for x in self.ok('/api/state')['documents'] if x['id']==result['id'])
        self.assertEqual(saved['filename'],'Polizze 2026 – geprüft.txt')
        self.assertEqual(self.ok('/api/documents/'+str(result['id'])),b'contract document')
        self.assertEqual(self.req('/api/documents/rename',{'id':result['id'],'filename':'../outside.txt'})[0],400)
        self.assertEqual(self.req('/api/documents',{**doc,'filename':'../outside.txt'})[0],400)
        self.assertEqual(self.req('/api/documents',{**doc,'data':'invalid!'})[0],400)
        raw=self.ok('/api/backup',{})
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            self.assertIn('crm.sqlite3',z.namelist())
            docs=[x for x in z.namelist() if x.startswith('Dokumente/')]
            self.assertEqual(len(docs),1)
            self.assertEqual(z.read(docs[0]),b'contract document')
            with tempfile.TemporaryDirectory() as td:
                z.extractall(td)
                c=sqlite3.connect(Path(td)/'crm.sqlite3')
                self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0],'ok')
                self.assertEqual(c.execute('SELECT count(*) FROM settlements').fetchone()[0],2)
                self.assertEqual(c.execute('SELECT count(*) FROM documents').fetchone()[0],1)
                c.close()
        exported=self.ok('/api/export/contracts').decode('utf-8-sig')
        self.assertIn('premium_cents',exported)
        self.assertIn('10050',exported)

    def test_06_logout_login_and_persistence(self):
        self.ok('/api/logout',{})
        self.assertEqual(self.req('/api/state')[0],401)
        self.assertEqual(self.req('/api/login',{'password':'incorrect-password'})[0],401)
        status,result,headers=self.req('/api/login',{'password':'long-password-test'})
        self.assertEqual(status,200,result)
        type(self).cookie=headers['Set-Cookie'].split(';')[0];type(self).csrf=result['csrf']
        app.initialize()
        self.assertEqual(len(self.ok('/api/state')['customers']),1)
        self.assertEqual(len(self.ok('/api/state')['settlements']),2)

    def test_07_restore_utility(self):
        raw=self.ok('/api/backup',{})
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            source=root/'backup.zip'
            source.write_bytes(raw)
            target=root/'Daten'
            target.mkdir()
            (target/'existing.txt').write_text('retained')
            old=restore.restore(source,target)
            self.assertEqual((old/'existing.txt').read_text(),'retained')
            c=sqlite3.connect(target/'crm.sqlite3')
            self.assertEqual(c.execute('SELECT count(*) FROM customers').fetchone()[0],1)
            c.close()
            bad=root/'bad.zip'
            with zipfile.ZipFile(bad,'w') as z:
                z.writestr('../escaped.txt','bad')
            with self.assertRaises(ValueError):
                restore.restore(bad,target)
            self.assertFalse((root/'escaped.txt').exists())
            self.assertTrue((target/'crm.sqlite3').exists())

    def test_08_delete_relationships_and_files(self):
        self.assertEqual(self.req('/api/records/delete', {'table': 'unknown', 'id': 1})[0], 400)
        self.assertEqual(self.req('/api/records/delete', {'table': 'customers', 'id': self.customer_id})[0], 409)
        self.assertEqual(self.req('/api/records/delete', {'table': 'contracts', 'id': self.contract_id})[0], 409)
        customer = self.ok('/api/customers', {'customer_type': 'legal', 'company': 'Löschtest GmbH'})['id']
        claim_id = self.ok('/api/claims', {'customer_id': customer})['id']
        email_id = self.ok('/api/claim-emails', {
            'claim_id': claim_id, 'subject': 'Test-E-Mail'
        })['id']
        document_id = self.ok('/api/documents', {
            'claim_id': claim_id, 'filename': 'Schadenfoto.txt',
            'data': base64.b64encode(b'delete-me').decode()
        })['id']
        with app.connect() as c:
            storage = app.get(c, 'documents', document_id)['storage_name']
        target = app.DATA / 'Dokumente' / storage
        self.assertTrue(target.exists())
        self.assertEqual(self.req('/api/records/delete', {'table': 'customers', 'id': customer})[0], 409)
        self.ok('/api/records/delete', {'table': 'claims', 'id': claim_id})
        self.assertFalse(target.exists())
        self.assertEqual(self.req('/api/documents/' + str(document_id))[0], 404)
        self.assertFalse(any(x['id'] == email_id for x in self.ok('/api/state')['claim_emails']))
        self.ok('/api/records/delete', {'table': 'customers', 'id': customer})
        self.assertFalse(any(x['id'] == customer for x in self.ok('/api/state')['customers']))

    def test_09_delete_settlement_reopens_commissions(self):
        state = self.ok('/api/state')
        settlement = state['settlements'][0]
        linked = [m['id'] for m in state['commissions'] if m['settlement_id'] == settlement['id']]
        self.assertTrue(linked)
        self.assertEqual(self.req('/api/records/delete', {'table': 'commissions', 'id': linked[0]})[0], 409)
        self.ok('/api/records/delete', {'table': 'settlements', 'id': settlement['id']})
        state = self.ok('/api/state')
        self.assertFalse(any(s['id'] == settlement['id'] for s in state['settlements']))
        self.assertTrue(all(next(m for m in state['commissions'] if m['id'] == ident)['settlement_id'] is None
                            for ident in linked))

    def test_10_delete_individual_records(self):
        customer = self.ok('/api/customers', {'first_name': 'Lena', 'last_name': 'Test'})['id']
        doc = self.ok('/api/documents', {'customer_id': customer, 'filename': 'Notiz.txt',
                                        'data': base64.b64encode(b'document').decode()})['id']
        with app.connect() as c:
            storage = app.get(c, 'documents', doc)['storage_name']
        self.ok('/api/records/delete', {'table': 'documents', 'id': doc})
        self.assertFalse((app.DATA / 'Dokumente' / storage).exists())
        claim_id = self.ok('/api/claims', {'customer_id': customer})['id']
        email_id = self.ok('/api/claim-emails', {'claim_id': claim_id, 'subject': 'Einzeln löschen'})['id']
        self.ok('/api/records/delete', {'table': 'claim_emails', 'id': email_id})
        self.ok('/api/records/delete', {'table': 'claims', 'id': claim_id})
        referrer = self.ok('/api/referrers', {'name': 'Freier Tippgeber', 'rate': '5'})['id']
        contract = self.ok('/api/contracts', {'customer_id': customer, 'insurer': 'Test',
            'policy_number': 'DELETE-ONLY-1', 'line': 'Haushalt', 'status': 'Aktiv',
            'start_date': '2026-01-01', 'premium': '111', 'frequency': '12',
            'referrer_id': referrer, 'rate': '5'})['id']
        self.assertEqual(self.req('/api/records/delete', {'table': 'referrers', 'id': referrer})[0], 409)
        self.ok('/api/records/delete', {'table': 'contracts', 'id': contract})
        self.ok('/api/records/delete', {'table': 'referrers', 'id': referrer})
        self.ok('/api/records/delete', {'table': 'customers', 'id': customer})

    def test_11_delete_unsettled_commission_then_contract(self):
        customer = self.ok('/api/customers', {'first_name': 'Provision', 'last_name': 'Test'})['id']
        referrer = self.ok('/api/referrers', {'name': 'Provisionstest', 'rate': '10'})['id']
        contract = self.ok('/api/contracts', {'customer_id': customer, 'insurer': 'Test',
            'policy_number': 'DELETE-COMMISSION-1', 'line': 'Haushalt', 'status': 'Aktiv',
            'start_date': '2026-01-01', 'premium': '111', 'frequency': '12',
            'referrer_id': referrer, 'rate': '10'})['id']
        commission = self.ok('/api/commissions', {
            'contract_id': contract, 'booking_date': '2026-09-15', 'kind': 'Abschluss',
            'base': '100', 'reference': 'DELETE-COMMISSION-REF'
        })['id']
        self.assertEqual(self.req('/api/records/delete', {'table': 'contracts', 'id': contract})[0], 409)
        self.ok('/api/records/delete', {'table': 'commissions', 'id': commission})
        self.ok('/api/records/delete', {'table': 'contracts', 'id': contract})
        self.ok('/api/records/delete', {'table': 'referrers', 'id': referrer})
        self.ok('/api/records/delete', {'table': 'customers', 'id': customer})

if __name__=='__main__':
    unittest.main(verbosity=2)
