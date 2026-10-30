# Versicherungsagentur CRM · Lokale Version 1.0

Kunden, Versicherungsverträge, Schäden, Prämien, Tippgeber-Provisionen und Abrechnungen. Deutsches Browser-Interface in Dunkelblau und Petrol, angelehnt an die freigegebene Designvorschau. Vollständiger Quellcode; keine externen Python-Pakete, keine Cloud-Dienste oder Internetverbindung im laufenden Betrieb erforderlich.

## Auf deinem Mac starten

1. ZIP entpacken und den enthaltenen Ordner `Versicherungsagentur-CRM` nach `/Users/patrickbogaards` verschieben.
2. Voraussetzung: Python 3.10 oder neuer. Im Terminal mit `python3 --version` prüfen. Falls es fehlt, Python für macOS von https://www.python.org/downloads/macos/ installieren.
3. `Start-CRM.command` doppelklicken. Das Terminal muss während der Nutzung geöffnet bleiben. Alternativ im Terminal:

   ```bash
   cd /Users/patrickbogaards/Versicherungsagentur-CRM
   python3 app.py
   ```

4. Das CRM öffnet sich unter http://127.0.0.1:8765 im Browser. Agenturname und eigenes Passwort (mindestens 12 Zeichen) festlegen. Keine vordefinierten Zugangsdaten oder Beispielkunden vorhanden.
5. Zum Beenden im Terminal Strg+C drücken. Zum erneuten Öffnen wieder die Startdatei ausführen. Alle Daten bleiben gespeichert.

Falls macOS die Startdatei nicht ausführbar entpackt hat, starte mit dem obigen Terminal-Befehl. Es ist nicht notwendig, allgemeine Sicherheitseinstellungen des Macs auszuschalten.

Dieser Ordner wurde in der Entwicklungsumgebung vorbereitet. Die Installation auf deinem Mac sowie ein Test auf deinem Mac sind noch erforderlich; auf `/Users/patrickbogaards` bestand in der Erstellungssitzung kein Zugriff.

## Datenablage

- `Daten/crm.sqlite3`: SQL-Datenbank mit Kunden, Verträgen, Schäden, E-Mail-Verläufen, Provisionen, Abrechnungen, Einstellungen und Protokoll.
- `Daten/Dokumente/`: abgelegte Dateien, intern mit zufälligen Dateinamen. Der Originalname bleibt in der Datenbank.
- `Daten/Sicherungen/`: ZIP-Sicherungen mit Datenbank und allen Dokumenten.

Für diese lokale Einzelplatzversion wird **SQLite** verwendet, nicht PostgreSQL. Eine spätere Server-/Mehrbenutzerversion kann auf PostgreSQL migriert werden; die Migration ist nicht bereits implementiert. Daten werden weder in den Chat noch an externe Dienste übertragen. Die App speichert keine fachlichen Datensätze im Browser-Speicher.

## Empfohlene Reihenfolge

1. Unter Einstellungen Agenturname und Anschrift vervollständigen.
2. Kunden anlegen, einschließlich Kontakt, Anschrift, Geburtstag und Notizen.
3. Tippgeber mit Kontakt, Anschrift, IBAN und Standard-Provisionssatz anlegen.
4. Versicherungsverträge mit Kunde, Versicherer, Sparte, Herkunft „Eigen“ oder „Fremd“, eindeutiger Polizzennummer, Status und Terminen anlegen. Bei noch unbekannter Polizzennummer eine eindeutige vorläufige Nummer verwenden.
5. Prämie **je Zahlung** und Zahlungsrhythmus eingeben. Die Jahresprämie wird hochgerechnet.
6. Je Vertrag den Tippgeber, die tatsächlich vereinbarte Berechnungsbasis und den Anteil festlegen.
7. Bestätigte Provisionen einzeln mit Datum, Art, Betrag und eindeutiger Belegreferenz buchen.
8. Abrechnung für einen Tippgeber und Buchungszeitraum erstellen. Anschließend über „Beleg / PDF“ im Druckdialog als PDF speichern.
9. Nach tatsächlicher Zahlung bzw. Verrechnung Datum und Referenz dokumentieren.

## Prämienberechnung

