#!/usr/bin/env python3
"""Raccoglitore dati per PronosticiCalcio.

Scarica i file CSV di football-data.co.uk, li normalizza in JSON e li salva
nella cartella ``dati/`` del repository. Viene eseguito da GitHub Actions una
volta al giorno: i file prodotti sono poi letti direttamente dal tool HTML
tramite raw.githubusercontent.com.

Usa solo la libreria standard di Python: nessuna dipendenza da installare.
"""

from __future__ import annotations

import csv
import io
import json
import pathlib
import urllib.error
import urllib.request
from datetime import datetime, timezone

# --------------------------------------------------------------------------
# Configurazione
# --------------------------------------------------------------------------

BASE = "https://www.football-data.co.uk"
CARTELLA = pathlib.Path("dati")

# Quante stagioni indietro scaricare (1 = solo quella corrente).
STAGIONI_INDIETRO = 4

# Campionati da raccogliere: codice del sito -> (nome, paese).
CAMPIONATI = {
    "I1": ("Serie A", "Italia"),
    "I2": ("Serie B", "Italia"),
    "E0": ("Premier League", "Inghilterra"),
    "E1": ("Championship", "Inghilterra"),
    "E2": ("League One", "Inghilterra"),
    "E3": ("League Two", "Inghilterra"),
    "SP1": ("La Liga", "Spagna"),
    "SP2": ("La Liga 2", "Spagna"),
    "D1": ("Bundesliga", "Germania"),
    "D2": ("2. Bundesliga", "Germania"),
    "F1": ("Ligue 1", "Francia"),
    "F2": ("Ligue 2", "Francia"),
    "N1": ("Eredivisie", "Olanda"),
    "P1": ("Primeira Liga", "Portogallo"),
    "B1": ("Pro League", "Belgio"),
    "T1": ("Super Lig", "Turchia"),
    "G1": ("Super League", "Grecia"),
    "SC0": ("Premiership", "Scozia"),
    "SC1": ("Championship", "Scozia"),
}

# Colonne delle quote, in ordine di preferenza. Le sigle con la C indicano le
# quote di chiusura, le piu' informative perche' incorporano tutte le notizie.
QUOTE = {
    "casa": ["AvgCH", "AvgH", "B365CH", "B365H", "PSCH", "PSH", "BbAvH"],
    "pari": ["AvgCD", "AvgD", "B365CD", "B365D", "PSCD", "PSD", "BbAvD"],
    "fuori": ["AvgCA", "AvgA", "B365CA", "B365A", "PSCA", "PSA", "BbAvA"],
    "over25": ["AvgC>2.5", "Avg>2.5", "B365C>2.5", "B365>2.5"],
    "under25": ["AvgC<2.5", "Avg<2.5", "B365C<2.5", "B365<2.5"],
}


# --------------------------------------------------------------------------
# Funzioni di supporto
# --------------------------------------------------------------------------

def stagioni_da_scaricare(oggi: datetime) -> list[str]:
    """Restituisce i codici stagione del sito, es. '2627', dal piu' recente."""
    inizio = oggi.year if oggi.month >= 7 else oggi.year - 1
    codici = []
    for scarto in range(STAGIONI_INDIETRO):
        anno = inizio - scarto
        codici.append(f"{anno % 100:02d}{(anno + 1) % 100:02d}")
    return codici


def nome_stagione(codice: str) -> str:
    """Da '2627' a '2026-27', il formato usato dal tool."""
    return f"20{codice[:2]}-{codice[2:]}"


def scarica(url: str) -> str | None:
    """Scarica un file di testo. Restituisce None se non esiste."""
    richiesta = urllib.request.Request(
        url, headers={"User-Agent": "PronosticiCalcio/1.0 (uso personale)"}
    )
    try:
        with urllib.request.urlopen(richiesta, timeout=60) as risposta:
            grezzo = risposta.read()
    except urllib.error.HTTPError as errore:
        if errore.code == 404:
            return None
        raise
    # I file del sito sono in latin-1: decodifico in modo tollerante.
    return grezzo.decode("latin-1", errors="replace")


def numero(valore: str | None) -> float | None:
    """Converte una cella in numero, ignorando celle vuote o non valide."""
    if valore is None:
        return None
    try:
        n = float(str(valore).strip())
    except ValueError:
        return None
    return n if n > 1 else None


def prima_quota(riga: dict, colonne: list[str]) -> float | None:
    """Prima quota disponibile tra le colonne indicate."""
    for colonna in colonne:
        n = numero(riga.get(colonna))
        if n is not None:
            return n
    return None


