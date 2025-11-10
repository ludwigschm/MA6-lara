Folgende Pakete müssen installiert werden: 

kivy	
pandas
numpy	
opencv-contrib-python
sounddevice


pip install kivy pandas numpy opencv-contrib-python sounddevice

## Eye-Tracking aktivieren

1. Echtzeit-API installieren:

   ```bash
   pip install pupil-labs-realtime-api
   ```

2. Tracker-Hosts hinterlegen. Entweder eine Datei `tracker_hosts.txt` im
   Projektverzeichnis anlegen oder die Hosts direkt im UI eintragen.

   Beispielinhalt für `tracker_hosts.txt`:

   ```
   192.168.0.20
   192.168.0.21
   ```

3. Anwendung starten:

   ```bash
   python bluffing_eyes.py
   ```

4. Smoke-Test ausführen:

   ```bash
   python -m scripts.neon_smoke
   ```

   Im Device-Recording sollten die Marker `fix.start`, `fix.beep` und
   `trial.start` erscheinen. Die Events werden mit der Gerätezeit gestempelt,
   Host-Zeitstempel müssen nicht übermittelt werden.

## Testanleitung

1. Zwei Tracker-Instanzen (oder Mocks) mit HTTP-Endpunkten `/api/status` und
   `/api/start_stream` bereitstellen, die im Statusfeld `state`, `mode`, `frame`
   oder `fps` zurückgeben.
2. Anwendung mit `python bluffing_eyes.py` starten, das Tracker-Dialogfenster
   öffnen und sicherstellen, dass die Eingabefelder jederzeit fokussierbar
   bleiben.
3. Session und Block im Startdialog setzen. In der Konsole erscheinen pro
   Tracker die Meldungen `status ok` und – nach dem Startbefehl – `streaming ok`.
4. Über den Play-Button den Start auslösen. Beobachten, dass VP1 sofort startet
   und VP2 exakt 2 s später folgt (Log-Ausgaben "Starte Aufnahme VP1/VP2").
5. Eine Marker-Aktion im UI auslösen (z. B. Demo-Events). Es erscheint einmalig
   der Hinweis "Marker-Pfad deaktiviert", weitere Marker-Warnungen bleiben
   aus; Eingaben im UI werden dabei nicht blockiert.

## Event-Zeitmodell & Refinement

Die Tabletop-App vergibt jetzt für jedes UI-Ereignis sofort eine eindeutige
`event_id` sowie einen hochauflösenden Zeitstempel (`t_local_ns`) auf Basis von
`time.perf_counter_ns()`. Diese Metadaten werden zusammen mit
`mapping_version`, `origin_device` und `provisional=True` an beide Pupil-Brillen
geschickt. Der `PupilBridge` stellt sicher, dass alle Events – auch Fallbacks –
dieses Format einhalten und bietet mit `refine_event(...)` eine API zum späteren
Verfeinern.

Der neue `TimeReconciler` läuft im Hintergrund, verarbeitet regelmäßige
`sync.heartbeat`-Marker und schätzt daraus Offset und Drift je Gerät (robuste
lineare Regression mit Huber-Loss). Sobald verlässliche Parameter vorliegen,
werden alle `provisional`-Events mit einem gemeinsamen Referenzzeitpunkt
(`t_ref_ns`) aktualisiert. Die Ergebnisse landen sowohl über die Bridge (Cloud)
als auch lokal in den Session-Datenbanken (`logs/events_<session>.sqlite3`)
innerhalb der Tabelle `event_refinements`.

Einen schnellen Smoke-Test liefert:

```bash
python -m tabletop.app --demo
```

Für einen Bridge-Check ohne UI kann folgender Befehl genutzt werden:

```bash
python -m scripts.neon_smoke
```

Der Demo-Lauf simuliert Button-Events sowie Heartbeats, zeigt den Übergang von
„provisional“ → „refined“ mit `event_id`, aktueller `mapping_version`,
Konfidenz und Queue-Last direkt in der Konsole und erzeugt eine Demo-Datenbank
(`logs/demo_refinement.sqlite3`).

Falls ein angeschlossenes Gerät keine nachträglichen Änderungen an bestehenden
Annotationen zulässt (z. B. Neon ohne Retro-Edit), werden Refinements trotzdem
hostseitig gespiegelt und im Laufordner (`runs/`) protokolliert.

## Manuelle Prüfschritte

Die folgenden Tests dokumentieren den erwarteten Systemzustand in häufigen
Szenarien. Sie lassen sich ohne zusätzliche Tools durchführen und dienen der
Reproduzierbarkeit:

1. **Start ohne Session**
   - Anwendung starten, ohne eine Session zu wählen.
   - Erwartung: Es werden keine Verbindungsversuche zu Trackern unternommen.

2. **Session mit einem offline Tracker**
   - Session setzen und einen Tracker bewusst offline lassen.
   - Erwarteter Ablauf: Status „CONNECTING“, anschließend Timeout und Status
     „ERROR“.

3. **Beide Tracker online**
   - Beide Tracker einschalten und Session setzen.
   - Erwarteter Ablauf: Status „STARTING“, anschließend laufen beide Tracker,
     Status wechselt auf „READY“, die Schaltfläche „Play“ ist aktiv.

4. **Tracker benötigt zwei Verbindungsversuche**
   - Einen Tracker so konfigurieren, dass der erste Versuch scheitert, der
     zweite jedoch klappt.
   - Erwartung: Im Log sind die Wiederholungsversuche inklusive Verzögerung
     („Retry“ + Delay) sichtbar.

5. **Kurzzeitiger Verbindungsabbruch während laufender Session**
   - Spiel starten, einen Tracker kurzzeitig unterbrechen und wieder herstellen.
   - Erwartung: Der Tracker stellt im Hintergrund still wieder eine Verbindung
     her, ohne Fehlermeldung; nach erfolgreichem Reconnect läuft das Spiel
     weiter.
