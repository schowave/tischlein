# Offen

- Einkaufsliste in die Paprika-Liste übertragen (an Bring wird schon übergeben).
- Bring-Übergabe über Wochen hinweg: Bring führt gleichnamige Artikel zusammen. Übergeben beide Wochen
  „Zwiebeln“, gilt die zuletzt geschickte Menge, und „weg“ in einer Woche entfernt sie auch für die andere.
  Ebenso entfernt „weg“ einen gleichnamigen Artikel, den jemand von Hand in Bring angelegt hat.
- Kochzeiten sind in Paprika selten gepflegt; schnelle Tage helfen erst, wenn `total_time` oder die
  Kategorie `Schnell` gesetzt ist.
- Freigegebene Pläne in den Paprika Meal Planner schreiben (Schnittstelle: `docs/paprika-api.md`).
- Wochenschema und Regeln in den Einstellungen statt in `tischlein.toml`.
- Fotos von Web-Rezepten in PNG/WebP nach Paprika (heute nur JPEG; bräuchte eine Umwandlung).
- Zutaten aufbrauchen: braucht ein Gericht nur einen Teil einer Packung (Sahne, Lauch), in derselben Woche
  ein zweites Gericht damit bevorzugen.
- Wer in der Mitteilung „Wie war's?“ antwortet, ist Tischlein nicht bekannt (Home Assistant meldet nur die Antwort).
- Stöbern nutzt Chefkochs inoffizielle App-Schnittstelle, die Suchseite von Eat Smarter und den
  Suchindex hinter der Cookidoo-Suche; fällt eine weg, bleibt der Weg über den Rezept-Link.
- Cookidoo: Die Rezeptseite zeigt keine Schritte; Tischlein fragt dieselbe Adresse als JSON und bekommt
  sie ohne Anmeldung. Verlangt Cookidoo dafür einmal einen Login, bleiben Zutaten, Zeiten und Foto.
- Die Cookidoo-Suche lässt sich nicht auf Hauptgerichte eingrenzen; zu „Lauch“ kommen auch Kuchen und Aufstriche.
