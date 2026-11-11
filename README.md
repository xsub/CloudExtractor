# CloudExtractor

CloudExtractor zapisuje oryginały zdjęć i filmów na własnym storage. Pierwszy adapter korzysta z `icloudpd` 1.33.0 i działa w kontenerze na Linuksie. Udział z OpenBSD lub innego serwera montujesz na hoście Linuksa, a Docker Compose przekazuje jego katalog do kontenera.

Repozytorium zawiera szablon `.env.example` z pustym adresem konta Apple. Każdy host ma własny `.env` i katalog `.state/` z sesją. Git ignoruje te pliki i katalogi.

## Konfiguracja na Linuksie

Potrzebujesz Docker Engine, Docker Compose i zamontowanego udziału. Użyj osobnego katalogu archiwum i nadaj użytkownikowi kontenera prawo zapisu. Na przykład dla udziału zamontowanego w `/mnt/storage`:

```sh
mountpoint /mnt/storage &&
  mkdir -p /mnt/storage/cloudextractor/icloud &&
  touch /mnt/storage/cloudextractor/icloud/.cloudextractor-archive
```

Znacznik `.cloudextractor-archive` utwórz na zamontowanym udziale. Konfiguracja Compose wyłącza tworzenie ścieżki źródłowej bind mountu. Jeśli kontener mimo to dostanie pusty katalog zastępczy, kontrola znacznika zatrzyma eksport przed zapisem.

```sh
cp .env.example .env
install -d -m 0700 .state/icloud
id -u
id -g
```

W `.env` ustaw `ARCHIVE_HOST_PATH` na pełną ścieżkę katalogu ze znacznikiem, wpisz adres konta Apple w `ICLOUD_USERNAME` i dopasuj `CLOUDEXTRACTOR_UID` oraz `CLOUDEXTRACTOR_GID` do użytkownika z dostępem do archiwum i katalogu sesji. `STATE_HOST_PATH` wskazuje prywatny katalog sesji na lokalnym dysku Linuksa. Jeśli go zmienisz, utwórz wskazany katalog z uprawnieniami `0700`.

