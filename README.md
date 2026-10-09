# AutoRadar

Veřejný webový přehled aut z Sauto.cz a Sbazar.cz.

**Automat · benzín · do 220 000 Kč · od 2015 · do 130 000 km · celá ČR.**

Web ukazuje posledních 48 hodin, s přepnutím na 7 a 30 dní. Každý den v 08:00 Europe/Prague se nabídky aktualizují přes GitHub Actions. Na webu tlačítko pro ruční kontrolu otevře Actions, kde vlastník klikne **Run workflow**. Po dokončení se web znovu publikuje.

Pro první publikování: **Settings → Pages → Build and deployment → Source: GitHub Actions**. Workflow AutoRadar poté publikuje samostatný veřejný web. Ostatní projekty z původního soukromého repozitáře nejsou součástí tohoto repozitáře.

Web je veřejný a obsahuje pouze údaje z veřejných inzerátů. Žádný GitHub token, přihlašovací údaj ani soukromý obsah se neposílá do webu. Nová kontrola vyžaduje přihlášení a oprávnění na GitHubu.

[Dokumentace, Docker a lokální spuštění](car_watch/README.md).

Upozornění: Kontrola přes GitHub Actions může začít později kvůli vytížení GitHubu. U Sbazar se parametry ověřují z textu inzerátu; chybějící údaje se zobrazí samostatně jako neověřené. Neúplná nebo neúspěšná kontrola je na webu zřetelně označená.
