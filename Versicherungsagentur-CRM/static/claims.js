'use strict';

const CLOSED_CLAIM_STATUSES = ['Reguliert', 'Abgelehnt', 'Geschlossen'];
const CLAIM_STATUSES = ['Entwurf', 'Gemeldet', 'In Bearbeitung', 'Warten auf Unterlagen',
  'Reguliert', 'Abgelehnt', 'Geschlossen'];

const claim = id => db.claims.find(item => item.id === Number(id));
const claimIsOpen = item => !CLOSED_CLAIM_STATUSES.includes(item.status);
const claimTitle = item => item.title || item.insurer_claim_number || 'Schaden ohne Bezeichnung';

function claimGroup(line) {
  const value = String(line || '').toLocaleLowerCase('de-AT');
  if (/kfz|kasko|fahrzeug/.test(value)) return 'vehicle';
  if (/unfall|kranken|invalid|berufsunf/.test(value)) return 'person';
  if (/haftpflicht/.test(value)) return 'liability';
  if (/rechtsschutz/.test(value)) return 'legal';
  if (/reise/.test(value)) return 'travel';
  if (/leben|ableben|rente/.test(value)) return 'life';
  if (/cyber|betriebsunterbrech/.test(value)) return 'cyber';
  if (/haushalt|eigenheim|gebäude|wohngebäude|sach|feuer|sturm|wasser|einbruch/.test(value)) return 'property';
  return 'general';
}

function claimBadge(item) {
  const value = item.status;
  const cls = CLOSED_CLAIM_STATUSES.includes(value) ? (value === 'Reguliert' ? 'green' : 'red') : '';
  return `<span class="badge ${cls}">${esc(value)}</span>`;
}

function claimTable(rows) {
  return rows.length ? table(['Schaden', 'Kunde / Vertrag', 'Ereignis', 'Evidenz', 'Status', ''], rows.map(item => {
    const c = customer(item.customer_id), policy = contract(item.contract_id);
    const overdue = claimIsOpen(item) && item.reminder_date && item.reminder_date < today();
    return `<tr><td><button class="name-link" data-action="claim-detail" data-id="${item.id}"><span class="initials">S</span><span>${esc(claimTitle(item))}<small>${esc(item.number)}${item.insurer_claim_number ? ' · ' + esc(item.insurer_claim_number) : ''}</small></span></button></td><td>${esc(full(c))}<small>${policy ? esc(policy.policy_number + ' · ' + policy.line) : esc(item.line || 'Ohne Vertragszuordnung')}</small></td><td>${day(item.event_date)}<small>${esc(item.location || 'Ort noch offen')}</small></td><td>${item.reminder_date ? day(item.reminder_date) : '—'}${overdue ? '<small class="due-overdue">Überfällig</small>' : `<small>${esc(item.reminder_note || '')}</small>`}</td><td>${claimBadge(item)}</td><td class="row-actions">${button('Öffnen →', 'claim-detail', item.id, 'text-button')}${deleteButton('claims',item.id)}</td></tr>`;
  })) : empty('Keine Schäden gefunden', 'Lege einen Schaden an oder passe Suche und Filter an.', 'new-claim', '+ Schaden anlegen');
}

function claimsView() {
  if (selected) return claimDetail(Number(selected));
  const rows = db.claims.filter(item =>
    (!statusFilter || (statusFilter === 'open' ? claimIsOpen(item) : item.status === statusFilter)) &&
    [item.number, item.insurer_claim_number, item.title, item.line, full(customer(item.customer_id)), contract(item.contract_id)?.policy_number]
      .join(' ').toLocaleLowerCase('de-AT').includes(query.toLocaleLowerCase('de-AT')));
  const options = [['', 'Alle Status'], ['open', 'Alle offenen'], ...CLAIM_STATUSES.map(x => [x, x])];
  return pagehead('Schäden', 'Schadenakten, Korrespondenz und Evidenztermine im Blick.', button('+ Neuer Schaden', 'new-claim', '', 'primary')) +
    `<section class="card">${tools('Schadennummer, Kunde, Vertrag oder Sparte suchen', select('status-filter', '', options, statusFilter))}${claimTable(rows)}</section>`;
}

