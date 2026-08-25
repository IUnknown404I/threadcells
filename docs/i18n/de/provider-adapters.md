---
slug: provider-adapters
source: docs/PROVIDER_ADAPTERS.md
source_sha256: sha256:1b3bda3574765fd4b540f7460e14a1677a3d3dd58be8bf9d07f5fba0c53df1d9
---

# Erstellen von Anbieteradaptern

Dies ist ein fortgeschrittener Leitfaden für Maintainer, die eine vertrauenswürdige Anbieterintegration hinzufügen. Operatoren, die zwischen integrierten Anbietern wählen, sollten mit [Anbieter](PROVIDERS.md) beginnen.

ThreadCells Provider Adapter API V1 ist eine Vertrauensgrenze für Codeerweiterungen, die sich von Beobachter-Plugins unterscheidet. Installieren Sie Adapter als geprüfte Python-Pakete, die `ProviderAdapterDefinition`-Objekte unter der Entry-Point-Gruppe `threadcells.provider_adapters.v1` registrieren. Starten Sie den lokalen Kandidaten/die Laufzeit nach der Installation neu, damit Entry Points erneut erkannt werden.

## Vertrag

Eine Adapterdefinition stellt Folgendes bereit:

- ein `AdapterManifest` mit stabiler `adapter_id`, Plugin-API `1.0`, Implementierungsversion, Beschreibung, Fähigkeiten und JSON-Konfigurationsschema;
- ein `AdapterSettings`-Pydantic-Modell für deklarative Einstellungen;
- eine Factory, die `ProviderLaunchContext` und validierte Einstellungen akzeptiert;
- eine Preflight-Funktion, die normalisierten Zustand, Installation, Authentifizierung, Version, Kompatibilität, Modelle, Reason-Code und eine geheimnisfreie Meldung zurückgibt.

Der zurückgegebene Anbieter implementiert über den bestehenden `BaseProvider`-Lebenszyklus normalisiertes Starten/Fortsetzen/Abbrechen, Terminalstatus/-ergebnis, Nutzung und Integritätssemantik. Deklarieren Sie nicht unterstützte und bedingte Fähigkeiten ehrlich. Erfinden Sie niemals Nutzung, die eine CLI nicht meldet.

## Vertrauen und Konfiguration

Adapterpakete sind ausführbar und werden daher nur vom vertrauenswürdigen Hostoperator installiert. Registry-JSON kann keine Binärdateien auswählen oder Befehle injizieren. ThreadCells lehnt ausführbare Schlüssel sowie Befehls-, Shell-, Argument-, Flag-, Umgebungs-, Credential-, Passwort-, Token- und Secret-Schlüssel rekursiv ab. Rohgeheimnisse gehören nie in `settings`; verwenden Sie semantische undurchsichtige `secret_refs` und lösen Sie sie nur innerhalb vertrauenswürdigen Adaptercodes gemäß der Geheimnisrichtlinie der Installation auf.

Halten Sie Fehler mit stabilen Reason-Codes und öffentlich sicheren Meldungen normalisiert. Preflight darf weder Anbietereinstellungen verändern noch den Operator in dessen Namen authentifizieren.

## Beispiel

Der installierte Quellcode/Kandidat enthält `examples/provider-adapters/threadcells-echo`, ein deterministisches Paket und Manifest, das Entry Point, Schema, Konfigurationsvalidierung, Lebenszyklus, Preflight und nicht unterstützte Nutzung demonstriert. Es ist kein Modellanbieter und standardmäßig deaktiviert. Bauen/testen Sie es unabhängig vor der Installation.

Die paketierten Schemata unter `schemas/v1/adapter-manifest.schema.json` und `schemas/v1/capabilities.schema.json` sind die portablen Artefaktreferenzen. Die Python-Vertragsvalidierung bleibt für installierten Code maßgeblich.

## Bereitschaft muss wahrheitsgemäß bleiben

Verwenden Sie den kanonischen Namen der ausführbaren Datei des Anbieters und eine begrenzte, nicht mutierende Prüfung. Preflight beantwortet Installation, Kompatibilität, Authentifizierung, wenn sicher erkennbar, und einen öffentlich sicheren Fehlergrund. Es darf nicht behaupten, dass Adapterregistrierung eine CLI verfügbar macht.

Registry-APIs, Settings und Spawn Agent projizieren alle dasselbe Ergebnis. Fügen Sie Abdeckung hinzu, die beweist, dass ein nicht installierter Befehl deaktiviert ist, ein Authentifizierungsfehler von Abwesenheit unterschieden wird und ein installierter Anbieter mit wirklich unbekannter Authentifizierung als nicht geprüft gekennzeichnet bleibt.

## Nutzung muss wahrheitsgemäß bleiben

Bevorzugen Sie ein anbietereigenes strukturiertes Ereignis gegenüber Terminaltext-Parsing. Zeichnen Sie nur Felder auf, die der Anbieter ausgibt, bewahren Sie die kumulative Checkpoint-Identität und machen Sie Neustart/Wiedergabe idempotent. Machen Sie aus einer nicht verfügbaren Metrik niemals Null und schätzen Sie Kosten nicht aus Token ohne ausdrücklichen Anbietervertrag.

## Prüfliste für Reviews

- Stabile Adapter-ID, Version, Anzeigename und Konfigurationsschema.
- Keine vom Aufrufer ausgewählte ausführbare Datei, Shell, Argument-, Umgebungs- oder Rohgeheimnisfelder.
- Begrenzter Preflight ohne Einstellungs- oder Authentifizierungsmutation.
- Ehrliche unterstützte/bedingte/nicht unterstützte Fähigkeiten.
- Lebenszyklustests für Start, Status, Abbruch und wiederherstellbaren Fehler.
- Exakte Nutzungstests, wenn Telemetrie unterstützt wird.
- Konsistenztests für Registry/Settings/Spawn.
- Öffentlich sichere Fehler ohne Anmeldedaten oder private Pfade.
