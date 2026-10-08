# ProteinRadar — rilevazione quotidiana dei prezzi (prototipo)

Il programma `scripts/daily_protein_track.py` legge le intestazioni con hyperlink della prima riga di `Daily_ProteinTrack` (scheda `Foglio1`). Ogni colonna B–L identifica una variante. Registra prezzi numerici in euro sulla riga della data corrente, senza sovrascrivere lo storico o i valori già inseriti.

## Stato e limiti

- Il codice è un **prototipo**: non è ancora stato provato con successo contro le pagine reali Bulk e Myprotein.
- Bulk: prova a selezionare «Non aromatizzato» e il peso indicato nell'intestazione.
- Myprotein: usa il parametro `variation` già presente nel link.
- Se la variante, il prezzo o la disponibilità non sono verificabili, la cella resta vuota; i log indicano il problema.
- I prezzi non comprendono spedizione né codici sconto personalizzati. Non elude CAPTCHA o restrizioni d'accesso.

## Connessione sicura Google Sheets — nessuna chiave JSON

La policy Google Cloud può vietare la creazione di chiavi JSON. Qui si usa **Workload Identity Federation / GitHub OIDC**, che genera credenziali temporanee senza scaricare chiavi private.

### 1. Account di servizio

Usa l'account di servizio già creato su Google Cloud oppure creane uno, per esempio `proteinradar-tracker`.

Non creare chiavi JSON. Copia solo la sua email (forma `nome@progetto.iam.gserviceaccount.com`).

### 2. API Google

Nel **medesimo progetto Google Cloud**, abilita:
- Google Sheets API
- IAM API (`iam.googleapis.com`)
- IAM Service Account Credentials API (`iamcredentials.googleapis.com`)
- Security Token Service API (`sts.googleapis.com`)
- Cloud Resource Manager API (`cloudresourcemanager.googleapis.com`), se richiesta dal progetto

Il progetto potrebbe richiedere billing abilitato per impostare la federazione; verifica indicazioni della console.

### 3. Workload Identity Pool e provider

Apri: https://console.cloud.google.com/iam-admin/workload-identity-pools

Crea un pool chiamato `proteinradar-github` e un provider `github-actions`:
- Tipo: **OpenID Connect (OIDC)**
- Issuer URL: `https://token.actions.githubusercontent.com/`
- Audience: **Default audience**
- Attribute mapping:
  - `google.subject` = `assertion.sub`
  - `attribute.repository` = `assertion.repository`
- Attribute condition (importante: limita l'accesso al repository e al ramo principale):
  ```text
  assertion.repository == 'ProteinRadar/protein-radar' && assertion.ref == 'refs/heads/main'
  ```

Se la creazione del pool/provider è bloccata da un'altra policy, occorre l'intervento di un amministratore Google Cloud o un progetto personale idoneo: non disabilitare arbitrariamente le protezioni.

### 4. Consenti al repository di impersonare l'account

Apri Google Cloud → **IAM e amministrazione → Account di servizio → proteinradar-tracker → Autorizzazioni → Concedi accesso**.

Aggiungi il principal (sostituisci `PROJECT_NUMBER`, che è il numero del progetto Google, non il nome):
```text
principalSet://iam.googleapis.com/projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/proteinradar-github/attribute.repository/ProteinRadar/protein-radar
```
Ruolo: **Workload Identity User** (`roles/iam.workloadIdentityUser`).

È anche possibile usare la procedura **Grant access** del Workload Identity Pool selezionando l'account di servizio e l'attributo repository.

### 5. Condividi il foglio Google

Apri https://docs.google.com/spreadsheets/d/146Al8DkpVSZYZXLJMtfMWXQzliYp6BeRCTJigvkKLd0/edit

Premi **Condividi**, aggiungi l'email dell'account di servizio come **Editor**.

### 6. Configura i tre GitHub Actions secrets

Apri https://github.com/ProteinRadar/protein-radar/settings/secrets/actions e aggiungi:

| Name | Value |
| --- | --- |
| `PROTEIN_SHEET_ID` | L'ID di `Daily_ProteinTrack` ottenuto dall'URL |
| `PROTEIN_GOOGLE_SERVICE_ACCOUNT` | Email dell'account di servizio |
| `PROTEIN_WORKLOAD_IDENTITY_PROVIDER` | `projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/proteinradar-github/providers/github-actions` |

**Non è necessario `PROTEIN_GOOGLE_SERVICE_ACCOUNT_JSON`**; quel vecchio secret non viene più usato. Non caricare chiavi private nel repository.

### 7. Prima esecuzione

GitHub → https://github.com/ProteinRadar/protein-radar/actions/workflows/daily-protein-track.yml → **Run workflow**.

I log indicano le varianti verificate e le varianti non verificabili. Controlla manualmente i primi prezzi prima di utilizzare questo storico per prendere decisioni di acquisto.

Lo script parte ogni giorno alle **05:00 UTC** (06:00 CET oppure 07:00 CEST). GitHub Actions può eseguire il workflow con ritardo.

## Sviluppo locale

```bash
pip install -r requirements-price-tracker.txt
python -m playwright install chromium
gcloud auth application-default login --scopes=https://www.googleapis.com/auth/spreadsheets,https://www.googleapis.com/auth/cloud-platform
# Imposta PROTEIN_SHEET_ID
python scripts/daily_protein_track.py         # controllo senza scrittura
python scripts/daily_protein_track.py --write # aggiorna i prezzi verificati
```

Il foglio deve essere accessibile all'identità con cui esegui lo script. Eseguire lo script in locale non configura l'account GitHub: il workflow usa separatamente OIDC.
