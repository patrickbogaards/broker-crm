"""Local document/email extraction and conservative CRM field suggestions."""
from email import policy
from email.parser import BytesParser
from email.header import decode_header, make_header
from pathlib import Path
import re
import os
import platform
import shutil
import subprocess
import tempfile
import unicodedata

SUPPORTED = {'.pdf', '.png', '.jpg', '.jpeg', '.tif', '.tiff', '.heic', '.eml', '.txt'}


def clean(value):
    return re.sub(r'\s+', ' ', value or '').strip(' \t:;-')


def normalized(value):
    value = unicodedata.normalize('NFKD', value or '').encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+', '', value)


def decode_header_text(value):
    try:
        return str(make_header(decode_header(value or '')))
    except (LookupError, UnicodeError):
        return value or ''


def parse_eml(raw):
    message = BytesParser(policy=policy.default).parsebytes(raw)
    parts = []
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == 'text/plain' and not part.get_filename():
                try:
                    parts.append(part.get_content())
                except (LookupError, UnicodeError):
                    pass
    elif message.get_content_type() == 'text/plain':
        parts.append(message.get_content())
    if not parts:
        for part in message.walk() if message.is_multipart() else [message]:
            if part.get_content_type() == 'text/html':
                try:
                    html = part.get_content()
                    parts.append(re.sub(r'<[^>]+>', ' ', html))
                except (LookupError, UnicodeError):
                    pass
    sender = decode_header_text(message.get('From', ''))
    subject = decode_header_text(message.get('Subject', ''))
    return '\n'.join([f'Absender: {sender}', f'Betreff: {subject}', *parts]), {
        'from': sender, 'subject': subject,
        'email': (re.search(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', sender) or [None])[0] or ''
    }


def _run(command, timeout=120, environment=None):
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                              check=False, env=environment)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError('Die Texterkennung konnte nicht gestartet werden.') from exc


def _mac_extract(path, helper):
    command = ['/usr/bin/swift']
    sdk = Path('/Library/Developer/CommandLineTools/SDKs/MacOSX26.sdk')
    if sdk.exists():
        command += ['-sdk', str(sdk)]
    environment = os.environ.copy()
    cache = Path(tempfile.gettempdir()) / 'versicherungsagentur-crm-swift-cache'
    cache.mkdir(mode=0o700, exist_ok=True)
    environment['CLANG_MODULE_CACHE_PATH'] = str(cache)
    return _run([*command, str(helper), str(path)], environment=environment)


def _tesseract(path):
    run = _run([shutil.which('tesseract'), str(path), 'stdout', '-l', 'deu+eng'])
    if run.returncode and ('deu' in run.stderr or 'eng' in run.stderr):
        run = _run([shutil.which('tesseract'), str(path), 'stdout'])
    return run


def _server_extract(path, suffix):
    if suffix == '.pdf' and shutil.which('pdftotext'):
        embedded = _run([shutil.which('pdftotext'), str(path), '-'])
        if embedded.returncode == 0 and len(clean(embedded.stdout)) >= 8:
            return embedded
    if suffix != '.pdf' and shutil.which('tesseract'):
        return _tesseract(path)
    if suffix == '.pdf' and shutil.which('pdftoppm') and shutil.which('tesseract'):
        with tempfile.TemporaryDirectory() as folder:
            prefix = str(Path(folder) / 'page')
            rendered = _run([shutil.which('pdftoppm'), '-f', '1', '-l', '30', '-r', '220',
                             '-png', str(path), prefix])
            if rendered.returncode:
                return rendered
            chunks, errors = [], []
            for page in sorted(Path(folder).glob('page-*.png')):
                recognized = _tesseract(page)
                chunks.append(recognized.stdout)
                if recognized.returncode:
                    errors.append(recognized.stderr)
            return subprocess.CompletedProcess([], 0 if clean('\n'.join(chunks)) else 1,
                                               '\n'.join(chunks), '\n'.join(errors))
    raise ValueError('Auf diesem Server fehlt die Texterkennung. Bitte Tesseract und Poppler installieren.')


def extract_text(filename, raw, helper):
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED:
        raise ValueError('Unterstützt werden PDF, PNG, JPG, TIFF, HEIC, EML und TXT.')
    if suffix == '.eml':
        text, meta = parse_eml(raw)
        return text, meta, 'email'
    if suffix == '.txt':
        return raw.decode('utf-8', errors='replace'), {}, 'email'
    with tempfile.NamedTemporaryFile(suffix=suffix) as item:
        item.write(raw); item.flush()
        if platform.system() == 'Darwin' and Path('/usr/bin/swift').exists():
            run = _mac_extract(Path(item.name), helper)
        else:
            run = _server_extract(Path(item.name), suffix)
    if run.returncode:
        raise ValueError(clean(run.stderr) or 'Aus dieser Datei konnte kein Text gelesen werden.')
    if len(clean(run.stdout)) < 8:
        raise ValueError('Es wurde kein ausreichend lesbarer Text erkannt. Bitte einen besseren Scan verwenden.')
    return run.stdout, {}, 'policy'


def first(patterns, text, flags=re.I):
    for pattern in patterns:
        match = re.search(pattern, text, flags)
        if match:
            return clean(match.group(1))
    return ''


def iso_date(value):
    match = re.search(r'(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})', value or '')
    if not match:
        return ''
    day, month, year = map(int, match.groups())
    year += 2000 if year < 70 else 1900 if year < 100 else 0
    try:
        from datetime import date
        return date(year, month, day).isoformat()
    except ValueError:
        return ''


def amount(value):
    value = (value or '').replace('€', '').replace('EUR', '').strip()
    if ',' in value:
        value = value.replace('.', '').replace(',', '.')
    else:
        value = value.replace(' ', '')
    return value if re.fullmatch(r'\d{1,10}(?:\.\d{1,2})?', value) else ''


def customer_section(text):
    """Return only the policy part that plausibly belongs to the insured person."""
    labels = r'(?:Versicherungsnehmer(?:in)?|Versicherungsnehmer:in|Kunde(?:in)?)'
    marker = re.search(r'^\s*' + labels + r'\s*:?[ \t]*$', text, re.I | re.M)
    if not marker:
        marker = re.search(r'^\s*' + labels + r'\s*:\s*\S[^\n]*$', text, re.I | re.M)
    if not marker:
        return ''
    section = text[marker.start():marker.start() + 1000]
    boundary = re.search(r'\n\s*(?:Versichert ist|Versicherte Person|Versicherungsdauer|Versicherer|'
                         r'Versicherungsgesellschaft|Polizzen?(?:nummer|daten)|Vertragsdaten|'
                         r'Versicherungsumfang|Deckung|Prämie|Berater|Vermittler|Makler)\b',
                         section[marker.end() - marker.start():], re.I)
    if boundary:
        section = section[:marker.end() - marker.start() + boundary.start()]
    return section


COMPANY_FORM = re.compile(
    r'\b(?:GmbH|Ges\.?m\.?b\.?H\.?|AG|SE|OG|KG|e\.?\s*U\.?|Genossenschaft|'
    r'Verein|Stiftung|Privatstiftung|Körperschaft|Gesellschaft\s+m\.?b\.?H\.?)'
    r'(?=$|\s|[,/&+-])', re.I)


def policyholder_value(section):
    value = first([
        r'(?:Versicherungsnehmer(?:in)?|Versicherungsnehmer:in|Kunde(?:in)?)\s*:?[ \t]*\n\s*([^\n;]{3,160})',
        r'(?:Versicherungsnehmer(?:in)?|Versicherungsnehmer:in|Kunde(?:in)?)\s*:\s*([^\n;]{3,160})'
    ], section)
    value = re.split(r'\s+(?=(?:E-?Mail|Telefon|Tel\.?|Mobil|Adresse|Anschrift|Geburtsdatum|Versicherer|Polizze|Vertrag)\b|\d{4}\s)', value, maxsplit=1, flags=re.I)[0]
    value = re.split(r',?\s*(?:geb\.?|geboren)\s+', value, maxsplit=1, flags=re.I)[0]
    value = re.sub(r'^(?:Kundenberater(?:in)?|Versicherungsnehmer(?:in)?|Herrn?|Frau|Dr\.?|Mag\.?)\s+', '', value, flags=re.I)
    value = re.sub(r',\s*(?:MSc|BSc|MBA|BA|MA|LL\.?M\.?)\.?$', '', value, flags=re.I).strip(' ,')
    return value


def looks_like_company(value):
    return bool(COMPANY_FORM.search(value or ''))


def customer_name(section):
    value = policyholder_value(section)
    if ',' in value:
        last, first_name = [clean(part) for part in value.split(',', 1)]
        return first_name, last
    pieces = value.split()
    return (pieces[0], ' '.join(pieces[1:])) if len(pieces) >= 2 else ('', pieces[0] if pieces else '')


def name_tokens(*values):
    ignored = {'herr', 'frau', 'dr', 'mag'}
    return {normalized(part) for value in values for part in re.findall(r"[A-Za-zÄÖÜäöüß'-]+", value or '')
            if normalized(part) and normalized(part) not in ignored}


def suggest(text, meta=None, kind='policy'):
    meta = meta or {}
    compact = text.replace('\r', '')
    section = customer_section(compact) if kind == 'policy' else compact
    contact_source = section if kind == 'policy' else compact
    email = meta.get('email') if kind == 'email' else first([r'\b([\w.+-]+@[\w.-]+\.[A-Za-z]{2,})\b'], contact_source)
    phone = first([r'(?:Telefon|Tel\.?|Mobil|Handy)\s*[:\-]?\s*([+()\d][+()\d /-]{5,})'], contact_source)
    postal_city = re.search(r'\b(\d{4})\s+([A-ZÄÖÜ][A-Za-zÄÖÜäöüß .-]{2,40})', contact_source)
    address = first([r'(?:Adresse|Anschrift)\s*[:\-]?\s*([^\n]*\d+[a-zA-Z]?)', r'\b([A-ZÄÖÜ][A-Za-zÄÖÜäöüß .-]+(?:straße|strasse|gasse|weg|platz)\s+\d+[a-zA-Z]?)\b'], contact_source)
    policyholder = policyholder_value(section)
    customer_type = 'legal' if looks_like_company(policyholder) else 'natural'
    company = policyholder if customer_type == 'legal' else ''
    first_name, last_name = ('', '') if customer_type == 'legal' else customer_name(section)
    birthday = iso_date(first([r'\b(?:geb\.?|geboren)\s*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})'], section))
    if kind == 'email':
        sender_name = re.sub(r'<[^>]+>', '', meta.get('from', '')).strip(' "')
        if sender_name and '@' not in sender_name:
            if looks_like_company(sender_name):
                customer_type, company, first_name, last_name = 'legal', sender_name, '', ''
            else:
                pieces = sender_name.split()
                first_name, last_name = (pieces[0], ' '.join(pieces[1:])) if len(pieces) >= 2 else ('', '')
        if not first_name:
            lines = [clean(x) for x in compact.splitlines() if clean(x)]
            candidates = [x for x in lines[-20:] if re.fullmatch(r'[A-ZÄÖÜ][A-Za-zÄÖÜäöüß.-]+(?:\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß.-]+){1,3}', x)]
            pieces = candidates[-1].split() if candidates else []
            candidate = ' '.join(pieces)
            if looks_like_company(candidate):
                customer_type, company, first_name, last_name = 'legal', candidate, '', ''
            else:
                first_name, last_name = (pieces[0], ' '.join(pieces[1:])) if len(pieces) >= 2 else ('', '')
    policy_number = first([r'(?:Polizzen?|Policen?|Versicherungs|Vertrags)[- ]?(?:nummer|Nr\.?)\s*[:#]?\s*([A-Z0-9][A-Z0-9./_-]{3,})'], compact)
    insurer = first([
        r'(?m)^\s*(?:Versicherer|Versicherungsgesellschaft)\s*[:\-]\s*([^\n]{3,100})$',
        r'(?m)^\s*([^\n]{2,100}\bVersicherung(?:en)?\s+(?:AG|GmbH|VVaG)\b[^\n]{0,60})$'
    ], compact)
    line_aliases = [('Kfz', r'\b(?:kfz|kraftfahr|haftpflicht.*fahrzeug|kasko)\b'), ('Haushalt', r'\bhaushalt'),
                    ('Eigenheim', r'\b(?:eigenheim|wohngebäude|gebäudeversicherung)'), ('Rechtsschutz', r'\brechtsschutz'),
                    ('Haftpflicht', r'\bhaftpflicht'), ('Unfall', r'\bunfallversicherung'), ('Kranken', r'\bkrankenversicherung'),
                    ('Leben', r'\b(?:lebensversicherung|ablebensversicherung)'), ('Reise', r'\breiseversicherung'),
                    ('Cyber', r'\bcyber')]
    line = next((label for label, pattern in line_aliases if re.search(pattern, compact, re.I)), '')
    start = first([
        r'Ursprünglicher\s+Versicherungsbeginn\s*[:\-]?\s*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})',
        r'(?:Versicherungsbeginn|Vertragsbeginn|Beginn)\s*[:\-]?\s*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})',
        r'Gültig ab\s*[:\-]?\s*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})'
    ], compact)
    end = first([r'(?:Versicherungsende|Vertragsende|Ablauf|Gültig bis)\s*[:\-]?\s*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})'], compact)
    notice = first([r'(?:Kündigungstermin|Kündbar zum|Kündigungsdatum)\s*[:\-]?\s*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})'], compact)
    premium = first([
        r'Prämie\s*\(\s*inkl\.?\s*Versicherungssteuer\s*\)\s*(?:EUR|€)?\s*([\d. ]+(?:,\d{1,2})?)',
        r'(?:Gesamtprämie|Bruttoprämie|Zahlungsprämie|Prämie)\s*[:\-]?\s*(?:EUR|€)?\s*([\d. ]+(?:,\d{1,2})?)'
    ], compact)
    frequency = '12' if re.search(r'\bmonatlich', compact, re.I) else '4' if re.search(r'\bvierteljährlich', compact, re.I) else '2' if re.search(r'\bhalbjährlich', compact, re.I) else '1'
    customer = {'customer_type': customer_type, 'first_name': first_name, 'last_name': last_name, 'company': company, 'email': email,
                'phone': phone, 'address': address, 'postal_code': postal_city.group(1) if postal_city else '',
                'city': clean(postal_city.group(2)) if postal_city else '', 'birthday': birthday}
    contract = {'insurer': insurer, 'policy_number': policy_number, 'line': line, 'ownership': 'Eigen',
                'status': 'Aktiv', 'start_date': iso_date(start), 'end_date': iso_date(end),
                'notice_date': iso_date(notice), 'premium': amount(premium), 'frequency': frequency}
    return {'kind': kind, 'customer': customer, 'contract': contract, 'email': meta,
            'text_excerpt': compact[:12000]}


def matches(connection, suggestion):
    customer = suggestion['customer']; contract = suggestion['contract']
    customer_rows = [dict(r) for r in connection.execute('SELECT * FROM customers WHERE archived=0')]
    scored = []
    for row in customer_rows:
        score, reasons = 0, []
        if customer['email'] and row['email'].lower() == customer['email'].lower(): score += 100; reasons.append('gleiche E-Mail')
        if customer['phone'] and normalized(row['phone']) == normalized(customer['phone']): score += 80; reasons.append('gleiche Telefonnummer')
        candidate_is_legal = customer.get('customer_type') == 'legal'
        stored_is_legal = row.get('customer_type', 'natural') == 'legal'
        if candidate_is_legal:
            candidate_company, stored_company = normalized(customer['company']), normalized(row['company'])
            if candidate_company and candidate_company == stored_company:
                score += 100; reasons.append('gleicher Firmenname')
            elif candidate_company and stored_company and len(candidate_company) >= 5 and (candidate_company in stored_company or stored_company in candidate_company):
                score += 45; reasons.append('ähnlicher Firmenname')
        elif not stored_is_legal:
            candidate_names = name_tokens(customer['first_name'], customer['last_name'])
            stored_names = name_tokens(row['first_name'], row['last_name'])
            if len(candidate_names) >= 2 and candidate_names == stored_names:
                reversed_order = (normalized(customer['first_name']) == normalized(row['last_name']) and
                                  normalized(customer['last_name']) == normalized(row['first_name']))
                score += 75; reasons.append('Name stimmt überein' + (' (Reihenfolge vertauscht)' if reversed_order else ''))
            else:
                common = candidate_names & stored_names
                if common:
                    score += 28 * len(common); reasons.append('gemeinsamer Namensteil: ' + ', '.join(sorted(common)))
                elif any(len(a) >= 5 and len(b) >= 5 and (a in b or b in a) for a in candidate_names for b in stored_names):
                    score += 20; reasons.append('ähnlicher Namensteil')
        if customer['postal_code'] and row['postal_code'] == customer['postal_code']: score += 20; reasons.append('gleiche PLZ')
        if customer['city'] and normalized(row['city']) == normalized(customer['city']): score += 12; reasons.append('gleicher Ort')
        if customer['address'] and normalized(row['address']) == normalized(customer['address']): score += 35; reasons.append('gleiche Adresse')
        label = row['company'] if stored_is_legal else f"{row['first_name']} {row['last_name']}"
        if score >= 20: scored.append({'id': row['id'], 'label': label.strip(), 'score': score, 'reason': ', '.join(reasons)})
    scored.sort(key=lambda x: (-x['score'], x['label']))
    contract_matches = []
    if contract['policy_number']:
        key = normalized(contract['policy_number'])
        for row in connection.execute('SELECT * FROM contracts'):
            if normalized(row['policy_number']) == key:
                contract_matches.append({'id': row['id'], 'customer_id': row['customer_id'], 'label': f"{row['policy_number']} · {row['insurer']}", 'score': 120, 'reason': 'gleiche Polizzennummer'})
    return {'customers': scored[:5], 'contracts': contract_matches[:5]}