function claimsDashboard() {
  const open = db.claims.filter(claimIsOpen);
  const ordered = [...open].sort((a, b) => (a.reminder_date || '9999').localeCompare(b.reminder_date || '9999'));
  const body=ordered.length?ordered.map(item => `<button class="deadline" data-action="claim-detail" data-id="${item.id}"><span class="date-box">${item.reminder_date ? day(item.reminder_date) : 'Offen'}</span><span><strong>${esc(item.number)} · ${esc(claimTitle(item))}</strong><small>${esc(full(customer(item.customer_id)))}${item.reminder_note ? ' · ' + esc(item.reminder_note) : item.reminder_date ? '' : ' · Evidenzdatum fehlt'}</small></span>${item.reminder_date && item.reminder_date < today() ? '<span class="badge red">Überfällig</span>' : claimBadge(item)}</button>`).join(''):empty('Keine offenen Schäden', 'Neue Schadenfälle und Fälligkeiten erscheinen hier.', 'new-claim', '+ Schaden anlegen');
  return `<section class="card claim-dashboard dashboard-list-card"><div class="sectionhead"><h2>Offene Schäden & Evidenzen</h2><div>${button('+ Schaden', 'new-claim', '', 'text-button')}${button('Alle Schäden →', 'nav', 'claims', 'text-button')}</div></div>${dashboardScroll('Offene Schäden und Evidenzen',body)}</section>`;
}

function valueOrDash(value) { return value ? esc(value) : '—'; }
function moneyOrDash(value) { return value == null ? '—' : euro(value); }

function claimDetail(id) {
  const item = claim(id);
  if (!item) { selected = null; return claimsView(); }
  const c = customer(item.customer_id), policy = contract(item.contract_id);
  const docs = db.documents.filter(d => d.claim_id === item.id);
  const emails = db.claim_emails.filter(mail => mail.claim_id === item.id)
    .sort((a, b) => (b.email_date || b.created_at).localeCompare(a.email_date || a.created_at));
  const details = Object.entries(item.details || {}).filter(([, value]) => value);
  return `${button('← Zur Schadenliste', 'back', '', 'text-button')}` +
    pagehead(`${esc(item.number)} · ${esc(claimTitle(item))}`, `${esc(full(c))}${policy ? ' · ' + esc(policy.policy_number) : ''}`,
      button('Schaden bearbeiten', 'edit-claim', item.id) + button('+ Dokument', 'claim-document', item.id) + button('+ E-Mail', 'claim-email', item.id, 'primary') + deleteButton('claims',item.id)) +
    `<div class="claim-summary"><section class="card profile"><h2>Schadenakte ${claimBadge(item)}</h2><dl><dt>Versicherer-Schadennummer</dt><dd>${valueOrDash(item.insurer_claim_number)}</dd><dt>Sparte / Vertrag</dt><dd>${valueOrDash(item.line)}${policy ? '<br>' + esc(policy.insurer + ' · ' + policy.policy_number) : ''}</dd><dt>Schadenereignis</dt><dd>${day(item.event_date)}${item.event_time ? ' · ' + esc(item.event_time) : ''}<br>${valueOrDash(item.location)}</dd><dt>Beim Versicherer gemeldet</dt><dd>${day(item.reported_date)}</dd><dt>Evidenz</dt><dd class="${item.reminder_date && item.reminder_date < today() && claimIsOpen(item) ? 'due-overdue' : ''}">${day(item.reminder_date)}<br>${valueOrDash(item.reminder_note)}</dd><dt>Sachbearbeitung</dt><dd>${valueOrDash(item.handler)}<br>${valueOrDash(item.contact)}</dd></dl></section><div><section class="card padded"><h2>Sachverhalt</h2><h3>${esc(claimTitle(item))}</h3><p class="preline">${valueOrDash(item.description)}</p><dl class="claim-facts"><dt>Ursache / Hergang</dt><dd>${valueOrDash(item.cause)}</dd><dt>Anspruchsteller / betroffene Person</dt><dd>${valueOrDash(item.claimant)}</dd><dt>Dritte / Gegner</dt><dd>${valueOrDash(item.third_party)}</dd><dt>Polizei / Aktenzahl</dt><dd>${valueOrDash(item.police_reference)}</dd><dt>Deckungsstand</dt><dd>${valueOrDash(item.coverage_status)}</dd><dt>Schadenschätzung</dt><dd>${moneyOrDash(item.estimated_cents)}</dd><dt>Selbstbehalt</dt><dd>${moneyOrDash(item.deductible_cents)}</dd><dt>Reguliert</dt><dd>${moneyOrDash(item.paid_cents)}</dd></dl>${details.length ? `<h3>Spartenspezifische Angaben</h3><dl class="claim-facts">${details.map(([key, value]) => `<dt>${esc(claimDetailLabel(key))}</dt><dd>${esc(value)}</dd>`).join('')}</dl>` : ''}</section></div></div>` +
    `<section class="card"><div class="sectionhead"><h2>Dokumente</h2>${button('+ Dokument anhängen', 'claim-document', item.id)}</div>${claimDocumentList(item.id)}</section>` +
    `<section class="card"><div class="sectionhead"><h2>E-Mail-Verlauf</h2>${button('+ E-Mail ablegen', 'claim-email', item.id)}</div>${emails.length ? emails.map(mail => `<article class="claim-email"><header><div><strong>${esc(mail.subject || 'Ohne Betreff')}</strong><small>${esc(mail.direction)} · ${mail.email_date ? new Date(mail.email_date).toLocaleString('de-AT') : day(mail.created_at)}</small></div><span>${esc(mail.from_address || '—')} → ${esc(mail.to_address || '—')} ${deleteButton('claim_emails',mail.id)}</span></header>${mail.cc_address ? `<small>CC: ${esc(mail.cc_address)}</small>` : ''}<p class="preline">${esc(mail.body || '')}</p></article>`).join('') : empty('Noch keine E-Mails', 'Eingehende und ausgehende Korrespondenz kann hier dokumentiert werden.')}</section>`;
}

