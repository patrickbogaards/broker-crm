/* Dedicated Kfz contract form. Its preview uses the same server calculation as saving. */
function editKfzContract(c={}, cid=''){
 if(!db.customers.length){notice('Lege zuerst einen Kunden an.');return;}
 const coverage=c.kfz_coverage||(/kasko/i.test(c.line||'')?'casco':/haftpflicht/i.test(c.line||'')?'liability':'');
 const vehicleClass=c.vehicle_class||'M1';
 const grossPremium=c.premium_cents==null?'':dec(c.premium_cents);
 const netPremium=c.premium_cents==null?'':dec(c.tax_cents==null?c.premium_cents:c.premium_cents-c.tax_cents);
 const premium=c.premium??(c.premium_basis==='net'?netPremium:grossPremium);
 const body=`<div class="kfz-intro">Wähle zuerst Deckung und Fahrzeugklasse. Danach erscheinen nur die Angaben, die für diese Steuern nötig sind.</div>
 <fieldset class="kfz-step"><legend>1 · Vertrag und Fahrzeug</legend>
 <div class="form-grid">${customerPicker(c.customer_id||cid)}${field('insurer','Versicherungsgesellschaft',c.insurer,'text',true)}${field('policy_number','Polizzennummer / vorläufige Nummer',c.policy_number,'text',true)}${field('line','Sparte / Vertragsbezeichnung',isKfzLine(c.line)?c.line:'Kfz','text',true)}${select('kfz_coverage','Versicherte Deckung',[['','Bitte wählen'],['liability','Nur Kfz-Haftpflicht'],['casco','Nur Kasko'],['combined','Haftpflicht und Kasko']],coverage,true)}${select('vehicle_class','Fahrzeugklasse laut Zulassung',[['M1','Pkw M1'],['N1','Leichter Lkw N1 bis 3,5 t'],['N2','Lkw N2 über 3,5 bis 12 t'],['N3','Schwerer Lkw N3 über 12 t'],['other','Andere Fahrzeugklasse']],vehicleClass,true)}${field('gross_weight_kg','Höchstes zulässiges Gesamtgewicht (kg)',c.gross_weight_kg)}${select('drive_type','Antriebsart',[['','Bitte wählen'],['combustion','Verbrenner / nicht aufladbarer Hybrid'],['phev','Plug-in-Hybrid'],['electric','Rein elektrisch']],c.drive_type||'')}${field('first_registration','Unionsweite Erstzulassung',c.first_registration,'date')}${field('power_kw','Leistung laut Zulassung (kW)',c.power_kw)}${select('wltp','CO₂-Messverfahren Pkw',[['yes','WLTP'],['no','Kein WLTP / NEFZ']],c.wltp||'yes')}${field('co2_g_km','CO₂ kombiniert / gewichtet kombiniert (g/km)',c.co2_g_km)}${field('weight_kg','Eigengewicht Elektro-Pkw (kg)',c.weight_kg)}</div>
 <div class="form-grid">${select('ownership','Vertragsherkunft',[['Eigen','Eigen – meine Agentur'],['Fremd','Fremd – anderer Vermittler']],c.ownership||'Eigen')}${select('status','Status',['Beantragt','Aktiv','Gekündigt','Storniert','Abgelaufen'].map(x=>[x,x]),c.status||'Aktiv')}${field('start_date','Versicherungsbeginn',c.start_date||today(),'date',true)}${field('end_date','Vertragsende',c.end_date,'date')}${field('notice_date','Letzter Tag für Kündigung',c.notice_date,'date')}</div></fieldset>
 <fieldset class="kfz-step"><legend>2 · Prämie je Zahlung</legend><div class="form-grid">${field('premium','Eingegebene Prämie (€)',premium,'text',true)}${select('premium_basis','Die eingegebene Prämie ist',[['gross','Brutto: alle Steuerbestandteile enthalten'],['net','Netto: Steuerbestandteile kommen hinzu']],c.premium_basis||'gross')}${select('frequency','Zahlungsrhythmus',[[12,'Monatlich'],[4,'Vierteljährlich'],[2,'Halbjährlich'],[1,'Jährlich']],c.frequency||12)}</div></fieldset>
 <fieldset class="kfz-step"><legend>3 · Steuern je Zahlung</legend><div class="form-grid">${select('tax_mode','Versicherungssteuer (11 %)',[['auto','Automatisch: 11 %'],['manual_rate','Manueller Satz (%)'],['manual_amount','Manueller Betrag (€)'],...(c.id?[['legacy','Bisheriger Betrag ohne Aufschlüsselung']]:[])],c.tax_mode||(c.id?'legacy':'auto'))}${field('tax_rate','Manueller Versicherungssteuersatz (%)',c.tax_rate_bp==null?'11,00':dec(c.tax_rate_bp))}${field('tax_amount','Manuelle Versicherungssteuer (€)',c.insurance_tax_cents==null?'':dec(c.insurance_tax_cents))}${select('fire_mode','Feuerschutzsteuer Kasko',[['auto','Automatisch nach Kasko-Nettoprämie'],['manual','Manueller Betrag laut Polizze']],c.fire_mode||'auto')}${field('fire_rate','Feuerschutzsteuersatz (%)',dec(c.fire_rate_bp??1))}${field('fire_amount','Manuelle Feuerschutzsteuer (€)',c.fire_tax_cents==null?'':dec(c.fire_tax_cents))}${select('casco_premium_basis','Eingegebene Kaskoprämie ist',[['net','Netto – ohne Steuern'],['gross','Brutto – inklusive Versicherungs- und Feuerschutzsteuer']],c.casco_premium_basis||'net')}${field('casco_premium','Kaskoprämie je Zahlung (€)',c.casco_premium_cents==null?(c.casco_net_cents==null?'':dec(c.casco_net_cents)):dec(c.casco_premium_cents))}${select('motor_mode','Motorbezogene Versicherungssteuer',[['auto','Automatisch aus Fahrzeugdaten'],['manual','Manueller Betrag laut Polizze'],['none','Keine']],c.motor_mode||'auto')}${field('motor_amount','Manuelle Motorsteuer (€)',c.motor_tax_cents==null?'':dec(c.motor_tax_cents))}${select('vehicle_tax_case','Separate Kraftfahrzeugsteuer schwerer Lkw',[['standard','Regeltarif nach Gewicht'],['exempt','Weitere Befreiung nachgewiesen']],c.vehicle_tax_case||'standard')}</div>
 <p class="hint">Die motorbezogene Steuer gehört zur Kfz-Haftpflicht. Schwere Lkw zahlen stattdessen eine separate Kraftfahrzeugsteuer. Bei einer kombinierten Polizze kannst du die Kaskoprämie netto oder brutto eingeben. Brutto enthält die 11-%-Versicherungssteuer und Feuerschutzsteuer, aber keine Motorsteuer. Für die automatische Feuerschutzsteuer wird die Nettobasis daraus ermittelt. Der vorgeschlagene Feuerschutzsteuersatz von 0,01 % muss mit der Polizze abgeglichen werden; Satz und Betrag sind änderbar.</p></fieldset>
 <div id="kfz-preview" class="kfz-preview" aria-live="polite">Prämie und Steuerangaben eingeben …</div>
 <fieldset class="kfz-step"><legend>4 · Tippgeber und Unterlagen</legend><div class="form-grid">${select('referrer_id','Tippgeber',[['','Kein Tippgeber'],...db.referrers.filter(r=>!r.archived||r.id===c.referrer_id).map(r=>[r.id,r.name])],c.referrer_id||'')}${select('commission_basis','Berechnungsbasis',[['agency','Eingegangene Agenturprovision'],['premium','Nettoprämie ohne alle Prämiensteuern'],['fixed','Fixbetrag je Buchung']],c.commission_basis||'agency')}${field('rate','Vereinbarter Anteil (%)',dec(c.rate_bp),'text',true)}${textarea('notes','Vertrags- und Provisionsvereinbarung',c.notes)}</div>
 <p class="hint">Neue Vereinbarungen gelten nur für künftige Provisionsbuchungen.</p>${c.id?`<h3>Vertragsdokumente</h3>${contractDocumentList(c.id)}`:''}
 <button type="submit" name="open_documents" value="1" class="secondary">Speichern & Dokumente anhängen</button></fieldset>`;
 modal(c.id?'Kfz-Vertrag bearbeiten':'Kfz-Vertrag anlegen',body,'contracts',c);
 dialog.classList.add('kfz-dialog');
 const f=document.querySelector('#editor');f.dataset.kfz='1';
 setupCustomerPicker(c.customer_id||cid);
 if(!c.id&&coverage!=='casco'&&!['N2','N3'].includes(vehicleClass))f.elements.motor_mode.value='auto';
 refreshKfzForm();
}

