# Reineke-RAG — Benutzerhandbuch

> Dieses Handbuch richtet sich an **Sie, die Nutzerin bzw. den Nutzer von Reineke-RAG**: Anmelden, Fragen zu Firmendokumenten stellen, Antworten mit Zitaten lesen und — sofern Ihre Rolle es erlaubt — Dokumente hochladen.
>
> Sie brauchen **keine** technischen Vorkenntnisse. Dies ist ein Anwenderhandbuch. Wenn Sie Administratorin sind und Informationen zu Installation, Backup oder Tuning suchen, lesen Sie stattdessen [TECHNICAL_HANDBOOK_DE.md](TECHNICAL_HANDBOOK_DE.md).

---

## Inhalt

1. [Was Reineke-RAG ist — in einer Minute](#1-was-reineke-rag-ist--in-einer-minute)
2. [Was es tut und was nicht](#2-was-es-tut-und-was-nicht)
3. [Erstmaliges Anmelden](#3-erstmaliges-anmelden)
4. [Ein Rundgang durch das Fenster](#4-ein-rundgang-durch-das-fenster)
5. [Gute Fragen stellen](#5-gute-fragen-stellen)
6. [Eine Antwort lesen (Zitate zählen)](#6-eine-antwort-lesen-zitate-zählen)
7. [Sonderfall: Tabellen und Zahlen](#7-sonderfall-tabellen-und-zahlen)
8. [Nachfragen im Gespräch](#8-nachfragen-im-gespräch)
9. [Dokumente hochladen](#9-dokumente-hochladen)
10. [Was Sie sehen dürfen — Berechtigungen](#10-was-sie-sehen-dürfen--berechtigungen)
11. [Datenschutz und Protokollierung](#11-datenschutz-und-protokollierung)
12. [Wenn etwas schiefgeht](#12-wenn-etwas-schiefgeht)
13. [Häufige Fragen](#13-häufige-fragen)
14. [Eine kurze Übung — Ihre erste gute Frage](#14-eine-kurze-übung--ihre-erste-gute-frage)
15. [Glossar](#15-glossar)
16. [Hilfe bekommen](#16-hilfe-bekommen)

---

## 1. Was Reineke-RAG ist — in einer Minute

Reineke-RAG ist der **interne Dokument-Assistent Ihres Unternehmens**.

Sie tippen eine Frage. Das System liest die Word-, PDF- und Excel-Dateien, auf die Sie Zugriff haben, findet die relevanten Stellen und schreibt eine Antwort — in Deutsch oder Englisch — mit **klickbaren Quellen**, damit Sie jede Aussage überprüfen können.

Es läuft komplett auf Unternehmens-Hardware. **Nichts verlässt das Haus.**

Stellen Sie sich eine Kollegin vor, die alle Dateien gelesen hat, auf die Sie Zugriff haben, und Ihnen immer sagt, wo eine Information steht.

---

## 2. Was es tut und was nicht

### Es tut

- Fragen zu internen PDF-/Word-/Excel-Dateien beantworten.
- Deutsch **und** Englisch verstehen — fragen Sie in der Sprache, die Ihnen natürlicher fällt.
- Die **Quelle** jeder Aussage zeigen (Dateiname, Seite, eine Vorschau, einen Link zum Öffnen).
- Tabellen numerisch verarbeiten — *„Welches Projekt hatte 2024 die höchste Marge?"* erzeugt eine wirklich aus Daten errechnete Antwort, keine Schätzung.
- Ihre Zugriffsrechte respektieren. Sie sehen und durchsuchen nur die Ordner, auf die Ihre Gruppe Zugriff hat.

### Es tut nicht

- Im Internet recherchieren. Keine Wikipedia, keine News, keine Websuche. Nur Ihre Dateien.
- E-Mails schreiben oder versenden, Kalender bearbeiten, Fremdsysteme ansprechen. Es ist ausschließlich lesend.
- Ihre Dokumente verändern. Es liest sie; die Originale werden nie angefasst.
- Erfinden. Findet kein zugängliches Dokument die Antwort, sagt es das ausdrücklich, anstatt zu raten.
- Sie standardmäßig zwischen Gesprächen erinnern — jedes Chatfenster beginnt frisch, wenn Sie kein laufendes Gespräch fortführen.

### Wenn Ihre Erwartung außerhalb dieser Grenzen liegt

Fragen Sie Ihre Administratorin. In der Regel ist es eines von drei:

- **Ein geplantes Feature**, das in der aktuellen Version noch nicht enthalten ist.
- **Eine Konfigurationseinstellung**, die der Admin für Sie anpassen kann.
- **Absichtlich deaktiviert** aus Sicherheitsgründen (z. B. Dateianhänge im Chat, die Ordner-Berechtigungen umgehen würden).

---

## 3. Erstmaliges Anmelden

1. Öffnen Sie die URL, die Ihr Administrator geteilt hat. Sie hat meist die Form `https://rag.<ihre-firma>.local`.
2. Sie werden auf die **Single-Sign-On-Seite** (Markenname „Authentik") umgeleitet. Melden Sie sich mit Ihren üblichen Firmendaten an.
3. Beim ersten Login werden Sie aufgefordert, Ihr **Passwort zu ändern**. Eventuell registrieren Sie zusätzlich einen **zweiten Faktor** (Authenticator-App) — tun Sie es; es dauert eine Minute.
4. Nach dem Login landen Sie im Chat-Fenster.

### Zertifikatswarnung beim ersten Besuch?

Frisch installiert kann Ihr Browser vor dem Zertifikat warnen. Ursache: das System nutzt eine **unternehmensinterne Zertifizierungsstelle**. Ihr Admin wird entweder:

- das CA-Zertifikat auf Ihrem Rechner installieren (danach verschwindet die Warnung), **oder**
- Ihnen mitteilen, dass die Warnung einmalig ignoriert werden kann (nur am Arbeitsrechner, niemals im öffentlichen WLAN).

Sehen Sie diese Warnung auf einem Smartphone oder zuhause, **nicht ignorieren**. Fragen Sie Ihren Admin.

---

## 4. Ein Rundgang durch das Fenster

Vereinfachte Skizze:

```
┌────────────────────────────────────────────────────────────┐
│  Reineke-RAG                         [DE | EN]   [Sie ▾]  │
├───────────────────────┬────────────────────────────────────┤
│  Konversationen       │   assistant: ...                   │
│  + Neu                │                                    │
│  Heute                │   [1] DIN-18065.pdf · S. 3         │
│   · Lieferfristen     │   [2] Angebot-2024-09.docx · §2.1  │
│                       │                                    │
│  Gestern              │                                    │
│   · Prozesshandbuch   │                                    │
│                       │                                    │
│                       ├────────────────────────────────────┤
│                       │  ▸ Frage tippen…               ↵   │
└───────────────────────┴────────────────────────────────────┘
```

### Elemente

- **Konversationsliste (links)** — Ihre vergangenen Chats. Ihre Konversationen sind **privat zu Ihrem Konto**; Administratoren sehen Audit-Metadaten (wer hat wann was gefragt), aber standardmäßig nicht das Chat-Transkript.
- **+ Neu** — startet eine frische Konversation ohne Erinnerung an frühere. Verwenden Sie dies bei Themenwechsel.
- **Sprachumschalter oben rechts (DE | EN)** — schaltet die Systemtexte um. Das System antwortet immer in der Sprache, in der Sie fragen; der Toggle beeinflusst vor allem Ablehnungsmeldungen und einige UI-Texte.
- **Zitate ([1], [2], …)** — kleine Klammern innerhalb der Antwort. Ein Klick öffnet eine Vorschau mit Dateinamen, Seite und kurzem Auszug. Zweiter Klick öffnet die ganze Datei.
- **Eingabefeld (unten)** — Ihre Frage eingeben; ↵ drückt ab. Streaming: die Antwort erscheint Wort für Wort, während das System sie generiert.

### Was Sie bewusst nicht sehen

- Eine Modellauswahl. Das System wählt je nach Fragetyp automatisch das passende Sprachmodell. Sie müssen nichts auswählen.
- Einen Datei-Upload-Button im Chat. Uploads laufen über einen separaten, berechtigungsbewussten Pfad (§ 9).

---

## 5. Gute Fragen stellen

Reineke-RAG ist klug, aber Ihre Frage macht immer noch den Unterschied. Diese Muster führen verlässlich zu besseren Antworten.

### 5.1 Vier Fragetypen, jeweils mit gutem Muster

| Typ | Gutes Muster | Deutsches Beispiel | Englisches Beispiel |
|-----|--------------|--------------------|---------------------|
| **Lookup** (kurz, spezifisch) | Thema mit präzisen Begriffen nennen | *„Welche Norm gilt für Typ-B-Schränke?"* | *„Which standard applies to type-B cabinets?"* |
| **Extraktion** (Liste aus einer Datei holen) | „Liste / Extrahiere alle X aus Y" | *„Liste alle Lieferfristen aus Angebot-2024-09.pdf."* | *„Extract all delivery deadlines from Offer-2024-09.pdf."* |
| **Tabelle / Zahl** (Berechnung) | Datei nennen + was berechnet werden soll | *„Welches Projekt in Projekte2024.xlsx hatte die höchste Marge?"* | *„In Projects2024.xlsx, which project had the highest margin?"* |
| **Synthese** (über viele Dateien) | „Fasse zusammen / Vergleiche …" | *„Fasse unsere Position zu Thema X über alle QMS-Dokumente zusammen."* | *„Summarise our position on topic X across all QMS documents."* |

### 5.2 Tricks, die wirklich helfen

- **Datei nennen**, wenn Sie sie wissen. Das ist wie ein Cheat-Code — das Retrieval rastet sofort darauf ein.
- **Ordner nennen**, wenn Sie nur ungefähr wissen, wo es herkommt („in unseren QMS-Dokumenten…").
- **Produktnamen und Bestellnummern wörtlich verwenden.** Das System hat Keyword-Suche; `KR-4711-B` bringt mehr als *„das 4711-Teil"*.
- **Eine Frage pro Nachricht.** Bei heiklen Themen eine Sache fragen, dann nachsetzen.
- **Bei Zahlen: die Rechnung explizit benennen** — „höchste", „Summe", „Mittelwert wo X > 10". Das System schreibt im Hintergrund eine kleine Datenbankabfrage.

### 5.3 Muster, die zu vermeiden sind

- *„Was hältst du von …"* — wird nichts halten. Es berichtet, was Dokumente sagen, keine Meinungen.
- *„Erzähl mir alles zu …"* — zu breit; Sie erhalten einen rauschigen Mix. Einschränken.
- *„Recherchiere im Web nach …"* — kein Internet, prinzipiell.
- *„Fasse die letzten fünf hochgeladenen PDFs zusammen"* — das System sortiert nicht nach Datum in Alltagsabfragen. Bei Bedarf an den Admin wenden.

### 5.4 Sprachwahl

Das System versteht DE und EN. Wenn Sie die **Antwort** in einer bestimmten Sprache wünschen, sagen Sie es:

- *„Antworte auf Deutsch."*
- *„Answer in English."*

Oder nutzen Sie den DE | EN-Toggle oben rechts.

---

## 6. Eine Antwort lesen (Zitate zählen)

Jede Tatsachenaussage *sollte* am Ende eine Klammerzahl wie **[1]** oder **[2]** tragen. Diese sind **klickbare Zitate**.

Beispiel:

> *„Die Norm DIN 18065 regelt die Abmessungen [1]. Der Mindestwert beträgt 800 mm [2]."*

[1] und [2] sind jeweils unabhängig klickbar. Ein Klick zeigt:

- Den Dateinamen.
- Die Seite oder den Abschnitt.
- Eine rund 240-Zeichen-Vorschau der genauen Textstelle.
- Einen Link, um die gesamte Datei zu öffnen (im Rahmen Ihrer Berechtigungen).

### Daumenregeln für das Lesen von Antworten

- **Kein Zitat an einer Aussage = schwach werten.** Manchmal setzt das System das Zitat erst am Absatzende statt an jeden Satz. Bei wichtigen Aussagen klicken und prüfen.
- **Unterstützt die zitierte Vorschau die Aussage nicht, ist die Antwort falsch.** Selten, aber es kommt vor. Melden Sie es an Ihren Admin — die Logs können die Anfrage rekonstruieren und das System verbessern.
- **„Ich habe dazu in den zugänglichen Dokumenten keine Information gefunden."** ist eine **Funktion**, kein Bug. Es heißt: nichts in den Ihnen zugänglichen Dokumenten stützt eine Antwort. Vertrauen Sie dem. Versuchen Sie nicht, die Ablehnung durch Umformulierungen zu umgehen.

### Zur Sprache

Die Antwortsprache entspricht in der Regel Ihrer Frage. Weicht sie ab (Sie fragen auf Deutsch, erhalten Englisch), hängen Sie einfach „Antworte auf Deutsch." an oder nutzen Sie den DE | EN-Toggle.

### Zur Länge

Das System wählt die Länge nach Fragetyp:

- Lookup → kurz, direkt.
- Extraktion → strukturiert, oft als Liste.
- Tabellen-Mathematik → knappe Antwort + (optional) die verwendete SQL-Abfrage.
- Synthese → länger, mehrere Absätze.

Sie können jederzeit mehr Tiefe anfordern: *„Ausführlicher bitte."* / *„Give me more detail."*

---

## 7. Sonderfall: Tabellen und Zahlen

Fragen Sie nach einer **Tabelle** oder einer **Zahl**, macht das System etwas anderes:

- Es schreibt eine kleine **SQL-Abfrage** gegen die Daten.
- Es führt die Abfrage sicher aus — Sie können selbst kein SQL laufen lassen; nur das System kann das, und ausschließlich gegen Tabellen, auf die Sie Berechtigung haben.
- Die zurückgegebenen Zeilen werden Teil der Antwort.

Für Qualitätsprüfung gibt es meist ein „SQL anzeigen"-Panel — klicken Sie es an, um die genaue Abfrage zu sehen. Vergleichen Sie sie mit der Datei, wenn die Zahl seltsam wirkt.

**Warum das wichtig ist:** Ein LLM, das Zellen direkt summieren soll, macht häufig kleine Rechenfehler — insbesondere bei deutschen Dezimaltrennzeichen (`,` vs. `.`). Der SQL-Pfad vermeidet das komplett.

### Typische Tabellen-Fragen

- *„Wie hoch ist die Summe der Kosten im Projekt Alpha?"*
- *„Welche Position hatte die längste Lieferfrist?"*
- *„Durchschnittliche Marge pro Quartal, 2024."*
- *„Wie viele Bestellungen über 10 000 € gingen 2024 Q2 an Lieferant X?"*

**Tipp:** den Dateinamen (`.xlsx`) möglichst immer nennen — das macht das Routing auf den SQL-Pfad sehr zuverlässig.

---

## 8. Nachfragen im Gespräch

Das System erinnert sich an die aktuelle Konversation. Nach:

> *„Welche Norm gilt für Typ-B-Schränke?"*

können Sie nachfragen:

> *„Und welche Mindestanforderung an die Tiefe?"*

Das System versteht, dass Sie beim selben Thema sind.

**Zurücksetzen:** Klick auf **+ Neu** oben links. Damit beginnt eine frische Konversation ohne Erinnerung. Tun Sie dies bei jedem Themenwechsel — es verhindert, dass das System vom vorherigen Thema verzerrt wird.

Nachfragen ist nach großen Antworten besonders stark:

- *„Zeig mir nur Punkt 3."*
- *„Übersetze das ins Englische."*
- *„Woher stammt diese Zahl genau?"*

---

## 9. Dokumente hochladen

Nicht jede Nutzerin darf hochladen. Sehen Sie kein **Upload** in der Seitenleiste, ist Ihre Gruppe nicht berechtigt. Bitten Sie Ihren Admin um Zugang zu einem konkreten Ordner.

### 9.1 So laden Sie hoch

1. Klicken Sie **Upload** in der linken Seitenleiste.
2. Wählen Sie im Dropdown den **Zielordner**. Es werden nur Ordner angezeigt, auf die Ihre Gruppe Schreibrechte hat — z. B. `/qms/normen`.
3. Ziehen Sie Dateien hinein. Unterstützte Formate: `.pdf`, `.docx`, `.doc`, `.xlsx`, `.xls` sowie `.pptx`, `.html`, `.md`, wenn der Admin sie aktiviert.
4. Klicken Sie **Ingest**.
5. Die Datei erscheint unter „In Progress" mit Live-Status: `queued → parsing → embedding → indexed`.
6. Sobald **indexed**, ist die Datei suchbar. Typische Zeiten:
   - Text-PDF: ca. 10 Sekunden pro Seite.
   - Gescanntes PDF (OCR nötig): ca. 30 Sekunden pro Seite.
   - Word / Excel: meist unter einer Minute.

### 9.2 Was geschieht mit Ihrer Datei

- Das **Original** wird unverändert und versioniert gespeichert.
- Eine **geparste, gechunkte, suchbare Kopie** wird für das Retrieval angelegt. Beim Klick auf ein Zitat sehen Sie weiterhin das Original.
- **Nichts verlässt das Haus.** Kein Cloud-Aufruf, keine API, keine Telemetrie.

### 9.3 Ein Dokument löschen

- Klicken Sie das Papierkorb-Symbol neben der Datei → bestätigen.
- Standard ist **Soft-Delete**: die Datei wird als „superseded" markiert und verschwindet aus der Suche, die Bytes bleiben 30 Tage für Audit-Rücknahmen erhalten.
- Ein **Hard-Delete** (endgültig) ist Admin-Aktion.

### 9.4 Ein Dokument durch eine neue Version ersetzen

Laden Sie eine gleichnamige Datei in denselben Ordner hoch. Hat sich der Inhalt geändert, entsteht eine neue Version; die alte wird als `superseded` markiert. Ab diesem Moment nutzt der Index die neue Version.

### 9.5 Keine Geheimnisse hochladen

Das System protokolliert Anfrage-Metadaten fürs Audit. Es ist für Dokumente gedacht, nicht für Passwörter oder Private Keys. Nutzen Sie dafür Ihren Passwort-Manager. Ordner-ACLs schützen *Zugriff*, nicht die Geheimhaltung des Dokument-Inhalts — enthält Ihre XLSX ein Geheimnis, kann es jede Person finden, die den Ordner lesen darf.

---

## 10. Was Sie sehen dürfen — Berechtigungen

Jedes Dokument liegt in einem **Ordner** (logisch, z. B. `/qms/normen` oder `/sales/angebote`). Jeder Ordner ist mit einer Liste **Gruppen** konfiguriert, die lesen dürfen.

### Ihr Blick auf die Welt

- Sie gehören einer oder mehreren Gruppen an (typisch: `engineering`, `sales`, `qms`, `finance`, `hr`).
- Sie sehen ein Dokument nur, wenn **mindestens eine** Ihrer Gruppen auf der Freigabeliste des Ordners steht.
- Die Durchsetzung geschieht zur Retrieval-Zeit. Fragen Sie nach etwas aus einem Ordner, auf den Sie keinen Zugriff haben, erhalten Sie die Antwort **„keine Information gefunden"** — **kein Hinweis darauf, dass ein solches Dokument existiert**. Das ist Absicht — es verhindert, dass die bloße Existenz sensibler Dateien durchsickert.

### Ihre Kollegin sieht evtl. Anderes

Zwei Kolleg:innen können dieselbe Frage stellen und unterschiedliche Antworten bekommen, weil sie in unterschiedlichen Gruppen sind. Das ist korrektes Verhalten, kein Bug.

### Neue Zugänge beantragen

Sind Sie der Meinung, auf einen Ordner Zugriff zu brauchen, ihn aber nicht zu haben, sprechen Sie den Admin an. ACL-Änderungen propagieren innerhalb ~1 Minute.

---

## 11. Datenschutz und Protokollierung

Transparenz ist Teil des Designs. Hier exakt, was gespeichert wird und was nicht:

### Protokolliert (Admin-sichtbare Audit-Spur)

- Ihre Benutzer-ID.
- Zeitstempel.
- Der **Fragetext**.
- Welche Dokument-IDs das System für Ihre Anfrage abgerufen hat.
- Welches Sprachmodell geantwortet hat.
- Latenz und Token-Zählungen.
- Ein Hash der Antwort (für Manipulations-Erkennung, nicht die Antworttexte selbst).

### Nicht protokolliert (standardmäßig)

- Ihre Konversationshistorie über die genannten Metadaten hinaus ist **privat zu Ihrem Konto** im UI.
- Administratoren sehen Audit-Metadaten, aber das Chat-Transkript standardmäßig nicht — es sei denn, Ihre Organisation hat eine andere Policy gewählt. Sprechen Sie bei Bedarf mit Ihrem Admin oder Ihrer Datenschutzbeauftragten.

### Aufbewahrung

- Audit-Log: gemäß Unternehmens-Policy, typisch **180 Tage**.
- Chat-Verlauf in Ihrem Konto: solange Sie nicht löschen.
- Originaldokumente: unbefristet, nach Aktenhaltungs-Policy Ihres Unternehmens.

### Ihre Rechte (DSGVO / BDSG)

Sie können beantragen:

- Eine Kopie Ihrer Audit-Einträge.
- Löschung Ihrer Audit-Einträge (vorbehaltlich etwaiger gesetzlicher Aufbewahrungspflicht).
- Eine Liste Ihrer Konversationen.

Richten Sie das an Ihre **Datenschutzbeauftragte**.

### Die Offline-Garantie

Zur Laufzeit werden keine ausgehenden Netzverbindungen aufgebaut. Das System wurde mit gezogenem Netzkabel geprüft; es degradiert anstandslos und ruft nichts Externes auf. Ihre Anfragen, Ihre Uploads, Ihre Antworten — **bleiben im Haus**.

---

## 12. Wenn etwas schiefgeht

| Symptom | Wahrscheinlicher Grund | Probieren Sie |
|---------|-----------------------|---------------|
| *„Ich finde ein Dokument nicht, von dem ich weiß, dass es existiert."* | Ihre Gruppe steht nicht auf der Freigabeliste des Ordners. | Admin ansprechen. |
| *„Antwort ist falsch, aber zitiert."* | Das abgerufene Stück wirkt verwandt, ist aber nicht relevant; oder das Dokument widerspricht sich selbst. | Umformulieren; Datei oder Abschnitt nennen; bei Wiederholung melden. |
| *„Antwort hat keine Zitate."* | Selten — das Modell hat sie ausgelassen. | Frage erneut stellen. Bleibt es, melden. |
| *„Antwortet in falscher Sprache."* | Modell-Sprach-Erkennung. | „Antworte auf Deutsch." / „Answer in English." anhängen oder DE | EN umschalten. |
| *„Eine Tabellen-Zahl wirkt verkehrt."* | Der SQL-Pfad hat evtl. die falsche Spalte gewählt. | „SQL anzeigen" ausklappen, Spaltennamen prüfen. Abweichungen melden. |
| *„Es dauert ewig."* | Eine Synthese-Frage kann bis zu einer Minute brauchen. | Streaming beobachten — es beginnt, bevor es fertig ist. Oder die Frage teilen. |
| *„Ich habe eine Datei hochgeladen, nichts passiert."* | Queue ausgelastet oder Parser-Fehler. | Status anschauen; bei „failed" Info-Symbol lesen; Admin kontaktieren. |
| *„Ich logge mich ein und sehe keine Dokumente."* | Ihr Konto hat evtl. noch keine Gruppen. | Admin um passende Gruppen-Zuordnung bitten. |

### So melden Sie sinnvoll an den Admin

Mitschicken:

1. Einen **Link zur Konversation** (URL aus der Adresszeile kopieren).
2. Die **genaue Frage**, die Sie gestellt haben.
3. Die **erwartete** Antwort vs. das, was Sie bekommen haben.
4. Nach Möglichkeit: ein **Zitat angeklickt** und notiert, ob die Vorschau die Aussage stützt oder nicht.

Ihr Admin hat Werkzeuge (Langfuse, Audit-Log), die aus Konversations-URL und Zeitstempel die Anfrage end-to-end rekonstruieren können.

---

## 13. Häufige Fragen

**F: Kann ich Reineke-RAG nutzen, um eine Antwort an eine Kundin zu schreiben?**
Ja — es ist ein nützliches Werkzeug für Entwürfe. Aber **Sie** sind für das Verantwortlich, was Sie hinausschicken. Prüfen Sie Fakten und Zitate vor jeder Außenkommunikation.

**F: Kann es eine Datei lesen, die ich im Chat anhänge?**
Nein. Dateien kommen nur über den offiziellen **Upload**-Weg in das System, der Ordner-Berechtigungen durchsetzt. Chat-Anhänge würden das umgehen — bewusst nicht unterstützt.

**F: Warum hat es eine Antwort verweigert?**
Weil nichts in den Ihnen zugänglichen Dokumenten eine Antwort stützt. Das ist beabsichtigt — das System bevorzugt „Ich weiß es nicht" über Erfundenes.

**F: Warum unterscheidet sich die Antwort meines Kollegen bei derselben Frage?**
Unterschiedliche Gruppen-Mitgliedschaften → unterschiedliche zugängliche Dokumente → unterschiedliche Zitate → manchmal unterschiedliche Antworten. Designgewollt.

**F: Lernt es aus meinen Korrekturen?**
Nicht automatisch. Das Retrieval passt sich standardmäßig nicht an vergangene Anfragen an (Datenschutz-Wahl). Admins können Prompts und Reranker-Gewichte anhand von Audit-Trends tunen; Verbesserungen zeigen sich leise.

**F: Welche Sprachen beherrscht es?**
Deutsch und Englisch sind vollständig unterstützt und evaluiert. Andere Sprachen können funktionieren (der Embedder deckt 100+ ab), sind aber nicht formal getestet.

**F: Kann es übersetzen?**
Ja. *„Übersetze §2.1 von Prozesshandbuch.pdf ins Englische."* — es zitiert die Quelle und übersetzt. Für rechtsverbindliche Übersetzungen bleibt eine menschliche Übersetzung sinnvoll.

**F: Kann es einen ganzen Ordner zusammenfassen?**
Ja, im Rahmen: *„Fasse die Kernpunkte aller Dokumente unter /qms/ zusammen."* Sehr breite Fragen werden unpräziser — engere Prompts sind immer besser.

**F: Gibt es eine Größenbeschränkung für Uploads?**
Praktisch ja — sehr große gescannte PDFs brauchen lange für OCR. Bei Unsicherheit den Admin fragen.

**F: Kann ich einen Chat exportieren?**
Kopieren/Einfügen geht. Formaler Export ist in v1 nicht enthalten.

**F: Sind meine Daten sicher?**
Alles läuft auf Firmen-Hardware. Keine Cloud. Die Kommunikation zwischen Ihrem Browser und dem Server ist verschlüsselt (HTTPS). Der Zugriff richtet sich nach Ihrem SSO-Konto und Ihren Gruppen.

---

## 14. Eine kurze Übung — Ihre erste gute Frage

Probieren Sie das zur Eichung:

1. Wählen Sie ein **konkretes Dokument**, das Sie kennen — z. B. `Angebot-2024-09.pdf`.
2. Fragen Sie: *„Liste alle Lieferfristen aus Angebot-2024-09.pdf."*
3. Lesen Sie die Antwort. Klicken Sie **[1]**. Passt die Vorschau?
4. Fragen Sie nach: *„Für welche Position ist die Frist am kürzesten und warum?"*
5. Öffnen Sie das Zitat für das „Warum" — steht die Begründung tatsächlich im Dokument?

Wenn Schritt 3 und 5 beide bestätigen, haben Sie den Dreh raus. Der Rest ist Übung.

---

## 15. Glossar

- **Chunk** — ein kleines Stück eines Dokuments (ein halber bis ein Seite), das das System intern für die Suche nutzt. Sie sehen Chunks nie direkt; sie sind die Einheit hinter einem Zitat.
- **Zitat** — die `[1]`, `[2]`-Verweise in der Antwort. Klicken öffnet die Quelle.
- **Ordner** — eine logische Kategorie wie `/qms/normen`. Bestimmt, wer welche Dokumente lesen darf.
- **Gruppe** — eine Markierung (z. B. `engineering` oder `qms`), die Ihr Admin an Ihr Konto hängt. Bestimmt, was Sie sehen dürfen.
- **Hybrid-Retrieval** — das System nutzt gleichzeitig semantische Suche und Stichwortsuche. Deshalb funktionieren `KR-4711-B` und *„Schraubverbindung"* beide.
- **OCR** — Optical Character Recognition. Wandelt gescannte Textbilder in durchsuchbaren Text zurück.
- **Refusal / Ablehnung** — wenn das System *„Ich habe dazu in den zugänglichen Dokumenten keine Information gefunden."* sagt. Bewusstes Verhalten, kein Fehler.
- **SQL-Pfad** — für numerische Fragen schreibt und führt das System eine kleine Datenbankabfrage aus, statt das Sprachmodell rechnen zu lassen.
- **SSO** — Single Sign-On. Ein Firmen-Login für alles.
- **System-Prompt** — unsichtbare Anweisungen, die das Modell veranlassen, alles zu zitieren und nichts zu fabrizieren. Sie sehen ihn nicht, aber er formt jede Antwort.

---

## 16. Hilfe bekommen

- **Admin-Kontakt:** steht meist im **ℹ Über**-Panel in der UI; Ihr Admin hinterlegt ihn beim Setup.
- **Statusseite:** ein Grafana-Dashboard unter `https://rag.<firma>.local/grafana/d/overview` zeigt die Systemgesundheit. Admins beobachten es; Endnutzer dürfen reinschauen.
- **Bug oder Wunsch:** über das übliche Ticket-System Ihrer Firma. Die **URL der betreffenden Konversation** deutlich dazuschreiben — es beschleunigt die Triage enorm.
- **Datenschutzfragen:** an die **Datenschutzbeauftragte**, nicht den IT-Admin.

---

*Willkommen bei Reineke-RAG. Stellen Sie gute Fragen, prüfen Sie die Zitate, und betrachten Sie eine Ablehnung als Information — nicht als Versagen.*

---

**Verwandte Dokumente:**

- [TECH_DESCRIPTION_DE.md](TECH_DESCRIPTION_DE.md) — was Reineke-RAG technisch ist
- [TECHNICAL_HANDBOOK_DE.md](TECHNICAL_HANDBOOK_DE.md) — für Administratoren
- [docs/03_HANDBOOK.md](docs/03_HANDBOOK.md) — das mitgelieferte Endnutzer-Handbuch (autoritative Quelle für UI-Texte)
