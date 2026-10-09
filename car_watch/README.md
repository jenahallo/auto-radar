# AutoRadar — Sauto.cz + Sbazar.cz

Webový přehled benzínových automatů po celé ČR:

| Parametr | Hodnota |
| --- | --- |
| Cena | 1–220 000 Kč (cena dohodou bez částky se nezapočítává) |
| Převodovka | Automatická |
| Palivo | Benzín |
| Rok výroby | Od 2015 |
| Nájezd | Do 130 000 km včetně |
| Výchozí období | Posledních 48 hodin |
| Další období | 7 a 30 dní |
| Denní kontrola | 08:00 Europe/Prague (včetně letního času) |

## Spuštění webové aplikace

Vyžaduje Python 3.12 nebo novější. Žádné externí Python balíčky.

```bash
python car_watch/server.py
```

Otevřete http://localhost:8080. Tlačítko **Spustit kontrolu** spustí skutečné hledání na obou serverech. Během kontroly lze prohlížet předchozí data. Přepínání 48 hodin / 7 / 30 dní nevyžaduje další hledání: každá kontrola sbírá 30 dní. Bez uložených dat se první kontrola spustí automaticky. Server musí běžet, aby jeho vlastní plánovač mohl pracovat.

## Docker

```bash
cd car_watch
docker compose up -d --build
```

Web: http://localhost:8080. Data jsou v trvalém Docker volume. Po restartu se zachovají; denní čas se počítá v Europe/Prague nezávisle na časové zóně hostitele.

Compose zpřístupňuje port pouze na localhost. Pro přístup z internetu nasaďte za HTTPS reverse proxy a nastavte `CAR_PASSWORD` (uživatel `carwatch`), případně použijte přihlašování na proxy. Heslo nikdy neukládejte do repozitáře. Před reverse proxy lze změnit mapování portu podle zvolené infrastruktury.

## Denní běh na GitHubu

Workflow [AutoRadar](https://github.com/jenahallo/auto-radar/actions/workflows/car-watch.yml) je naplánovaný na **08:00 Europe/Prague**. Ručně se spouští přes **Run workflow**. Spuštění plánu na GitHub Actions může být zpožděné vytížením GitHubu; pro přesnější čas použijte běžící server s vlastním plánovačem.

Po běhu:

1. V souhrnu běhu je stav obou zdrojů a počet výsledků.
2. Artifact **auto-radar** obsahuje `dashboard.html` a `latest.json`.
3. Stažený `dashboard.html` funguje samostatně v prohlížeči, včetně fotografií, hledání, řazení a období. Je to snapshot: jeho tlačítko otevře GitHub Actions, kde je nutné kliknout **Run workflow**. Nové výsledky pak stáhněte z nového běhu.
4. `car_watch/data/latest.json` se aktualizuje také přímo v repozitáři. Při chybě zdroje obsahuje starší výsledky jasně označené jako neověřené při poslední kontrole.

GitHub ukládá kód a spouští kontroly. Tento repozitář je soukromý; samotné nahrání kódu nevytvoří veřejnou URL běžícího serveru. Pro trvale dostupný web a přímé ruční spuštění z webu je potřeba nasadit Docker/Python server na vybraný hosting. Snapshot z Actions je dostupný i bez něj.

Workflow používá `contents: write` pouze pro job ukládající `latest.json`. Pokud GitHub zápis zakáže, zkontrolujte **Settings → Actions → General → Workflow permissions** a pravidla větve. Artifact zůstává použitelný i při neúspěšném zápisu. V repozitáři se nepřepisují ostatní aplikace ani jejich workflow.

## Jak se vybírají nabídky

- Sauto: čtení veřejného endpointu používaného webem, stránkování všech výsledků a další kontrola parametrů v aplikaci. Kategorie osobních aut má ID `838`. API filtry se nesmějí slepě považovat za potvrzení všech parametrů.
- Sbazar: kategorie `/170-osobni-auta`, vyhledávání výrazů automat, DSG, DCT, CVT, EDC, EAT6, EAT8, tronic a Powershift. Výraz tronic pokrývá také Tiptronic, S tronic a Easytronic. Čtení strukturovaných dat vložených do veřejné HTML stránky a kontrola popisu v detailu. Nevyužívá přihlášení ani placenou službu. Vyhledávání závisí na textu prodejce; nabídka, která automatickou převodovku neuvádí žádným z těchto výrazů, se nemusí najít.
- Rozhoduje **datum vložení** (`create_date` / `createDate`), nikoli datum obnovení, topování či prvního nalezení. Obě data se zobrazují odděleně. Čas bez offsetu ze Seznamu se interpretuje jako český místní čas.
- Chybějící nebo nejednoznačné parametry ze Sbazar nejsou odhadované z modelu auta. Nabídky mohou být v části **K ručnímu ověření**; nejsou započítané mezi odpovídající auta. Automatická klimatizace neznamená automatickou převodovku. Hybrid, LPG/CNG ani diesel nepatří do výběru čistě benzínových aut.
- Při chybě nebo dosažení limitu stránky/detailů se zobrazí **chyba zdroje / neúplné výsledky**, nikoli zavádějící zpráva, že na zdroji nejsou vhodná auta. Neúplná kontrola skončí na Actions chybou a přesto uloží přehled.
- Nedochází k obcházení přihlašování nebo blokací. API a struktura HTML nejsou smluvně garantovaná; jejich změna může vyžadovat úpravu parseru.
- Obě nabídky stejného auta mohou zůstat samostatně. Automatické slučování podle názvu by mohlo spojit různá auta. Dostupnost auta a pravdivost parametrů je potřeba ověřit u prodejce.

## Nastavení a diagnostika

```bash
python car_watch/collector.py
python car_watch/export.py
python -m unittest discover -s car_watch/tests -v
```

| Proměnná serveru | Výchozí hodnota | Význam |
| --- | --- | --- |
| `CAR_HOST` | `127.0.0.1` | Adresa pro naslouchání (v Dockeru `0.0.0.0`) |
| `PORT` | `8080` | HTTP port |
| `CAR_DATA` | `car_watch/data/latest.json` | Trvalá cesta k výsledkům |
| `CAR_PASSWORD` | prázdné | Volitelné HTTP Basic přihlášení; pro internet používat s HTTPS |
| `CAR_SCHEDULE` | `true` | Vlastní denní plánovač běžícího webového serveru |
| `CAR_SCAN_ON_START` | `false` | Nová kontrola po každém spuštění serveru |
| `CAR_MAX_PAGES` | `60` | Bezpečnostní limit stránek na zdroj / výraz |
| `CAR_MAX_DETAILS` | `1500` | Bezpečnostní limit detailů Sbazar |

Sběr na Sbazar může kvůli detailům a rozsahu 30 dní trvat několik minut až desítky minut. Opakované kontroly znovu používají již ověřené nezměněné nabídky. Současné kliknutí na ruční kontrolu a plánovač nespustí dva sběry ve stejném serveru. CLI nepouštějte současně nad stejným souborem se serverem.

Výsledky jsou ukládány atomicky. Odpovědi API neobsahují hesla ani GitHub tokeny. HTML používá textContent při vkládání textu inzerátů; snapshot escapuje vložené JSON proti předčasnému ukončení skriptu.

Oficiální dokumentace plánu GitHub Actions: https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#onschedule