def data_iso(valore: str) -> str | None:
    """Da '22/08/2026' o '22/08/26' a '2026-08-22'."""
    parti = str(valore).strip().split("/")
    if len(parti) != 3:
        return None
    giorno, mese, anno = parti
    if len(anno) == 2:
        anno = ("19" if int(anno) > 70 else "20") + anno
    try:
        return f"{int(anno):04d}-{int(mese):02d}-{int(giorno):02d}"
    except ValueError:
        return None


def converti_riga(riga: dict) -> dict | None:
    """Trasforma una riga del CSV nel formato interno del tool."""
    casa = (riga.get("HomeTeam") or riga.get("Home") or "").strip()
    fuori = (riga.get("AwayTeam") or riga.get("Away") or "").strip()
    data = data_iso(riga.get("Date", ""))
    if not casa or not fuori or not data:
        return None

    partita = {
        "data": data,
        "ora": (riga.get("Time") or "").strip(),
        "casa": casa,
        "fuori": fuori,
    }

    gol_casa = riga.get("FTHG") or riga.get("HG")
    gol_fuori = riga.get("FTAG") or riga.get("AG")
    try:
        partita["gol"] = [int(gol_casa), int(gol_fuori)]
    except (TypeError, ValueError):
        partita["gol"] = None  # partita non ancora giocata

    quote = {nome: prima_quota(riga, colonne) for nome, colonne in QUOTE.items()}
    if any(v is not None for v in quote.values()):
        partita["quote"] = quote

    return partita


def leggi_csv(testo: str) -> list[dict]:
    """Converte il testo di un CSV in un elenco di partite normalizzate."""
    lettore = csv.DictReader(io.StringIO(testo))
    partite = []
    for riga in lettore:
        convertita = converti_riga(riga)
        if convertita:
            partite.append(convertita)
    return partite


def salva_json(percorso: pathlib.Path, contenuto: dict) -> None:
    """Scrive il JSON in modo stabile, per non creare commit inutili."""
    percorso.parent.mkdir(parents=True, exist_ok=True)
    testo = json.dumps(contenuto, ensure_ascii=False, indent=1, sort_keys=True)
    percorso.write_text(testo + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# Programma principale
# --------------------------------------------------------------------------

def main() -> None:
    oggi = datetime.now(timezone.utc)
    momento = oggi.strftime("%Y-%m-%d %H:%M UTC")
    stagioni = stagioni_da_scaricare(oggi)
    print(f"Stagioni richieste: {', '.join(nome_stagione(s) for s in stagioni)}")

    indice = {"aggiornato": momento, "leghe": []}

    for codice, (nome, paese) in CAMPIONATI.items():
        trovate = []
        for stagione in stagioni:
            url = f"{BASE}/mmz4281/{stagione}/{codice}.csv"
            testo = scarica(url)
            if testo is None:
                continue
            partite = leggi_csv(testo)
            if not partite:
                continue
            giocate = [p for p in partite if p["gol"]]
            salva_json(
                CARTELLA / codice / f"{nome_stagione(stagione)}.json",
                {
                    "codice": codice,
                    "nome": nome,
                    "paese": paese,
                    "stagione": nome_stagione(stagione),
                    "aggiornato": momento,
                    "partite": partite,
                },
            )
            trovate.append(nome_stagione(stagione))
            print(f"  {codice} {nome_stagione(stagione)}: "
                  f"{len(partite)} partite ({len(giocate)} giocate)")

        if trovate:
            indice["leghe"].append({
                "codice": codice,
                "nome": nome,
                "paese": paese,
                "stagioni": trovate,
            })

    # Partite in programma: un unico file per tutti i campionati.
    testo = scarica(f"{BASE}/fixtures.csv")
    if testo:
        tutte = []
        lettore = csv.DictReader(io.StringIO(testo))
        for riga in lettore:
            convertita = converti_riga(riga)
            if convertita and (riga.get("Div") or "").strip() in CAMPIONATI:
                convertita["codice"] = riga["Div"].strip()
                tutte.append(convertita)
        salva_json(CARTELLA / "prossime.json",
                   {"aggiornato": momento, "partite": tutte})
        print(f"  Partite in programma: {len(tutte)}")
        indice["prossime"] = len(tutte)

    indice["leghe"].sort(key=lambda l: (l["paese"], l["nome"]))
    salva_json(CARTELLA / "indice.json", indice)
    print(f"Fatto: {len(indice['leghe'])} campionati aggiornati.")


if __name__ == "__main__":
    main()
