# ProteinRadar — rilevazione prezzi giornaliera (prototipo)

Lo script `scripts/daily_protein_track.py` legge prodotti e hyperlink dalla riga 1 del foglio Google `Daily_ProteinTrack`, scheda `Foglio1`. Ogni colonna B–L contiene un prodotto. Scrive il prezzo numerico in euro nella riga con data odierna (colonna A), senza cancellare lo storico o sovrascrivere prezzi già registrati oggi.

- Bulk: tenta di selezionare `Non aromatizzato` e il peso dell'intestazione (non quello predefinito nel link).
- Myprotein: usa il parametro `variation` già indicato nel link.
- Prezzi non confermati: lascia le celle vuote e registra un avviso nei log.
- Non comprende le spese di spedizione. Non aggira CAPTCHA o blocchi del negozio.

## Configurazione obbligatoria

1. Google Cloud Console: crea un progetto, abilita **Google Sheets API**, crea un **account di servizio** e scarica la sua chiave JSON.
2. Nel foglio Google clicca **Condividi** e aggiungi come **Editor** l'indirizzo `client_email` contenuto nel JSON.
3. GitHub → repository **ProteinRadar/protein-radar** → **Settings → Secrets and variables → Actions → New repository secret**. Crea due secret:
   - `PROTEIN_SHEET_ID`: ID del foglio (parte dell'URL fra `/d/` e `/edit`).
   - `PROTEIN_GOOGLE_SERVICE_ACCOUNT_JSON`: tutto il contenuto JSON della chiave dell'account di servizio.
4. GitHub → **Actions → Daily ProteinTrack → Run workflow**. Controlla i log e verifica manualmente che i primi prezzi corrispondano alle varianti richieste.

Non committare mai il JSON nel repository. Il workflow salta l'elaborazione se mancano i due secret.

Pianificazione automatica: **05:00 UTC** ogni giorno (06:00 CET, 07:00 CEST), con possibili ritardi GitHub.

## Esecuzione locale

```bash
pip install -r requirements-price-tracker.txt
python -m playwright install chromium
# Imposta PROTEIN_SHEET_ID e PROTEIN_GOOGLE_SERVICE_ACCOUNT_JSON
python scripts/daily_protein_track.py            # sola lettura
python scripts/daily_protein_track.py --write    # scrive prezzi verificati
```

**Limite attuale:** il programma è stato controllato staticamente e la logica base è testata, ma non è ancora stato validato tramite esecuzione di Playwright sui siti reali. In particolare selettori e vincoli anti-bot di Bulk e Myprotein potrebbero richiedere modifiche.