- Jahresprämie = Betrag je Zahlung × Zahlungen pro Jahr.
- Monatlich: 12; vierteljährlich: 4; halbjährlich: 2; jährlich: 1.
- Beispiel: 100,50 € monatlich ergeben 1.206,00 € hochgerechnete Jahresprämie.
- Die Prämie kann netto oder brutto eingegeben werden. Versicherungssteuer wird pro Zahlung auf Cent gerundet und mit Netto, Brutto und Jahresbetrag angezeigt. Übersichten verwenden den Bruttobetrag.
- Automatisch nach Sparte: Sach/Kfz/Unfall 11 %, Kranken 1 %, Leben/Invalidität im Regelfall 4 %, Pensionskassenbeiträge 2,5 %. Bei Kapitalversicherungen mit Einmalerlag und kurzer Laufzeit kann die 11-%-Regel ausgewählt werden. Quelle: [BMF, Stand 10.09.2026](https://www.bmf.gv.at/themen/steuern/steuern-von-a-bis-z/versicherungssteuer/bemessungsgrundlage-steuersaetze.html).
- Alternativ „Manueller Steuersatz“ oder „Manueller Steuerbetrag“ auswählen; auch 0 ist möglich. Unbekannte Sparten und Pflanzen-/Hagelversicherungen werden manuell erfasst, da letztere die Versicherungssumme als Bemessungsgrundlage benötigen.
- Motorbezogene Versicherungssteuer und Feuerschutzsteuer werden nicht automatisch berechnet. Bei Bedarf gesamten Steuerbetrag laut Polizze manuell erfassen. Auch Zuschläge, Indexierung und anteilige Laufzeiten werden nicht automatisch ermittelt.
- Bestehende Verträge behalten ihren Gesamtbetrag und werden zunächst als „Steuer nicht aufgeschlüsselt“ angezeigt. Beim Bearbeiten kann eine Steuerberechnung gewählt werden. Datenbankfelder werden beim nächsten CRM-Start automatisch ergänzt.
- Die Übersicht addiert ausschließlich Verträge mit Status „Aktiv“. Statuswechsel erfolgen manuell; die Anwendung leitet keinen Vertragsstatus aus einem Datum ab.

## Tippgeber-Provisionsregeln

Die im Vertrag hinterlegte Regel wird bei jeder Buchung übernommen und dort dauerhaft gespeichert:

| Basis | Formel / Eingabe |
|---|---|
| Eingegangene Agenturprovision | Eingegebene tatsächlich eingegangene Agenturprovision × vereinbarter Prozentsatz |
| Nettoprämie | (Gespeicherte Bruttoprämie − Versicherungssteuer) × vereinbarter Prozentsatz; wahlweise je Zahlung oder für ein Versicherungsjahr |
| Fixbetrag | Eingegebener Betrag ohne Prozentrechnung |

Beispiel: 2.000,00 € Agenturprovision × 15 % = 300,00 € Tippgeber-Provision.

Geldbeträge werden in ganzen Cent gespeichert. Prozentwerte haben höchstens zwei Nachkommastellen. Jede Prozentbuchung wird mit Dezimalrechnung kaufmännisch auf Cent gerundet (ROUND_HALF_UP), anschließend werden die gerundeten Buchungen summiert. Fixbeträge werden unverändert übernommen.

- Abschluss-, Folge- und Sondervergütungen sind separat buchbar. Wiederkehrende Provisionen werden **nicht automatisch** erzeugt. Buchungsreferenzen müssen im gesamten System eindeutig sein.
- Eine Änderung am Tippgeber-Standardsatz ändert keine bestehenden Verträge. Eine Änderung am Vertrag wirkt nur auf zukünftige Buchungen.
- Noch nicht abgerechnete Buchungen können mit Begründung aufgehoben werden. Sie bleiben sichtbar.
- Bei bereits abgerechneten Buchungen erzeugt „Gegenbuchung“ eine vollständige negative Buchung. Der bestehende Beleg bleibt unverändert. Teilstornos und automatische Stornohaftungsmodelle sind nicht implementiert.
- Eine negative Abrechnung stellt eine auszugleichende Rückforderung dar. „Zahlung / Ausgleich“ dokumentiert den Vorgang, führt aber keine Banktransaktion aus.
- Eine Abrechnung enthält alle offenen Buchungen des Tippgebers mit Buchungsdatum innerhalb des gewählten Zeitraums. Erneutes Abrechnen derselben Buchungen ist ausgeschlossen.
- Der Beleg ist eine **interne Provisionsabrechnung**. Umsatzsteuersätze, steuerliche Gutschriftenerfordernisse und individuelle rechtliche Vertragsregeln sind nicht konfiguriert. Die Software nimmt hierzu keine automatische Einstufung vor.

## Dokumente, Archivierung und Suche

Je Dokument maximal 20 MB. Dateien können in der Kundenakte abgelegt und optional einem Vertrag dieses Kunden zugeordnet werden. Downloads werden als Datei ausgeliefert; hochgeladener HTML-/Script-Inhalt wird nicht in der App ausgeführt.

Kunden und Tippgeber lassen sich archivieren. Bestehende Verknüpfungen und Abrechnungen bleiben erhalten. Neue Verträge für archivierte Kunden und neue Provisionen für archivierte Tippgeber werden abgewehrt. Datensätze können außerdem einzeln nach einer Bestätigung gelöscht werden; eine automatische Aufbewahrungs- oder Löschfrist ist nicht eingerichtet.

Beim Löschen einer Schadenakte werden ihre E-Mails und Dokumente einschließlich der gespeicherten Dateien entfernt. Ein Kunde mit Verträgen oder Schäden, ein Vertrag mit Schäden oder Provisionen und ein Tippgeber mit zugeordneten Verträgen, Provisionen oder Abrechnungen müssen zuerst von diesen Einträgen getrennt werden. Einzelne Dokumente und Schaden-E-Mails können direkt in der Akte gelöscht werden. Abgerechnete Provisionen können erst nach dem Löschen der zugehörigen Abrechnung gelöscht werden; dabei werden die betroffenen Provisionen wieder offen. Ein Löschvorgang wird im Änderungsprotokoll vermerkt. Gelöschte Daten lassen sich nur aus einer Sicherung wiederherstellen.

Kunden, Verträge und Provisionen sind durchsuchbar; Kunden, Verträge und Provisionen haben Statusfilter. Eine vollständige Datensicherung und CSV-Exporte sind unter Einstellungen verfügbar. CSV verwendet technische Spaltennamen, UTF-8 und Semikolon. Geldwerte stehen in Cent, `rate_bp` in Hundertstel-Prozent; `1500` bedeutet 15,00 %.

Verträge tragen die Herkunft „Eigen“ für Abschlüsse der eigenen Agentur oder „Fremd“ für Verträge anderer Agenturen bzw. Vermittler. Bei bestehenden Verträgen wird das Feld beim ersten Start automatisch mit „Eigen“ ergänzt und kann anschließend geändert werden.

Das Dashboard zeigt überfällige und innerhalb der nächsten 90 Tage anstehende Kündigungstermine aktiver oder beantragter Verträge. Zusätzlich erscheinen die Geburtstage aktiver Kunden der nächsten 30 Tage, einschließlich Alter und Kennzeichnung für heute bzw. morgen. Kunden ohne hinterlegtes Geburtsdatum und archivierte Kunden werden nicht angezeigt.

## Schadenanlage und Evidenz

Schäden können im Bereich „Schäden“, in einer Kundenakte oder direkt bei einem Vertrag angelegt werden. Jeder Fall erhält automatisch eine interne Nummer im Format `SCH-JAHR-LAUFNUMMER`. Nur der Kunde ist für einen Entwurf erforderlich; Vertrag, Ereignisdaten, Sachverhalt, Beträge, Versicherer-Schadennummer und weitere Angaben können später ergänzt werden.

Das Formular bietet allgemeine Schadenfelder sowie zusätzliche Angaben für Kfz, Sach/Haushalt/Gebäude, Haftpflicht, Unfall/Kranken, Rechtsschutz, Reise, Leben und Cyber/Betriebsunterbrechung. Diese Felder unterstützen die interne Erfassung; verbindlich sind im Einzelfall die Fragen und Unterlagen des jeweiligen Versicherers. Grundlage der Feldauswahl sind die üblichen Kerndaten zu Zeitpunkt, Ort, Hergang, Beteiligten, Dokumentation sowie Polizzen-/Schadennummer, wie sie unter anderem der [VVO](https://vvonet.vvo.at/vvonet_Informations_Beschwerdestelle) und die [Arbeiterkammer](https://arbeiterkammer.at/beratung/konsument/Versicherungen/Unwetterschaeden.html) nennen.

Offene Status sind Entwurf, Gemeldet, In Bearbeitung und Warten auf Unterlagen. Reguliert, Abgelehnt und Geschlossen gelten für die Dashboard-Zählung als abgeschlossen. Das Evidenzdatum und die zugehörige Aufgabe erscheinen auf dem Dashboard; überfällige Termine werden hervorgehoben. Offene Fälle ohne Evidenzdatum bleiben ebenfalls sichtbar.

Zu jeder Schadenakte lassen sich beliebig viele Dokumente bis 20 MB und E-Mail-Einträge ablegen. E-Mails können mit Richtung, Datum/Uhrzeit, Absender, Empfänger, CC, Betreff und Inhalt dokumentiert werden. Originale E-Mail-Dateien (`.eml`/`.msg`) und Anhänge werden als Dokument gespeichert. Alle Schadenunterlagen liegen lokal und sind Bestandteil der ZIP-Datensicherung. Schäden stehen zusätzlich als CSV-Export unter Einstellungen bereit.

## Sicherung und Wiederherstellung

Unter Einstellungen „Sicherung erstellen & herunterladen“ wählen. Zusätzlich wird beim Start an jedem neuen Tag eine Sicherung angelegt, sofern das CRM bereits eingerichtet ist. Wenn das Programm mehrere Tage durchläuft, erfolgt kein zusätzlicher zeitgesteuerter Sicherungslauf: in diesem Fall regelmäßig die manuelle Sicherung ausführen.

Die Datenbanksicherung nutzt die SQLite-Backup-API, einschließlich der aktuell bestätigten Daten im WAL-Modus. Sicherungen enthalten auch den Passwort-Hash und alle Dokumente. Sie sind **nicht verschlüsselt**. Eine Kopie auf demselben Rechner schützt nicht gegen Verlust oder Defekt dieses Rechners. Getrennten, geschützten Datenträger verwenden und alte Sicherungen selbst verwalten; sie werden nicht automatisch gelöscht.

Zur Wiederherstellung:

1. CRM vollständig beenden, auch bei Nutzung eines abweichenden Ports.
2. `Wiederherstellen.command` starten oder `python3 restore.py` im Projektordner aufrufen.
3. Pfad zur ZIP-Sicherung eingeben und mit `WIEDERHERSTELLEN` bestätigen.
4. Das Hilfsprogramm prüft die Datenbank, Verknüpfungen und Dokumentdateien. Die bisherigen Daten werden in `Daten-vor-Wiederherstellung-DATUM-UHRZEIT` aufbewahrt.
5. CRM neu starten. Das Passwort aus der Sicherung gilt wieder.

Nur eigene, vertrauenswürdige Sicherungen verwenden. Die Wiederherstellung ist auf Sicherungen bis 20 GB entpackter Größe begrenzt; größere Bestände benötigen einen angepassten Wiederherstellungsweg.

## Betriebsgrenzen

Die Version ist für **einen lokalen Arbeitsplatz mit einem Agenturzugang** ausgelegt. Sie bindet ausschließlich an `127.0.0.1`, akzeptiert nur lokale Hostnamen und prüft Ursprung und CSRF-Token bei Änderungen. Passwort-Hash mit PBKDF2-SHA256 (600.000 Iterationen); Sitzungscookie HttpOnly/SameSite Strict; Anmeldung läuft nach einer Stunde Inaktivität ab. Nicht mit Portfreigabe oder einem öffentlichen Proxy ins Internet stellen.

Datenbank und Dokumente liegen unverschlüsselt als lokale Dateien vor. Das Login schützt die Oberfläche, nicht gegen Personen mit Dateizugriff auf deinen Mac. Festplattenverschlüsselung und ein geschütztes macOS-Konto gehören zum lokalen Betrieb. Das Änderungsprotokoll ist ein lokales Anwendungsprotokoll, kein extern abgesichertes revisionsfestes Archiv.

Mehrbenutzerrollen, 2FA, E-Mail-/Versicherer-Schnittstellen, SEPA-Zahlungen, mobile Fernzugriffe und PostgreSQL-Serverbetrieb sind nicht Bestandteil dieser Version. Das Paket wurde funktional automatisiert geprüft, aber nicht durch einen unabhängigen Sicherheitsreview. Vor realem Einsatz zunächst mit eigenen Testfällen prüfen, insbesondere den individuellen Tippgeber-Vereinbarungen und der Erkennung verschiedener Polizzenlayouts.

## Entwicklung und Tests

Python 3.10+; ausschließlich Standardbibliothek. Oberfläche mit lokalem HTML, CSS und JavaScript, keine extern geladenen Schriften oder Skripte. Kein Paketmanager oder Build-Schritt notwendig.

```bash
python3 -m unittest discover -s tests -v
```

Die Tests nutzen ein temporäres Datenverzeichnis, nicht deinen Kundenbestand. Geprüft werden Zugangsschutz, Anlage und Änderung, Konflikterkennung, Rundung, doppelte Buchungen, unveränderte Abrechnungsbelege, Gegenbuchungen, Fixbeträge, Archivierung, Dokumente, Sicherung/Wiederherstellung und erneute Anmeldung.

Alternativer lokaler Port: `python3 app.py --port 8766`. Browser nicht automatisch öffnen: `python3 app.py --no-browser`. Optionales alternatives Datenverzeichnis über Umgebungsvariable `CRM_DATA_DIR`; der Standard bleibt `Daten` direkt im Projekt.

Bei prämienbasierten Tippgeber-Provisionen übernimmt der Server die Nettoprämie direkt aus dem Vertrag. Eine frei eingegebene Bruttobasis wird nicht verwendet. Bestandsverträge ohne Steueraufschlüsselung müssen zuerst ergänzt werden. Bereits gebuchte Provisionen und Abrechnungen bleiben unverändert.

## Dokumente direkt am Vertrag

- In der Vertragsliste öffnet „Dokumente (Anzahl)“ die Dateien des jeweiligen Vertrags.
- Im Vertragsformular zeigt „Vertragsdokumente“ bereits angehängte Dateien. Mit „Speichern & Dokumente anhängen“ werden zunächst die Vertragsänderungen gespeichert und anschließend die Dokumentablage geöffnet; das funktioniert auch bei neuen Verträgen.
- Datei auswählen und „Dokument anhängen“ drücken. Der Vertrag und dessen Kunde sind bereits fest zugeordnet. Weitere Dateien können danach nacheinander angehängt werden, jeweils bis 20 MB.
- Polizzen, Anträge und sonstige Dateien lassen sich direkt herunterladen. Sie erscheinen weiterhin in der Kundenakte und sind in der Datensicherung enthalten.

Das Kundenfeld im Vertragsformular ist ein durchsuchbares Dropdown: Namensbestandteile, Firma oder Kundennummer eingeben und einen Treffer auswählen. Der Pfeil öffnet die vollständige Liste. Pfeiltasten und Enter wählen einen Kunden aus; Escape schließt die Liste. Freitext allein gilt nicht als Kundenzuordnung.

Kunden können als „Natürliche Person“ oder „Juristische Person“ angelegt werden. Bei natürlichen Personen sind Vor- und Nachname erforderlich; bei juristischen Personen ist der Firmenname erforderlich und eine Kontaktperson optional. Bestehende Kundenakten werden beim Datenbank-Update als natürliche Personen weitergeführt. Firmenkunden erscheinen unter ihrem Firmennamen in Suche, Verträgen, Schäden und Auswahllisten; Geburtstage werden nur für natürliche Personen angezeigt.

## Intelligenter Import von Polizzen und E-Mails

Im Bereich „Import“ können PDF-Dateien, Scans und Bilder (`.pdf`, `.png`, `.jpg`, `.tif`, `.heic`) sowie gespeicherte E-Mails (`.eml`) und Textdateien bis 20 MB abgelegt werden. PDF-Text und Bildinhalt werden mit PDFKit und der Apple-Texterkennung Vision direkt auf dem Mac gelesen. Eine Internetverbindung oder Übertragung an einen KI-/Cloud-Dienst findet nicht statt.

Dateien können über die Dateiauswahl oder per Drag-and-drop auf die Importfläche gelegt werden. Beim direkten Ziehen aus Outlook oder Apple Mail übernimmt das CRM eine vom Mailprogramm bereitgestellte `.eml`-Datei oder ersatzweise den lesbaren Nachrichtentext. Dieser Text-Fallback enthält keine Mail-Anhänge. Manche Outlook-Versionen übergeben dem Browser nur ein internes Mailobjekt oder eine `.msg`-Datei; diese Nachricht muss zuerst als `.eml` gespeichert oder über den Schreibtisch/Finder als Datei abgelegt werden. Das CRM zeigt dafür einen konkreten Hinweis an.

Das CRM erkennt unter anderem den Kundentyp, Personen- oder Firmennamen, E-Mail, Telefon, Anschrift, Polizzennummer, Versicherer, Sparte, Vertragsdaten, Prämie und Zahlweise. Gängige Rechtsformen wie GmbH, AG, SE, OG, KG, e.U., Genossenschaft, Verein und Stiftung werden als Firmenkunde erkannt und können vor der Übernahme korrigiert werden. Bei Polizzen wird zuerst die Polizzennummer mit dem Vertragsbestand verglichen. Existiert sie bereits, wird die Datei direkt diesem Vertrag und dessen gespeichertem Kunden zugeordnet; erkannte Personendaten können diese Verbindung nicht verändern. Andernfalls werden zuerst die Daten für einen neuen Vertrag geprüft und erst anschließend ein bestehender oder neuer Kunde verknüpft.

Kundenkontaktdaten werden nur aus einem strukturierten Abschnitt zum Versicherungsnehmer gelesen; allgemeine Textstellen sowie E-Mail-Adressen des Versicherers, Maklers oder Servicecenters werden nicht als Kundenadresse übernommen. Überschriften wie „VersicherungsnehmerIn“ haben Vorrang vor bloßen Erwähnungen im Fließtext. Das CRM vergleicht exakte E-Mail-Adressen, Telefonnummern und Polizzennummern sowie vertauschte oder teilweise übereinstimmende Namen, Firma, PLZ, Ort und Anschrift mit dem Bestand. Vor jeder Übernahme erscheint eine Prüfmaske mit bis zu fünf möglichen Bestandstreffern und den Gründen für den Treffer. Erst „Geprüft übernehmen“ legt einen Kunden oder Vertrag an und speichert die Originaldatei als Anhang. Bei einem bestehenden Kunden bleiben die Stammdaten standardmäßig vollständig unverändert; optional können ausschließlich bisher leere Kontakt- und Adressfelder ergänzt werden.

Bei erkannten österreichischen Sparten wird die Versicherungssteuer eines neu importierten Vertrags aus der angegebenen Bruttoprämie automatisch aufgeschlüsselt. Bei unbekannten Sparten bleibt die Steuer zunächst unaufgeschlüsselt und kann anschließend im Vertragsformular manuell ergänzt werden. Texterkennung ist abhängig von Scanqualität und Dokumentlayout; besonders Namen, Beträge, Prämienzeitraum und Kündigungstermine müssen in der Prüfmaske kontrolliert werden. Outlook-`.msg`-Dateien werden nicht ausgelesen; diese bitte zuvor als `.eml` speichern.

### Betrieb auf einem Server

Auf einem macOS-Server wird weiterhin PDFKit/Vision verwendet. Auf einem Linux-Server nutzt das CRM automatisch `pdftotext` für Text-PDFs sowie Tesseract und `pdftoppm` für Bilder und gescannte PDFs. Dafür müssen die Pakete `tesseract-ocr`, das deutsche Sprachpaket `tesseract-ocr-deu` und `poppler-utils` auf dem Server installiert sein. Die Erkennung läuft dann vollständig auf diesem Server; ein externer KI-Dienst ist nicht notwendig.

Vor einer öffentlichen Bereitstellung braucht die bisherige Einzelplatzversion zusätzlich eine abgesicherte Serverkonfiguration mit HTTPS, Benutzer- und Rollenverwaltung, verschlüsselter bzw. geschützter Datenablage, regelmäßigen externen Sicherungen und Größen-/Zeitlimits für Uploads. Die aktuelle Bindung an `127.0.0.1` ist bewusst nur für lokale Nutzung vorgesehen.