function kfzVisible(f,name,visible,required=false){
 const input=f.elements[name];if(!input)return;
 input.closest('label').hidden=!visible;
 input.required=visible&&required;
}

function refreshKfzForm(){
 const f=document.querySelector('#editor');if(!f||f.dataset.kfz!=='1')return;
 const e=f.elements,coverage=e.kfz_coverage.value,vehicleClass=e.vehicle_class.value,
       heavy=vehicleClass==='N2'||vehicleClass==='N3',casco=coverage==='casco',hasCasco=coverage==='casco'||coverage==='combined';
 if(f.dataset.lastVehicleClass&&f.dataset.lastVehicleClass!==vehicleClass){
  const weight=Number(e.gross_weight_kg.value);
  if((vehicleClass==='M1'||vehicleClass==='other'||vehicleClass==='N1')&&weight>3500||
     vehicleClass==='N2'&&(weight<=3500||weight>12000)||
     vehicleClass==='N3'&&weight<=12000)e.gross_weight_kg.value='';
 }
 f.dataset.lastVehicleClass=vehicleClass;
 if((casco||heavy)&&e.motor_mode.value!=='none')e.motor_mode.value='none';
 if(!casco&&!heavy&&e.motor_mode.value==='none')e.motor_mode.value=vehicleClass==='other'?'manual':'auto';
 if(vehicleClass==='other'&&!casco&&e.motor_mode.value==='auto')e.motor_mode.value='manual';
 const motorAuto=!casco&&!heavy&&e.motor_mode.value==='auto',motorManual=!casco&&!heavy&&e.motor_mode.value==='manual';
 const modernPkw=vehicleClass==='M1'&&e.first_registration.value>='2020-10-01';
 kfzVisible(f,'gross_weight_kg',true,['N1','N2','N3'].includes(vehicleClass));
 kfzVisible(f,'drive_type',heavy||motorAuto,heavy||motorAuto);
 kfzVisible(f,'first_registration',motorAuto,motorAuto);
 kfzVisible(f,'power_kw',motorAuto,motorAuto);
 kfzVisible(f,'wltp',motorAuto&&modernPkw&&e.drive_type.value!=='electric');
 kfzVisible(f,'co2_g_km',motorAuto&&modernPkw&&e.drive_type.value!=='electric'&&e.wltp.value==='yes',true);
 kfzVisible(f,'weight_kg',motorAuto&&vehicleClass==='M1'&&e.drive_type.value==='electric',true);
 kfzVisible(f,'tax_rate',e.tax_mode.value==='manual_rate',e.tax_mode.value==='manual_rate');
 kfzVisible(f,'tax_amount',e.tax_mode.value==='manual_amount',e.tax_mode.value==='manual_amount');
 kfzVisible(f,'fire_mode',hasCasco);
 kfzVisible(f,'fire_rate',hasCasco&&e.fire_mode.value==='auto',hasCasco&&e.fire_mode.value==='auto');
 kfzVisible(f,'fire_amount',hasCasco&&e.fire_mode.value==='manual',hasCasco&&e.fire_mode.value==='manual');
 kfzVisible(f,'casco_premium_basis',coverage==='combined');
 kfzVisible(f,'casco_premium',coverage==='combined',coverage==='combined'&&e.fire_mode.value==='auto');
 kfzVisible(f,'motor_mode',!casco&&!heavy);
 kfzVisible(f,'motor_amount',motorManual,motorManual);
 kfzVisible(f,'vehicle_tax_case',heavy);
 const preview=document.querySelector('#kfz-preview');
 e.tax_mode.setCustomValidity('');
 clearTimeout(refreshKfzForm.timer);
 const sequence=(refreshKfzForm.sequence||0)+1;refreshKfzForm.sequence=sequence;
 if(!coverage){preview.textContent='Bitte zuerst die versicherte Deckung auswählen.';return;}
 if(!e.premium.value.trim()){preview.textContent='Bitte die Prämie je Zahlung eintragen.';return;}
 refreshKfzForm.timer=setTimeout(async()=>{
  if(!f.isConnected||refreshKfzForm.sequence!==sequence)return;
  const values=Object.fromEntries(new FormData(f));
  try{
   const p=await api('contracts/preview',values);
   if(!f.isConnected||refreshKfzForm.sequence!==sequence)return;
   const rows=[...(coverage==='combined'&&p.casco_net_cents!=null?[['Kasko-Nettoprämie (berechnet)',p.casco_net_cents]]:[]),['Nettoprämie',p.net_cents],['Versicherungssteuer',p.insurance_tax_cents],['Feuerschutzsteuer',p.fire_tax_cents],['Motorbezogene Versicherungssteuer',p.motor_tax_cents],['Steuern auf der Prämie gesamt',p.tax_cents],['Bruttoprämie',p.premium_cents]];
   preview.innerHTML=`<div class="kfz-preview-head"><strong>Berechnung je Zahlung</strong><span>${freq(Number(e.frequency.value))}</span></div><dl>${rows.map(([label,cents])=>`<dt>${label}</dt><dd>${cents==null?'Nicht aufgeschlüsselt':euro(cents)}</dd>`).join('')}</dl><div class="kfz-preview-annual">Jährlich: Netto ${p.annual_net_cents==null?'—':euro(p.annual_net_cents)} · Steuern ${p.annual_tax_cents==null?'—':euro(p.annual_tax_cents)} · Brutto ${euro(p.annual_gross_cents)}</div>${p.vehicle_tax_cents==null?'':`<p>Separate Kraftfahrzeugsteuer des Zulassungsbesitzers: <strong>${euro(p.vehicle_tax_cents)} pro Monat</strong>. Sie gehört nicht zur Prämie und wird nicht von der Tippgeber-Nettoprämie abgezogen.</p>`}`;
   e.tax_mode.setCustomValidity('');
  }catch(err){if(!f.isConnected||refreshKfzForm.sequence!==sequence)return;preview.textContent=err.message;e.tax_mode.setCustomValidity(err.message);}
 },180);
}