Jeśli konto korzysta z Zaawansowanej ochrony danych (ADP), ustaw `ICLOUD_ADP_WEB_ACCESS=true`, włącz dostęp do danych iCloud przez WWW i zaakceptuj prośbę Apple na zaufanym urządzeniu podczas logowania. [Dokumentacja uwierzytelniania `icloudpd`](https://icloud-photos-downloader.github.io/icloud_photos_downloader/authentication.html)

## Lokalna próba na laptopie

Jeśli uruchamiasz kontenery linuksowe na laptopie, możesz trzymać próbkę w ignorowanym katalogu `.state/archive`.

```sh
install -d -m 0700 .state/icloud .state/archive
touch .state/archive/.cloudextractor-archive
```

W lokalnym `.env` ustaw `ARCHIVE_HOST_PATH=./.state/archive`, wpisz adres konta Apple i dopasuj UID oraz GID do właściciela katalogów widocznego w kontenerze.

## Pierwsze uruchomienie

```sh
docker compose build
docker compose run --rm -T extractor check
docker compose run --rm extractor auth
ICLOUD_RECENT=20 docker compose run --rm -T extractor run
```

Polecenie `check` testuje docelowy katalog i dostęp do katalogu stanu bez połączenia z Apple. W `auth` podajesz hasło i potwierdzasz 2FA w terminalu. Kontener zachowuje sesję w prywatnym katalogu stanu. Po wygaśnięciu sesji ponownie uruchom `auth`.

Po sprawdzeniu próbki uruchom pełny eksport z pustym `ICLOUD_RECENT`:

```sh
docker compose run --rm -T extractor run
```

Archiwum działa zawsze w trybie **APPEND**. Panel i CLI dopisują wyłącznie brakujące pliki. Archiwum zachowuje już pobrane zdjęcia i filmy po ich usunięciu z iCloud.

Archiwum używa katalogów `<Apple ID>/rok/miesiąc` w UTC oraz nazw z identyfikatorem zasobu iCloud. Eksport obejmuje oryginały, część wideo Live Photo i dostępną alternatywną reprezentację zasobu, np. parę RAW+JPEG. Po przerwaniu uruchom `run` ponownie z tą samą konfiguracją.

Przed odłączeniem iCloud trzeba zweryfikować kompletność osobno, w tym zakres zbiorów udostępnionych i wersje po edycji. `check` potwierdza dostępność miejsca zapisu.

## Wiele kont Apple

Każde uruchomienie obsługuje konto wskazane w `ICLOUD_USERNAME`. Konta mają osobne katalogi archiwum, sesje i blokady. Nazwa podkatalogu to adres konta zapisany małymi literami. Aby uwierzytelnić i przetestować kolejne konto, podstaw jego adres w obu poleceniach:

```sh
ICLOUD_USERNAME=drugie-konto@example.com docker compose run --rm extractor auth
ICLOUD_USERNAME=drugie-konto@example.com ICLOUD_RECENT=20 docker compose run --rm -T extractor run
```

Przy aktualizacji z wersji jednokontowej zatrzymaj kontener i przenieś katalogi lat oraz pliki sesji do podkatalogu dotychczasowego Apple ID w ich katalogach nadrzędnych. Znacznik `.cloudextractor-archive` pozostaje w katalogu głównym archiwum.

## Panel pobierania i galerii

Na hoście Linuksa uruchom `docker compose up -d --build ui` z katalogu instalacji. Na laptopie otwórz tunel, podstawiając adres lub alias SSH hosta:

```sh
ssh -N -L 8765:127.0.0.1:8765 host-linux
```

Otwórz [panel CloudExtractor](http://127.0.0.1:8765). Jeśli ustawisz inny port przez `CLOUDEXTRACTOR_UI_PORT` w `.env`, użyj go w poleceniu tunelu i adresie panelu. Panel wybiera Apple ID spośród kont skonfigurowanych lub zalogowanych przez `auth`. Domyślny zakres panelu to `ICLOUD_RECENT` lub 20 pozycji. Opcja „Cała biblioteka” uruchamia pełny przebieg.

„Uruchom”, „Zatrzymaj” i „Wznów” sterują przebiegami uruchomionymi z panelu. „Skanuj iCloud” odczytuje metadane całej głównej biblioteki konta i odświeża rozmiar oraz listę braków. Zakres próbny dotyczy pobierania. Galeria wczytuje miniatury ze wskazanego katalogu konta. Dwuklik otwiera pełnoekranowy podgląd. Odtwarzanie oryginalnych filmów zależy od obsługi formatu przez przeglądarkę.

Panel pokazuje dwa łączne rozmiary dla wybranego Apple ID. Apple raportuje miejsce zajęte przez Zdjęcia iCloud. Szczegółowy skan liczy pliki dostępne dla eksportu, w tym części wideo Live Photos i warianty RAW+JPEG. Suma lokalna obejmuje pliki w archiwum, także częściowe pobrania. Filtry typu i katalogu pokazują podsumowania wybranych plików obok obu sum. W tabeli katalogów rozmiary są grupowane według ścieżek archiwum `rok/miesiąc`.

## Adaptery

Moduły źródeł są w `cloudextractor/adapters/`. Każdy adapter wyznacza katalogi archiwum i stanu oraz argumenty procesu dla `run` i `auth`. Polecenie główne wybiera adapter przez `CLOUDEXTRACTOR_SOURCE`, sprawdza archiwum, blokuje równoległe użycie tego samego stanu i przekazuje kod zakończenia procesu. Import z Google Takeout może użyć tej samej obsługi katalogu docelowego i własnej konfiguracji wejścia.

Adaptery przestrzegają trybu APPEND. Adapter iCloud przekazuje `icloudpd` ustaloną listę opcji w trybie Copy.
