'use strict';
let pendingImport = null;

function importsView(){
 return pagehead('Intelligenter Import','Polizzen und E-Mails lokal auslesen, zuordnen und nach deiner Prüfung übernehmen.')+
 `<div class="import-grid"><section class="card padded"><h2>Datei oder E-Mail analysieren</h2><form id="import-analyze"><label class="file-drop import-drop"><strong>Datei oder E-Mail hierher ziehen</strong><span class="file-drop-name">Alternativ anklicken und PDF, Bild, EML oder TXT auswählen · maximal 20 MB</span><input type="file" name="file" accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff,.heic,.eml,.txt" required></label><div class="form-error" role="alert"></div><button class="primary">Datei analysieren</button></form><div class="mail-drop-help"><strong>Direkt aus Outlook oder Apple Mail</strong><p>Wenn das Mailprogramm eine EML-Datei oder lesbaren Nachrichtentext bereitstellt, wird die Nachricht direkt übernommen. Bei einer Outlook-MSG-Datei die Nachricht zuerst als EML speichern oder auf den Schreibtisch ziehen und anschließend hier ablegen.</p></div></section><section class="card padded"><h2>So funktioniert die Zuordnung</h2><ol class="steps"><li>Das CRM liest Text und Kontaktdaten direkt auf dem CRM-Server.</li><li>E-Mail, Telefon, Name, PLZ und Polizzennummer werden mit dem Bestand verglichen.</li><li>Du prüfst und änderst den Vorschlag vor dem Speichern.</li><li>Die Originaldatei landet in der Kunden- oder Vertragsakte.</li></ol><p class="hint">In der lokalen Mac-Version bleiben die Dateien auf diesem Mac. Schlechte Scans und ungewöhnliche Dokumentlayouts können eine manuelle Ergänzung erfordern.</p></section></div>`;
}

function importCustomerOptions(){return db.customers.filter(c=>!c.archived).map(c=>[c.id,full(c)+(c.customer_type!=='legal'&&c.company?' · '+c.company:'')+' · K-'+String(c.id).padStart(5,'0')]);}

function updateImportCustomerType(){
 const form=document.querySelector('#editor');if(!form||form.dataset.kind!=='imports/commit')return;
 const legal=form.elements.customer_type.value==='legal',creating=form.elements.customer_action.value==='new'&&form.elements.contract_action.value!=='existing';
 form.elements.company.required=creating&&legal;
 form.elements.first_name.required=creating&&!legal;
 form.elements.last_name.required=creating&&!legal;
 form.elements.birthday.closest('label').hidden=legal;
 const hint=form.querySelector('#import-customer-type-hint');
 if(hint)hint.textContent=legal?'Als juristische Person erkannt: Firmenname prüfen; eine Kontaktperson ist optional.':'Als natürliche Person erkannt: Vor- und Nachname prüfen.';
}