const CLAIM_DETAIL_LABELS = {
  vehicle_plate:'Kennzeichen', vehicle_description:'Fahrzeug', driver:'Lenker/in', opponent:'Unfallgegner/in', opponent_insurer:'Gegnerische Versicherung',
  property_object:'Versicherungsort / Objekt', damage_type:'Schadenart', damaged_items:'Beschädigte oder abhandengekommene Sachen', emergency_measures:'Schadenminderungsmaßnahmen',
  injured_person:'Verletzte / erkrankte Person', injury:'Verletzung / Diagnose', doctor_hospital:'Arzt / Krankenhaus', incapacity:'Arbeitsunfähigkeit / Dauer',
  legal_opponent:'Gegner/in', legal_matter:'Rechtsangelegenheit', lawyer_reference:'Anwalt / Aktenzeichen', trip_details:'Reise / Zeitraum / Ziel', booking_reference:'Buchungsnummer', travel_reason:'Storno- oder Schadenursache',
  insured_person:'Versicherte Person', beneficiary:'Bezugsberechtigte Person', life_event:'Versicherungsfall', affected_systems:'Betroffene Systeme', downtime:'Ausfallzeit', data_affected:'Betroffene Daten', incident_response:'Sofortmaßnahmen',
  liability_allegation:'Anspruch / Vorwurf', demand:'Forderung des Dritten', witnesses:'Zeugen und Kontaktdaten', other_insurances:'Weitere Versicherungen'
};
function claimDetailLabel(key) { return CLAIM_DETAIL_LABELS[key] || key; }
function detailField(name, label, value='', area=false) { return area ? textarea(name, label, value) : field(name, label, value); }

function claimSpecificFields(item={}) {
  const d = item.details || {};
  const group = (name, fields) => `<div class="claim-specific" data-claim-group="${name}"><h3>${fields[0]}</h3><div class="form-grid">${fields.slice(1).map(([key, label, area]) => detailField(key, label, d[key], area)).join('')}</div></div>`;
  return group('vehicle', ['Kfz-Angaben', ['vehicle_plate','Kennzeichen'], ['vehicle_description','Fahrzeug / Fahrgestellnummer'], ['driver','Lenker/in'], ['opponent','Unfallgegner/in und Kontaktdaten',true], ['opponent_insurer','Gegnerische Versicherung'], ['witnesses','Zeugen',true]]) +
    group('property', ['Sachschaden-Angaben', ['property_object','Versicherungsort / betroffenes Objekt'], ['damage_type','Schadenart'], ['damaged_items','Beschädigte oder abhandengekommene Sachen',true], ['emergency_measures','Sofort- und Schadenminderungsmaßnahmen',true]]) +
    group('liability', ['Haftpflicht-Angaben', ['liability_allegation','Anspruch / Vorwurf',true], ['demand','Forderung des Dritten'], ['witnesses','Zeugen',true], ['other_insurances','Weitere betroffene Versicherungen',true]]) +
    group('person', ['Personenschaden-Angaben', ['injured_person','Verletzte / erkrankte Person'], ['injury','Verletzung / Diagnose',true], ['doctor_hospital','Arzt / Krankenhaus'], ['incapacity','Arbeitsunfähigkeit / Dauer'], ['other_insurances','Weitere Unfall-/Krankenversicherungen',true]]) +
    group('legal', ['Rechtsschutz-Angaben', ['legal_opponent','Gegner/in'], ['legal_matter','Rechtsangelegenheit / Vorwurf',true], ['lawyer_reference','Anwalt / Aktenzeichen']]) +
    group('travel', ['Reise-Angaben', ['trip_details','Reise / Zeitraum / Ziel',true], ['booking_reference','Buchungsnummer'], ['travel_reason','Storno- oder Schadenursache',true]]) +
    group('life', ['Personen-/Lebensversicherung', ['insured_person','Versicherte Person'], ['beneficiary','Bezugsberechtigte Person'], ['life_event','Versicherungsfall',true]]) +
    group('cyber', ['Cyber-/Betriebsunterbrechungs-Angaben', ['affected_systems','Betroffene Systeme',true], ['downtime','Ausfallzeit'], ['data_affected','Betroffene Daten',true], ['incident_response','Sofortmaßnahmen',true]]);
}

function editClaim(item={}, customerId='', contractId='') {
  if (!db.customers.length) { notice('Lege zuerst einen Kunden an.'); return; }
  const initialContract = contract(contractId || item.contract_id);
  const initialCustomer = customerId || item.customer_id || initialContract?.customer_id || '';
  modal(item.id ? `Schaden ${esc(item.number)} bearbeiten` : 'Neuen Schaden anlegen',
    `<div class="info">Du kannst den Schaden jederzeit als Entwurf speichern. Außer dem Kunden dürfen Angaben vorerst fehlen und später ergänzt werden.</div><div class="form-grid">${customerPicker(initialCustomer)}${select('contract_id','Versicherungsvertrag (optional)',[['','Noch nicht zugeordnet']],item.contract_id||contractId)}${field('title','Kurzbezeichnung',item.title)}${select('status','Status',CLAIM_STATUSES.map(x=>[x,x]),item.status||'Entwurf')}${field('line','Versicherungssparte',item.line||initialContract?.line||'')}${field('insurer_claim_number','Schadennummer des Versicherers',item.insurer_claim_number)}${field('event_date','Schadendatum',item.event_date,'date')}${field('event_time','Uhrzeit',item.event_time,'time')}${field('reported_date','Beim Versicherer gemeldet am',item.reported_date,'date')}${field('location','Schadenort',item.location)}</div>${textarea('description','Schadenbeschreibung / Sachverhalt',item.description)}${textarea('cause','Hergang / Ursache',item.cause)}<div class="form-grid">${field('claimant','Anspruchsteller / betroffene Person',item.claimant)}${field('third_party','Dritte / Gegner',item.third_party)}${field('police_reference','Polizei / Behörde / Aktenzahl',item.police_reference)}${field('coverage_status','Deckungsstand / Entscheidung',item.coverage_status)}${field('estimated','Schadenschätzung (€)',item.estimated_cents==null?'':dec(item.estimated_cents))}${field('deductible','Selbstbehalt (€)',item.deductible_cents==null?'':dec(item.deductible_cents))}${field('paid','Regulierter Betrag (€)',item.paid_cents==null?'':dec(item.paid_cents))}${field('handler','Sachbearbeiter/in',item.handler)}${field('contact','Kontakt Versicherer / Gutachter',item.contact)}${field('reminder_date','Evidenzdatum / nächste Fälligkeit',item.reminder_date,'date')}</div>${textarea('reminder_note','Was ist bei der Evidenz zu erledigen?',item.reminder_note)}${claimSpecificFields(item)}${item.id?`<h3>Anlagen & Korrespondenz</h3><p class="hint">${db.documents.filter(d=>d.claim_id===item.id).length} Dokument(e) · ${db.claim_emails.filter(m=>m.claim_id===item.id).length} E-Mail(s). In der Schadenakte kannst du beides ergänzen.</p>`:''}`,
    'claims', item, item.id ? 'Schaden speichern' : 'Schaden anlegen');
  setupCustomerPicker(initialCustomer);
  setupClaimForm(item.contract_id || contractId);
}