function showImportReview(result){
 const cm=result.matches.customers[0],vm=result.matches.contracts[0];
 const customerId=vm?.customer_id||cm?.id||'',customerAction=(vm||cm?.score>=60)?'existing':'new';
 const hasPolicy=Boolean(result.contract.policy_number||result.kind==='policy'),contractAction=vm?'existing':hasPolicy?'new':'none';
 const c=result.customer,v=result.contract,customerMatches=result.matches.customers.map(x=>`${x.label}: ${x.reason}`).join(' · '),matchText=[vm?`Vertrag: ${vm.label} (${vm.reason})`:'',customerMatches?`Mögliche Kunden: ${customerMatches}`:''].filter(Boolean).join(' · ');
 modal('Import prüfen',`<div class="import-confidence"><strong>${vm?'Polizzennummer bereits vorhanden':matchText?'Neuer Vertrag mit möglicher Kundenzuordnung':'Neuer Vertrag ohne eindeutigen Kundentreffer'}</strong><span>${esc(matchText||'Die Vertragsdaten werden zuerst geprüft. Anschließend wird der Vertrag einem Kunden zugeordnet.')}</span></div>
 <h3>1. Vertrag prüfen</h3><div class="form-grid">${select('contract_action','Vertragsaktion',[['existing','Datei beim bestehenden Vertrag ablegen'],['new','Neuen Vertrag anlegen'],['none','Nur in einer Kundenakte ablegen']],contractAction,true)}${select('contract_id','Bestehender Vertrag',[['','Bitte auswählen'],...db.contracts.map(x=>[x.id,x.policy_number+' · '+x.insurer+' · '+full(customer(x.customer_id))])],vm?.id||'')}${field('insurer','Versicherer',v.insurer)}${field('policy_number','Polizzennummer',v.policy_number)}${field('line','Sparte',v.line)}${select('ownership','Herkunft',[['Eigen','Eigen'],['Fremd','Fremd']],v.ownership||'Eigen')}${select('status','Status',['Beantragt','Aktiv','Gekündigt','Storniert','Abgelaufen'].map(x=>[x,x]),v.status||'Aktiv')}${field('start_date','Vertragsbeginn',v.start_date,'date')}${field('end_date','Vertragsende',v.end_date,'date')}${field('notice_date','Kündigungstermin',v.notice_date,'date')}${field('premium','Bruttoprämie je Zahlung (€)',v.premium)}${select('frequency','Zahlungsrhythmus',[['12','monatlich'],['4','vierteljährlich'],['2','halbjährlich'],['1','jährlich']],v.frequency||'1')}</div><p class="hint">Bei gleicher Polizzennummer wird die Datei direkt an den bereits vorhandenen Vertrag und damit automatisch an dessen Kunden angehängt.</p>
 <h3>2. Kunde für einen neuen Vertrag</h3><div class="form-grid">${select('customer_action','Kundenaktion',[['existing','Bestehenden Kunden unverändert verwenden'],['new','Neuen Kunden anlegen']],customerAction,true)}${select('customer_id','Bestehender Kunde',[['','Bitte auswählen'],...importCustomerOptions()],customerId)}${select('customer_type','Erkannter Kundentyp',[['natural','Natürliche Person'],['legal','Juristische Person']],c.customer_type||'natural',true)}${field('company','Erkannter Firmenname',c.company)}${field('first_name','Erkannter Vorname / Kontaktperson',c.first_name)}${field('last_name','Erkannter Nachname / Kontaktperson',c.last_name)}${field('email','Erkannte Kunden-E-Mail',c.email,'email')}${field('phone','Erkanntes Telefon',c.phone)}${field('address','Erkannte Adresse',c.address)}${field('postal_code','Erkannte PLZ',c.postal_code)}${field('city','Erkannter Ort',c.city)}${field('birthday','Erkannter Geburtstag',c.birthday,'date')}</div><p class="hint" id="import-customer-type-hint"></p><label class="check"><input type="checkbox" name="update_customer"> Optional: ausschließlich leere Kontakt- und Adressfelder des bestehenden Kunden ergänzen</label><p class="hint">Bei einem bestehenden Vertrag wird der Kunde immer aus dem Vertrag übernommen. Bereits gespeicherte Stammdaten werden durch den Import niemals überschrieben.</p>
 <details class="extracted"><summary>Erkannten Text kontrollieren</summary><pre>${esc(result.text_excerpt)}</pre></details><p class="hint">Bei einem neuen Vertrag sind Versicherer, Polizzennummer, Sparte, Beginn und Prämie erforderlich. Bei Kfz-Polizzen bleibt die Steuer bis zur Prüfung von Deckung und Fahrzeugdaten unaufgeschlüsselt. Öffne anschließend den Vertrag und erfasse die Steuerbestandteile laut Polizze oder berechne die Motorsteuer für Pkw M1 automatisch.</p>`,'imports/commit',{},'Geprüft übernehmen');
 pendingImport.analysis=result;updateImportCustomerType();
}

async function handleImportAnalyze(form){
 const file=form.elements.file.files[0];
 if(!file||file.size>20*1024*1024)throw new Error('Bitte eine Datei mit maximal 20 MB wählen.');
 const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=()=>reject(new Error('Die Datei konnte nicht gelesen werden.'));reader.readAsDataURL(file);});
 pendingImport={filename:file.name,data};const result=await api('imports/analyze',pendingImport);showImportReview(result);
}