function setupClaimForm(initialContract='') {
  const f = document.querySelector('#editor');
  const customerInput = f.elements.customer_id, contractSelect = f.elements.contract_id, line = f.elements.line;
  function contractsForCustomer(keep='') {
    const rows = db.contracts.filter(c => c.customer_id === Number(customerInput.value));
    contractSelect.innerHTML = `<option value="">Noch nicht zugeordnet</option>` + rows.map(c => `<option value="${c.id}" ${String(c.id)===String(keep)?'selected':''}>${esc(c.policy_number)} · ${esc(c.insurer)} · ${esc(c.line)}</option>`).join('');
    if (keep && !rows.some(c => String(c.id) === String(keep))) contractSelect.value = '';
  }
  function update() {
    const policy = contract(contractSelect.value);
    line.readOnly = Boolean(policy);
    if (policy) line.value = policy.line;
    const active = claimGroup(line.value);
    f.querySelectorAll('[data-claim-group]').forEach(section => section.hidden = section.dataset.claimGroup !== active);
  }
  contractsForCustomer(initialContract);
  customerInput.addEventListener('change', () => { contractsForCustomer(); update(); });
  contractSelect.addEventListener('change', update);
  line.addEventListener('input', update);
  update();
}

function claimDocumentList(id) {
  const docs = db.documents.filter(d => d.claim_id === Number(id));
  return docs.length ? table(['Dateiname','Hinzugefügt','Größe',''], docs.map(d => `<tr><td class="document-name">${esc(d.filename)}</td><td>${day(d.created_at)}</td><td>${Math.max(1,Math.ceil(d.size/1024))} KB</td><td class="row-actions">${documentActions(d)}</td></tr>`)) : empty('Noch keine Dokumente', 'Fotos, Meldungen, Rechnungen, Gutachten und weitere Unterlagen hier ablegen.');
}

function uploadClaimDocument(id) {
  const item = claim(id); if (!item) return notice('Schaden nicht gefunden. Bitte neu laden.');
  modal('Dokument zur Schadenakte', `<input type="hidden" name="claim_id" value="${item.id}"><p><strong>${esc(item.number)} · ${esc(claimTitle(item))}</strong><br>${esc(full(customer(item.customer_id)))}</p>${claimDocumentList(item.id)}<h3>Dokument anhängen</h3>${fileDrop()}<p class="hint">Fotos, PDFs, Rechnungen, Gutachten sowie gespeicherte E-Mails (.eml oder .msg) werden lokal abgelegt und mitgesichert.</p>`, 'documents', {}, 'Dokument anhängen');
  document.querySelector('#editor').dataset.claimId = String(item.id);
}

function addClaimEmail(id) {
  const item = claim(id); if (!item) return notice('Schaden nicht gefunden. Bitte neu laden.');
  const local = new Date(Date.now() - new Date().getTimezoneOffset()*60000).toISOString().slice(0,16);
  modal('E-Mail zur Schadenakte ablegen', `<input type="hidden" name="claim_id" value="${item.id}"><p><strong>${esc(item.number)} · ${esc(claimTitle(item))}</strong></p><div class="form-grid">${select('direction','Art',[['Eingang','Eingang'],['Ausgang','Ausgang'],['Notiz','Gesprächs-/E-Mail-Notiz']],'Eingang')}${field('email_date','Datum und Uhrzeit',local,'datetime-local')}${field('from_address','Von', '')}${field('to_address','An', '')}${field('cc_address','CC', '')}${field('subject','Betreff', '')}</div>${textarea('body','E-Mail-Inhalt / Gesprächsnotiz','')}<p class="hint">Eine originale E-Mail-Datei oder Anhänge kannst du zusätzlich über „Dokument“ in der Schadenakte ablegen.</p>`, 'claim-emails', {}, 'E-Mail ablegen');
}