async function handleImportCommit(form){
 if(!pendingImport)throw new Error('Die Importdatei ist nicht mehr verfügbar. Bitte erneut auswählen.');
 const f=Object.fromEntries(new FormData(form));
 if(f.contract_action==='existing'&&!f.contract_id)throw new Error('Bitte einen bestehenden Vertrag auswählen.');
 if(f.contract_action!=='existing'&&f.customer_action==='new'&&f.customer_type==='natural'&&(!f.first_name||!f.last_name))throw new Error('Für eine natürliche Person bitte Vor- und Nachname ergänzen.');
 if(f.contract_action!=='existing'&&f.customer_action==='new'&&f.customer_type==='legal'&&!f.company)throw new Error('Für eine juristische Person bitte den Firmennamen ergänzen.');
 if(f.contract_action!=='existing'&&f.customer_action==='existing'&&!f.customer_id)throw new Error('Bitte einen bestehenden Kunden auswählen.');
 if(f.contract_action==='new'&&['insurer','policy_number','line','start_date','premium'].some(k=>!f[k]))throw new Error('Für den neuen Vertrag bitte Versicherer, Polizzennummer, Sparte, Beginn und Prämie ergänzen.');
 return api('imports/commit',{filename:pendingImport.filename,data:pendingImport.data,customer_action:f.customer_action,customer_id:f.customer_id,update_customer:Boolean(form.elements.update_customer?.checked),contract_action:f.contract_action,contract_id:f.contract_id,customer:{customer_type:f.customer_type,first_name:f.first_name,last_name:f.last_name,company:f.company,email:f.email,phone:f.phone,address:f.address,postal_code:f.postal_code,city:f.city,birthday:f.birthday},contract:{insurer:f.insurer,policy_number:f.policy_number,line:f.line,ownership:f.ownership,status:f.status,start_date:f.start_date,end_date:f.end_date,notice_date:f.notice_date,premium:f.premium,frequency:f.frequency}});
}

function showDroppedFile(input){
 const name=input.files?.[0]?.name,display=input.closest('.file-drop')?.querySelector('.file-drop-name');
 if(display&&name)display.textContent='Ausgewählt: '+name;
}

document.addEventListener('change',event=>{if(event.target.matches('.file-drop input[type=file]'))showDroppedFile(event.target);});
document.addEventListener('dragover',event=>{const drop=event.target.closest?.('.file-drop');if(!drop)return;event.preventDefault();drop.classList.add('dragging');});
document.addEventListener('dragleave',event=>{const drop=event.target.closest?.('.file-drop');if(drop&&!drop.contains(event.relatedTarget))drop.classList.remove('dragging');});
document.addEventListener('drop',event=>{
 const drop=event.target.closest?.('.file-drop');if(!drop)return;event.preventDefault();drop.classList.remove('dragging');
 const input=drop.querySelector('input[type=file]'),transfer=event.dataTransfer;
 let files=[...(transfer?.files||[])];
 if(!files.length)files=[...(transfer?.items||[])].filter(item=>item.kind==='file').map(item=>item.getAsFile()).filter(Boolean);
 if(files.length){
  const file=files[0];
  if(drop.classList.contains('import-drop')&&file.name.toLowerCase().endsWith('.msg')){notice('Outlook-MSG kann der Browser nicht direkt lesen. Bitte die Nachricht als EML speichern und diese Datei ablegen.');return;}
  if(!file.size){notice('Das Mailprogramm stellt beim direkten Ziehen keine lesbare Datei bereit. Bitte die E-Mail zuerst auf den Schreibtisch ziehen und danach hier ablegen.');return;}
  if(file.size>20*1024*1024){notice('Bitte eine Datei mit maximal 20 MB wählen.');return;}
  const list=new DataTransfer();list.items.add(file);input.files=list.files;showDroppedFile(input);return;
 }
 if(!drop.classList.contains('import-drop')){notice('Es wurde keine lesbare Datei übergeben. Bitte die Datei zuerst speichern und danach hier ablegen.');return;}
 const plain=transfer?.getData('text/plain')?.trim();
 if(plain){
  const file=new File([plain],'E-Mail-Import-'+new Date().toISOString().slice(0,10)+'.txt',{type:'text/plain'}),list=new DataTransfer();
  list.items.add(file);input.files=list.files;showDroppedFile(input);notice('Der lesbare Nachrichtentext wurde übernommen. Mail-Anhänge sind in diesem Format nicht enthalten.');return;
 }
 notice('Outlook oder Mail hat dem Browser keine lesbare Nachricht übergeben. Bitte die E-Mail zuerst als EML-Datei speichern.');
});
