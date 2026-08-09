# Metodologia del Motore di Valutazione Solare KiloWattChiaro

**Versione**: 1.2 — 2026-08-09
**Stato**: **Pubblicata** — questa metodologia è pubblica, insieme al codice che la implementa ([`src/kilowattchiaro_engine/`](../src/kilowattchiaro_engine/))

> **English readers** — this document specifies, in Italian, the full
> methodology of the KiloWattChiaro solar-evaluation engine: data sources,
> consumption archetypes, the hourly self-consumption model, the 20-year
> financial model (NPV / IRR / payback with explicit formulas), price
> scenarios, known limitations, and a register of every assumption with its
> value. The code implementing it is in this repository and is the exact
> engine running in production at [kilowattchiaro.it](https://kilowattchiaro.it):
> deterministic — same inputs, same answer. File references below point into
> `src/kilowattchiaro_engine/`; references marked *(privato)* are to the
> private service layer (HTTP connectors, API, persistence), which is not
> part of the published engine.

---

## Indice

1. [Ambito e Finalità](#1-ambito-e-finalità)
2. [Fonti Dati Esterne](#2-fonti-dati-esterne)
3. [Pipeline di Input](#3-pipeline-di-input)
4. [Modello di Stima dei Consumi](#4-modello-di-stima-dei-consumi)
5. [Stima della Produzione Fotovoltaica](#5-stima-della-produzione-fotovoltaica)
6. [Modello di Autoconsumo](#6-modello-di-autoconsumo)
7. [Modello Finanziario](#7-modello-finanziario)
8. [Scenari di Proiezione Prezzi](#8-scenari-di-proiezione-prezzi)
9. [Confronto Preventivi](#9-confronto-preventivi)
10. [Limitazioni Note](#10-limitazioni-note)
11. [Validazione e Backtest](#11-validazione-e-backtest)
12. [Registro delle Assunzioni](#12-registro-delle-assunzioni)

---

## 1. Ambito e Finalità

### Cosa fa il motore

KiloWattChiaro stima il rendimento finanziario di un impianto fotovoltaico residenziale in Italia. Il motore combina:

- dati pubblici di irraggiamento solare (PVGIS, Commissione Europea)
- rilevamento satellitare del tetto (Google Solar API)
- stima o inserimento dei consumi dell'utente
- modello finanziario a 20 anni con proiezione prezzi energia

L'output è una simulazione comparativa di tre scenari: **stato attuale** (senza fotovoltaico), **solo fotovoltaico**, e **fotovoltaico + batteria**, con metriche finanziarie (NPV, IRR, payback, risparmio annuo).

### Cosa NON fa il motore

> Le informazioni, i dati e i risultati forniti da KiloWattChiaro hanno carattere puramente informativo, educativo e indicativo. KiloWattChiaro **NON** costituisce e **NON** deve essere interpretato come:
>
> - consulenza in materia di investimenti ai sensi del D.Lgs. 58/1998 (TUF)
> - consulenza finanziaria, fiscale, legale o tecnica
> - raccomandazione personalizzata all'acquisto di prodotti o servizi
> - sollecitazione all'investimento
> - offerta commerciale
>
> I calcoli di NPV, IRR, payback period e risparmio stimato sono basati su parametri medi e ipotesi semplificative che potrebbero non riflettere la situazione specifica dell'utente. I risultati effettivi possono variare significativamente. Per decisioni di investimento si consiglia di rivolgersi a un tecnico qualificato e a un consulente finanziario. KiloWattChiaro non presta servizi di investimento e non è iscritto in alcun albo o registro previsto dal TUF.

Il motore non produce APE (attestati di prestazione energetica), progetti impiantistici, certificazioni, o perizie tecniche. I risultati sono **stime indicative** ("simulazione finanziaria") che richiedono verifica professionale.

---

## 2. Fonti Dati Esterne

### 2.1 PVGIS (European Commission Joint Research Centre)

- **API**: v5.3, `https://re.jrc.ec.europa.eu/api/v5_3`
- **Endpoint `/PVcalc`**: produzione mensile media (kWh/mese)
- **Endpoint `/tmy`**: profilo orario TMY (Typical Meteorological Year, 8760 ore)
- **Accuratezza dichiarata**: ±5% per l'Italia (documentazione PVGIS)
- **Cache TMY**: arrotondata a 2 decimali lat/lon (~1,1 km di risoluzione)
- **Parametri inviati**:
  - `pvtechchoice`: silicio cristallino (`crystSi`)
  - `mountingplace`: integrato in edificio (`building`)
  - `loss`: 14% (perdite di sistema BOS, cavi, inverter)
  - `angle`: inclinazione in gradi (0–90)
  - `aspect`: azimuth in convenzione PVGIS (0 = sud, negativo = est, positivo = ovest)

**Limitazione**: il TMY rappresenta un anno meteorologico statistico tipico, non il meteo recente. Non cattura la variabilità inter-annuale (±10% anno su anno).

Codice: `backend/app/connectors/pvgis.py` *(privato — la trasformazione pura è pubblicata in `src/kilowattchiaro_engine/pvgis.py`)*

### 2.2 Google Solar Building Insights API

- **Utilizzo**: rilevamento segmenti tetto (inclinazione, azimuth, area utilizzabile)
- **Budget mensile**: 9.500 chiamate (su 10.000 del tier gratuito Google)
- **Fallback** (assenza copertura): segmento singolo sud (azimuth 180° bussola / 0° PVGIS), inclinazione 35°, area 50 m²
- **Convenzione azimuth Google**: 0° = Nord, 90° = Est, 180° = Sud, 270° = Ovest
- **Conversione a PVGIS**: `pvgis_azimuth = google_azimuth - 180`
- **Filtro**: segmenti < 5 m² vengono scartati (rumore)

**Limitazione**: risoluzione satellite limita la precisione per tetti complessi. Nessuna analisi di ombreggiamento da ostacoli.

Codice: `backend/app/connectors/google_solar.py` *(privato — service layer)*

### 2.3 ARERA (Autorità di Regolazione per Energia Reti e Ambiente)

- **Fonte**: pubblicazioni trimestrali ARERA per le componenti del prezzo elettrico
- **Valori di riferimento Q1 2026** (codice: `src/kilowattchiaro_engine/models/price_components.py`):

| Componente | EUR/kWh | Peso |
|---|---|---|
| Wholesale (PUN + dispacciamento) | 0,137 | ~49% |
| Rete (trasporto + contatore) | 0,062 | ~22% |
| Oneri di sistema (ASOS + ARIM) | 0,030 | ~11% |
| Imposte (accise + addizionali) | 0,028 | ~10% |
| Margine fornitore | 0,022 | ~8% |
| **Totale** | **0,279** | 100% |

### 2.4 Mistral AI

- **Utilizzo**: solo OCR/estrazione dati da bollette e preventivi caricati
- **Modello**: `mistral-small-latest`, max 1024 token output, timeout 15s
- **NON utilizzato** in alcun calcolo finanziario — esclusivamente per estrazione strutturata di valori
- Mistral non utilizza i dati inviati tramite API per l'addestramento dei propri modelli

---

## 3. Pipeline di Input

### 3.1 Tre percorsi di inserimento consumi

1. **Preset 1-click**: l'utente seleziona un profilo domestico tipico (es. "Famiglia con figli")
2. **Stimatore a 5 domande**: questionario su occupanti, pompa di calore, AC, scaldacqua, occupazione diurna → stima deterministica (nessun LLM)
3. **Inserimento manuale**: l'utente fornisce kWh annui + opzionalmente F1/F2/F3

Tutti e tre i percorsi producono un oggetto `ConsumptionProfile`:

```
annual_kwh:          float  (consumo annuo totale, obbligatorio)
f1_kwh:              float  (fascia F1: Lun–Ven 8–19)
f2_kwh:              float  (fascia F2: Lun–Ven 7–8/19–23, Sab 7–23)
f3_kwh:              float  (fascia F3: notti, domeniche, festivi)
annual_cost_eur:     float  (costo annuo totale, opzionale)
potenza_impegnata_kw: float (potenza contrattuale, default 3,0 kW)
```

### 3.2 Dati del tetto

- Da **Google Solar API**: segmenti con inclinazione, azimuth, area
- Da **inserimento manuale**: l'utente specifica inclinazione e orientamento
- **Default** (se nessun dato): 35° inclinazione, esposto a sud, 50 m² area

### 3.3 Derivazione della tariffa all-in

Se l'utente fornisce `annual_cost_eur`:
```
all_in_rate = annual_cost_eur / annual_kwh
```
Altrimenti: `all_in_rate = 0,25 EUR/kWh` (default residenziale italiano).

---

## 4. Modello di Stima dei Consumi

### 4.1 Sette archetipi domestici italiani

Basati su cluster centroids RSE (Besagni et al. 2020), profili standard ARERA TIC, e progetto MICENE.

| ID | Etichetta | Range kWh/anno | Potenza kW | Caratteristica |
|---|---|---|---|---|
| `single_retiree` | Pensionato solo | 1.200–1.800 | 3,0 | Carico basso, picchi moderati |
| `couple_working` | Coppia lavoratrice | 2.000–2.800 | 3,0 | Assente di giorno, picco serale 19–22 |
| `family_children` | Famiglia con figli | 2.800–4.000 | 4,5 | Picco serale massimo, pomeriggio figli a casa |
| `work_from_home` | Lavoro da casa | 2.500–3.500 | 3,0 | Carico elevato 9–18, meno contrasto serale |
| `heat_pump` | Pompa di calore | 4.000–6.500 | 6,0 | Consumo invernale dominante (fattore 2×) |
| `ac_heavy` | Condizionamento pesante | 3.000–5.000 | 4,5 | Consumo estivo dominante (fattore 1,8×) |
| `energy_poor` | Povertà energetica | 800–1.200 | 1,5 | Profilo piatto, variazione minima |

Codice: `src/kilowattchiaro_engine/load_profiles/archetypes.py`

### 4.2 Profilo orario di carico

Ogni archetipo definisce:
- **24 pesi orari feriali** (Lun–Ven)
- **24 pesi orari weekend** (Sab–Dom)
- **12 fattori stagionali** (moltiplicatori mensili, range 0,5–2,0)
- **Carico base** (consumo minimo notturno, 0,03–0,06)

Generazione per ciascuna delle 8.760 ore dell'anno:
```
valore_h = (carico_base + forma_oraria[ora]) × fattore_stagionale[mese]
```
Tutti i 8.760 valori vengono normalizzati affinché la somma = 1,0.

Esempio — `couple_working` (profilo feriale):
```
Ore 0–6:   0,04–0,06  (notte, carico minimo)
Ore 7–8:   0,12       (preparazione mattutina)
Ore 9–17:  0,04–0,05  (lavoro, carico molto basso)
Ore 18–19: 0,10–0,22  (rientro)
Ore 19–22: 0,22–0,38  (picco serale: cucina, TV)
Ore 23:    0,10       (tarda serata)
```

### 4.3 Selezione dell'archetipo (percorso stimatore)

Funzione di scoring basata sulle caratteristiche del nucleo familiare:

| Criterio | Punteggio se match | Punteggio se non match |
|---|---|---|
| Pompa di calore | +10 | -3 (fino a -8 se archetipo heat_pump) |
| Condizionamento (senza PdC) | +6 | — |
| Occupazione diurna | +4 per corrispondenza | -4 per mismatch |
| Numero occupanti | +5 per fascia corretta | -5 per fascia distante |
| Povertà energetica | +2 se 1 occupante | — |

L'archetipo con il punteggio più alto viene selezionato.

Codice: `src/kilowattchiaro_engine/household_estimation.py`

### 4.4 Scalatura dei consumi

1. **Punto base**: midpoint del range dell'archetipo `(min + max) / 2`
2. **Aggiustamento occupanti**: scalatura a rendimenti decrescenti (Besagni & Borgarello 2018, Eurostat)

   | Occupanti | Fattore | Delta |
   |-----------|---------|-------|
   | 1 | 0,85 | −15% |
   | 2 (base) | 1,00 | — |
   | 3 | 1,15 | +15% |
   | 4 | 1,25 | +10% |
   | 5+ | +8% ciascuno | decrescente |
3. **Modificatori elettrodomestici**:
   - Scaldacqua elettrico a resistenza: +1.500 kWh/anno
   - Scaldacqua a pompa di calore: +700 kWh/anno
   - Cottura elettrica: +300 kWh/anno
4. **Clamping**: il risultato è limitato a `[min_archetipo, max_archetipo × 1,25]`
5. **Ripartizione F1/F2/F3**: dalle frazioni di banda dell'archetipo
6. **Stima costo**: `annual_kwh × 0,25 EUR/kWh`

### 4.5 Classificazione fasce orarie ARERA

Le fasce di consumo seguono la classificazione ufficiale ARERA:

| Fascia | Giorni | Ore | Tipologia |
|---|---|---|---|
| **F1** (peak) | Lun–Ven (non festivi) | 8:00–19:00 | Più costosa |
| **F2** (mid) | Lun–Ven | 7:00–8:00 e 19:00–23:00 | Intermedia |
| | Sabato | 7:00–23:00 | |
| **F3** (off-peak) | Tutti gli altri orari | Notti, Domeniche, festivi | Meno costosa |

I festivi nazionali italiani sono codificati (1 gen, 6 gen, 25 apr, 1 mag, 2 giu, 15 ago, 1 nov, 8 dic, 25 dic, 26 dic, Lunedì dell'Angelo).

Codice: `src/kilowattchiaro_engine/tariff.py`

---

## 5. Stima della Produzione Fotovoltaica

### 5.1 Produzione mensile (PVGIS PVcalc)

Parametri della richiesta PVGIS:
- Tecnologia: silicio cristallino
- Montaggio: integrato in edificio
- Perdite di sistema: **14%** (BOS, cavi, inverter, mismatch, sporco)
- Tilt e azimuth: dal rilevamento tetto o default (35° sud)

La risposta fornisce 12 valori `e_m` (kWh/mese) per la potenza richiesta.

### 5.2 Produzione oraria (PVGIS TMY)

Quando disponibile, il profilo TMY fornisce 8.760 valori orari di GHI (Global Horizontal Irradiance, W/m²). Conversione in potenza:

```
kW_h = GHI_Wm2 × kWp × efficienza_sistema / 1000
```

Dove `efficienza_sistema = 0,86` (equivalente a 14% di perdite totali).

### 5.3 Gestione tetti multi-segmento

Per tetti con più segmenti rilevati:

1. **Compressione** (se >8 segmenti): merging agglomerativo basato su distanza:
   ```
   distanza = delta_azimuth + (delta_tilt × 3,0)
   ```
   I segmenti più simili vengono fusi fino a raggiungere ≤8 segmenti.

2. **Chiamate PVGIS pesate**: per ogni segmento compresso:
   ```
   kWp_pesato = kWp_desiderato × (area_segmento / area_totale)
   ```
   Ogni segmento viene valutato separatamente su PVGIS.

3. **Aggregazione mensile**: i risultati mensili dei segmenti vengono sommati.

### 5.4 Distribuzione mensile dei consumi

Quando non è disponibile il matching orario, i consumi annuali vengono distribuiti mensilmente con pesi fissi:

| Mese | Gen | Feb | Mar | Apr | Mag | Giu | Lug | Ago | Set | Ott | Nov | Dic |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **Peso** | 10,0% | 9,0% | 8,5% | 7,5% | 7,0% | 6,5% | 6,5% | 6,5% | 7,5% | 8,0% | 9,5% | 13,5% |

Questo profilo riflette un'abitazione italiana tipica con picchi invernali (riscaldamento) e consumi ridotti in estate.

Codice: `src/kilowattchiaro_engine/solar_eval/`

---

## 6. Modello di Autoconsumo

### 6.1 Inferenza del profilo di carico da bolletta

Il sistema rileva automaticamente il tipo di contratto dall'input:
- **Triorario** (F1 + F2 + F3 forniti): matching basato su chi-quadrato delle tre frazioni
- **Biorario** (F1 + F23): matching su due frazioni
- **Solo annuale**: matching per potenza impegnata e range di consumo

Formula chi-quadrato per la selezione dell'archetipo:
```
score = Σ ((ratio_bolletta - ratio_archetipo)² / (ratio_archetipo + 10⁻⁹))
```

Dopo la selezione, il profilo viene scalato per banda:
1. Calcolo somme grezze per ciascuna fascia
2. Fattore di scala = `kWh_bolletta_fascia / kWh_grezzo_fascia`
3. Applicazione per-fascia al profilo 8.760 ore
4. Capping dei picchi a `potenza_impegnata × 1,1`

Codice: `src/kilowattchiaro_engine/load_profiles/infer.py`

### 6.2 Algoritmo di matching orario

Algoritmo greedy ora per ora su 8.760 ore annuali:

```
Per ogni ora h:
  1. Autoconsumo diretto:
     self_from_pv = min(carico_h, produzione_h)

  2. Surplus = produzione_h - self_from_pv
     Deficit = carico_h - self_from_pv

  3. Se batteria presente E surplus > 0:
     capacità_disponibile = battery_kwh - SOC
     energia_carica = min(surplus, capacità_disponibile / η_carica, C_rate × battery_kwh)
     SOC += energia_carica × η_carica

  4. Se batteria presente E deficit > 0:
     scarica_max = min(deficit / η_scarica, SOC, C_rate × battery_kwh)
     energia_al_carico = scarica_max × η_scarica
     SOC -= scarica_max

  5. Cessione rete = surplus residuo (dopo carica batteria)
     Prelievo rete = deficit residuo (dopo scarica batteria)
```

Output dell'algoritmo:
- `self_consumption_kwh`: 8.760 valori orari di autoconsumo
- `grid_import_kw`: 8.760 valori orari di prelievo dalla rete
- `grid_export_kw`: 8.760 valori orari di cessione alla rete
- `battery_state_kwh`: 8.760 valori di stato di carica (SOC)
- `self_consumption_ratio`: rapporto annuale autoconsumo / produzione

Codice: `src/kilowattchiaro_engine/hourly_match.py`

### 6.3 Modello batteria

| Parametro | Valore | Fonte |
|---|---|---|
| Chimica | LFP (Litio-Ferro-Fosfato) | Standard residenziale |
| Efficienza round-trip | 90% | Specifiche produttore |
| Efficienza carica | √0,90 ≈ 94,87% | Modello simmetrico |
| Efficienza scarica | √0,90 ≈ 94,87% | Modello simmetrico |
| C-rate | 0,5 | Carica/scarica max = 50% capacità/ora |
| SOC | Limitato a [0, capacità] | Nessun DoD minimo |

Codice: `src/kilowattchiaro_engine/hourly_match.py`

### 6.4 Rapporti di default (fallback)

Quando il matching orario non è disponibile (dati TMY mancanti o errore):

| Configurazione | Rapporto autoconsumo | Fonte |
|---|---|---|
| Solo fotovoltaico | **30%** | Consenso industria, conservativo |
| Fotovoltaico + batteria | **70%** | Consenso industria |

Codice: `src/kilowattchiaro_engine/config.py`

---

## 7. Modello Finanziario

### 7.1 Investimento

```
investimento_fotovoltaico = kWp × costo_pannello_EUR_per_kWp
investimento_batteria     = costo_batteria_EUR  (se inclusa)
investimento_totale       = investimento_fotovoltaico + investimento_batteria
```

Valori di default:
- Costo pannello: **€1.700/kWp** (installato, chiavi in mano — mercato italiano 2026, fonti: IEA-PVPS, Viessmann Italia, LONGi EU)
- Costo batteria: **€8.000** (10 kWh LFP, installata)

### 7.2 Detrazione fiscale IRPEF

```
detrazione_totale  = min(investimento_totale × 0,50, €48.000)
detrazione_annuale = detrazione_totale / 10
```

- Tasso: **50%** dell'investimento
- Tetto massimo: **€48.000** di detrazione (da €96.000 di spesa ammissibile)
- Durata: **10 rate annuali** di pari importo
- Base normativa: Art. 16-bis TUIR, Legge di Bilancio 2026

### 7.3 Flussi di cassa annuali

Per ogni anno `t` da 1 a 20:

**Degradazione**:
```
fattore_pannello_t  = (1 - 0,005)^(t-1)          // 0,5%/anno
fattore_batteria_t  = (1 - 0,02)^(età_batteria-1)  // 2,0%/anno
```

**Produzione e autoconsumo degradati**:
```
autoconsumo_kWh_t = autoconsumo_anno1 × fattore_pannello_t
                    + uplift_batteria × fattore_pannello_t × fattore_batteria_t
cessione_kWh_t    = cessione_anno1 × fattore_pannello_t
                    - riduzione_batteria × fattore_pannello_t × fattore_batteria_t
prelievo_kWh_t    = max(0, consumo_SQ - autoconsumo_kWh_t)
```

**Flusso di cassa annuale**:
```
CF_t = (P_retail_t × C_SQ) - (P_retail_t × prelievo_kWh_t)
       + P_RID_t × cessione_kWh_t
       - manutenzione
       + detrazione_annuale × 𝟙{t ≤ 10}
       - costo_inverter/3 × 𝟙{10 ≤ t ≤ 12}
       - costo_batteria/3 × 𝟙{13 ≤ t ≤ 15}
```

Dove:
- `P_retail_t` = prezzo retail proiettato all'anno t (vedi Sezione 8)
- `P_RID_t` = max(prezzo_minimo_garantito, prezzo_wholesale_proiettato_t)
- `C_SQ` = consumo annuo stato attuale (costante)
- `𝟙{condizione}` = 1 se condizione vera, 0 altrimenti

### 7.4 Valore Attuale Netto (NPV)

```
NPV = -I₀ + Σ(t=1..20) CF_t / (1 + r)^t
```

Dove:
- `I₀` = investimento totale (pannelli + eventuale batteria)
- `CF_t` = flusso di cassa anno t
- `r` = tasso di sconto (default **3%**)
- Orizzonte: **20 anni**

Codice: `src/kilowattchiaro_engine/solar_eval/npv.py` (funzione `_npv_20yr`)

### 7.5 Tasso Interno di Rendimento (IRR)

Risolto per bisezione: trova il tasso `r` tale che `NPV(r) = 0`.

```
NPV(r) = -I₀ + Σ(t=1..20) CF_t / (1 + r)^t = 0
```

- Range di ricerca: [-10%, +100%]
- Tolleranza: |high - low| < 0,0001
- Iterazioni massime: 100
- Risultato espresso in percentuale (es. 8,5%)

Codice: `src/kilowattchiaro_engine/solar_eval/npv.py` (funzione `_estimate_irr`)

### 7.6 Tempo di Ritorno (Payback)

Accumulazione iterativa dei flussi di cassa non attualizzati:

```
cumulativo = 0
Per anno = 1..25:
  cumulativo += CF_anno
  Se cumulativo ≥ investimento:
    frazione = (cumulativo - investimento) / CF_anno
    payback = anno - frazione
    return arrotonda(payback, 1)
return null  // non recuperato in 25 anni
```

- Orizzonte esteso a **25 anni** per scenari con rientro lento
- Interpolazione lineare nell'anno di pareggio

Codice: `src/kilowattchiaro_engine/solar_eval/payback.py` (funzione `_payback_years`)

### 7.7 Costi ricorrenti e una tantum

| Voce | Importo | Anno | Note |
|---|---|---|---|
| Manutenzione annuale | **€75/anno** | Ogni anno | Pulizia, ispezione, piccole riparazioni |
| Sostituzione inverter | **€1.750** (€583/anno × 3) | **Anni 10-12** | Distribuita su 3 anni centrati sull'anno tipico di fine vita |
| Sostituzione batteria | Costo batteria (÷3) | **Anni 13-15** | Distribuita su 3 anni centrati sull'anno tipico di fine vita LFP |

**Nota**: i costi di sostituzione sono distribuiti su 3 anni anziché concentrati in un singolo anno, per riflettere l'incertezza sul momento esatto del guasto e rendere il flusso di cassa più realistico per la pianificazione finanziaria. I costi di manutenzione sono espressi in termini nominali e non vengono indicizzati all'inflazione lungo l'orizzonte di analisi (v. Sezione 10, limitazione #9).

---

## 8. Scenari di Proiezione Prezzi

### 8.1 Decomposizione del prezzo elettrico

Il prezzo retail viene decomposto in 5 componenti indipendenti, ciascuna con una propria regola di escalation:

| Componente | Valore base Q1 2026 | Regola di escalation |
|---|---|---|
| Wholesale (PUN + disp.) | €0,137/kWh | CPI + spread scenario |
| Rete (trasporto + contatore) | €0,062/kWh | CPI + 0,5% reale |
| Oneri di sistema | €0,030/kWh | CPI - 2,0% reale |
| Imposte (accise) | €0,028/kWh | CPI − 1% reale (leggera riduzione reale) |
| Margine fornitore | €0,022/kWh | CPI (piatto in termini reali) |

CPI di default: **2,0%** annuo (target BCE).

Codice: `src/kilowattchiaro_engine/price_projection.py`

### 8.2 Regole di escalation condivise

- **Rete** (CPI + 0,5%): trend storico ARERA di graduale aumento per adeguamento infrastrutturale
- **Oneri di sistema** (CPI - 2%): rolloff progressivo degli incentivi Conto Energia, che si esauriranno intorno al 2037
- **Imposte** (CPI − 1% reale): leggera riduzione reale delle accise nel tempo, basata su analisi ECCO Climate e trend ARERA
- **Margine fornitore** (CPI): piatto in termini reali, nessuna compressione/espansione

### 8.3 Cinque scenari deterministici

Solo la componente wholesale varia tra gli scenari:

| Scenario | Spread wholesale | Descrizione | Fonte |
|---|---|---|---|
| **ENTSO-E Reference** | CPI + 1,0% | Elettrificazione moderata | TYNDP 2024 |
| **High Electrification** | CPI + 2,0% | Raddoppio domanda MASE 2050 | Piano MASE |
| **Abundant Renewables** | CPI - 2,0% | 79,2 GW FV, cannibalizzazione prezzi | PNIEC |
| **Status Quo** | CPI + 0,0% | Prezzi reali piatti (conservativo) | Baseline |
| **Energy Crisis** | CPI + 1,5% | Media elevata, picchi periodici | ~2022 ogni 8 anni |

Codice: `src/kilowattchiaro_engine/price_projection.py`

### 8.4 Simulazione Monte Carlo (opzionale)

Per l'analisi avanzata, il motore supporta simulazioni stocastiche:

- **Wholesale**: processo Ornstein-Uhlenbeck geometrico (mean-reverting, log-space, nessun prezzo negativo)
  - θ (velocità mean-reversion) = 0,5
  - σ (volatilità annuale) = 0,30
  - 12 sub-step mensili per anno
- **Componenti regolate**: deterministiche + rumore gaussiano (σ = 0,005)
- **Output**: bande percentili P10/P25/P50/P75/P90 da N simulazioni (default 1.000)
- **Vincoli**: wholesale capped a 5× base, floor a 0,01 EUR/kWh

Codice: `src/kilowattchiaro_engine/price_projection.py`

---

## 9. Confronto Preventivi

### 9.0 Percorso di valutazione attivo

La pagina `/preventivo` usa l'agente esterno KiloWattChiaro per leggere e
strutturare un singolo preventivo. OCR, riconoscimento dei componenti, retrieval
delle schede e sintesi servono a ricostruire ciò che il documento afferma; non
sono autorità di calcolo. Produzione, dimensionamento, prezzo e rientro economico
restano deterministici e vengono calcolati dagli strumenti MCP
`solar_evaluate_v2` e `quote_compare_v1` descritti in questa sezione.

Se il documento non contiene un totale pagabile, mescola fotovoltaico con beni
non confrontabili o non identifica un componente, il relativo verdetto viene
omesso invece di imputare zero o inventare un prodotto. Il report mostra al
massimo cinque domande deduplicate e mantiene le evidenze tecniche in una sezione
facoltativa. Il confronto multi-preventivo è temporaneamente disattivato.

### 9.1 Benchmark di mercato

| Soglia | EUR/kWp |
|---|---|
| Floor competitivo | €1.400 |
| Stima centrale | €1.700 |
| Ceiling premium | €2.500 |

### 9.2 Verdetti di costo

| Verdetto | Condizione EUR/kWp |
|---|---|
| `below_market` | < €1.200 (sospettosamente basso) |
| `competitive` | ≤ €1.550 |
| `fair` | ≤ €1.870 |
| `above_market` | ≤ €2.500 |
| `overpriced` | > €2.500 |

### 9.3 Red flag automatiche

| Flag | Severità | Condizione |
|---|---|---|
| Prezzo eccessivo | CRITICAL | > €2.500/kWp |
| Prezzo sospettosamente basso | WARNING | < €1.200/kWp |
| Produzione molto gonfiata | CRITICAL | > 130% della stima PVGIS |
| Produzione gonfiata | WARNING | > 115% della stima PVGIS |
| Impianto sovradimensionato | WARNING | kWp > 130% del raccomandato |
| Impianto sottodimensionato | INFO | kWp < 70% del raccomandato |
| Payback sottostimato | WARNING | Payback < nostro payback - 2 anni |
| Garanzia pannelli insufficiente | WARNING | < 10 anni garanzia prodotto |
| Garanzia inverter insufficiente | WARNING | < 5 anni |
| Garanzia batteria insufficiente | WARNING | < 5 anni (se batteria presente) |
| IVA non inclusa | INFO | Non chiavi-in-mano |
| Detrazione non menzionata | INFO | Nessun riferimento a IRPEF 50% |
| Ricavi SSP sovrastimati | WARNING | SSP/RID > 150% della nostra stima |

### 9.4 Punteggio complessivo

| Punteggio | Condizione |
|---|---|
| `poor` | ≥ 2 flag critiche |
| `concerning` | 1 flag critica OPPURE ≥ 3 warning |
| `fair` | ≥ 1 warning |
| `good` | Nessuna warning o critica |

### 9.5 Ri-valutazione con dati installatore

Se il preventivo contiene `system_kwp`, il motore:
1. Recupera la produzione PVGIS per quel kWp sul tetto dell'utente
2. Riesegue `evaluate_solar()` con: kWp dell'installatore, costo/kWp dell'installatore (se disponibile), configurazione batteria dell'installatore
3. Confronta payback, NPV e risparmio con la valutazione originale

Codice: `app/engine/quote/` *(privato — vedi [docs/quote-engine.md](quote-engine.md))*

---

## 10. Limitazioni Note

Il modello presenta le seguenti limitazioni note. Ciascuna può causare differenze significative tra la stima e la realtà.

1. **Nessuna analisi di ombreggiamento**: il modello non considera ombreggiamento da edifici adiacenti, alberi, comignoli o altri ostacoli. La produzione potrebbe essere sovrastimata per tetti parzialmente ombreggiati.

2. **Variabilità inter-annuale**: il TMY rappresenta un anno tipico statistico. La produzione reale varia ±10% anno su anno per variazioni meteorologiche.

3. **Fallback orientamento tetto**: quando i dati Google Solar non sono disponibili, il fallback (30° sud) potrebbe non corrispondere al tetto reale.

4. **Approssimazione profilo di carico**: l'inferenza basata su archetipi è un'approssimazione statistica. Nuclei familiari individuali possono deviare significativamente dalla forma dell'archetipo.

5. **Consumi costanti nel tempo**: le quote mensili di consumo sono fisse. Non viene modellata l'evoluzione dei consumi (veicolo elettrico, pompa di calore, cambio abitudini) lungo i 20 anni.

6. **Evoluzione tariffaria semplificata**: oneri di rete e sistema sono proiettati con regole di escalation semplici. Cambiamenti regolatori strutturali (es. tariffe basate sulla potenza vs. volumetriche) non sono modellati.

7. **Degradazione batteria lineare**: il fade del 2%/anno è una semplificazione. La degradazione reale LFP dipende da profondità di ciclaggio, temperatura e invecchiamento calendariale.

8. **Nessun prezzo zonale**: il modello non differenzia tra zone di mercato PUN (Nord, Centro-Nord, Centro-Sud, Sud, Sicilia, Sardegna).

9. **Manutenzione non indicizzata**: il costo di manutenzione è fisso a €75/anno nominali per l'intero orizzonte. In termini reali, questo sottostima il costo effettivo nel tempo.

10. **Prezzo RID conservativo**: il modello usa il Prezzo Minimo Garantito GSE (€0,0475/kWh), che è il floor. Il ricavo RID reale è tipicamente superiore (prezzo zonale orario vs. PMG).

11. **Nessun costo di connessione alla rete**: si assume che la connessione esistente sia adeguata. Eventuali adeguamenti della potenza contrattuale non sono modellati.

12. **Nessun ritardo burocratico**: tempi di permesso, connessione GSE e installazione non sono fattorizzati nei rendimenti finanziari.

13. **Ipotesi di prima casa**: la detrazione al 50% si applica all'abitazione principale. Per seconde case si applica il 36% (non modellato).

14. **Nessun modello di finanziamento**: non vengono considerati interessi su prestiti o leasing per l'acquisto dell'impianto.

15. **Bias risoluzione oraria**: il matching greedy a risoluzione oraria sottostima l'autoconsumo di ~3–5 punti percentuali rispetto a simulazioni sub-minute (Quoilin et al. 2016). Questo parzialmente compensa il default ottimistico del 70% PV+batteria.

16. **Cannibalizzazione solare**: con la crescita del FV italiano verso 80+ GW, i capture rate solari al mezzogiorno sono attesi in calo dal ~90% al 60–70% dei prezzi medi wholesale entro il 2035. Il modello usa una valorizzazione fissa del surplus che non cattura questo declino strutturale.

17. **EU ETS2 dal 2027**: l'estensione del carbon pricing a edifici e trasporti influenzerà i prezzi elettrici (al rialzo via costi gas) e i costi di riscaldamento a gas (migliorando l'economia relativa del FV). Questo shift strutturale non è catturato dall'escalation basata su CPI.

18. **Valore residuo fine vita**: pannelli e batterie conservano un valore residuo a anno 20 che non viene modellato. Questa è un'approssimazione conservativa che sottostima leggermente i rendimenti reali.

19. **Drift di implementazione note (ancora aperte)**. Audit interno 2026-04-13 ha identificato quattro divergenze tra questa metodologia e l'implementazione live; sono da risolvere prima di pubblicare claim quantitativi più forti sui risultati:

    - **Percorso orario non roof-aware**: quando `tmy_data` è disponibile, `evaluate_solar()` costruisce il profilo orario solo da `G(h)`, `kWp` e un `system_efficiency=0.86` codificato; `tilt` e `azimuth` non vengono propagati e `HourlyProductionProfile` viene creato con placeholder `lat=0, lon=0, tilt=0, azimuth=0` (`src/kilowattchiaro_engine/solar_eval/`, `src/kilowattchiaro_engine/pvgis.py`).
    - **Tetti multi-segmento bypass del matching orario**: il path multi-segmento aggrega solo i mensili PVGIS per segmento, non scarica TMY, e `evaluate_solar()` ricade silenziosamente sui ratio di autoconsumo di default 30/70 (`backend/app/api/solar_eval.py` *(privato — service layer)*, `src/kilowattchiaro_engine/solar_eval/`). Il campo `self_consumption_source` già emesso dal motore va esposto in UI ed esteso a coprire single-vs-multi-segment e hourly-vs-fallback.
    - **Risparmi calcolati su tariffa all-in**: il motore deriva `all_in_rate = annual_cost_eur / annual_kwh` (default 0.25) e applica l'intera tariffa sia allo status quo sia al post-solare, trattando i kWh evitati come se evitassero anche le componenti fisse non evitabili (`src/kilowattchiaro_engine/solar_eval/, 543-565`). Sovrastima dei risparmi stimata ~15-20% — coerente con la nota `lessons.md` "fixed vs variable costs".
    - **Bias di risoluzione oraria**: il matching greedy a risoluzione oraria sottostima l'autoconsumo di ~3-5 punti percentuali rispetto a simulazioni sub-minute (Quoilin et al. 2016) — già coperto al punto §10.15 ma menzionato qui per completezza dell'audit.

20. **Ipotesi di singola abitazione**: l'intero motore modella **una singola unità abitativa** — l'estimatore accetta al massimo 10 occupanti e satura il consumo al tetto dell'archetipo × 1,25 (max ~8.100 kWh/anno), gli archetipi RSE sono tutti monofamiliari, e l'analisi economica assume un impianto personale con detrazione individuale. Edifici multiunità (condomini), impianti condominiali condivisi e comunità energetiche (CER) — con ripartizione millesimale, delibera assembleare e incentivi dedicati (tariffa TIP GSE) — **non sono modellati**. Dal 2026-08 il wizard lo dichiara esplicitamente (gate "tipo di edificio") e offre una waitlist per il futuro supporto condomini; il percorso "impianto individuale su tetto condominiale" resta possibile con avvertenze. Vedi `docs/qa/condominio-dead-end-diagnosis-2026-08.md`.

---

## 11. Validazione e Backtest

### 11.1 Metodologia

Il motore include un framework di backtesting storico walk-forward (senza look-ahead bias):

1. Per ciascun **decision point** storico (es. 1 gennaio 2015, 2020, ecc.):
   - Si raccolgono solo le informazioni disponibili a quella data
   - Si esegue la valutazione con i parametri dell'epoca:
     - Costo PV al momento della decisione (curva IRENA/GSE)
     - Prezzo elettricità al momento della decisione (Eurostat/ARERA)
     - Regime incentivante attivo (Conto Energia I–V, Detrazione 50%, Superbonus 110%)
     - Meccanismo di cessione attivo (SSP, RID, tariffa incentivante)
   - Si confronta la previsione con i risultati reali (traiettoria prezzi effettiva fino al 2025)

2. **Metriche calcolate**:
   - Errore NPV (medio, mediano, massimo, %)
   - Accuratezza binaria (raccomandazione corretta sì/no)
   - Errore payback (anni)
   - Errore IRR (punti percentuali)
   - Correlazione di Spearman (accuratezza direzionale del ranking)
   - Accuratezza per tipo di regime incentivante

### 11.2 Decision points

I decision points coprono tutti i regimi incentivanti italiani dal 2008 al 2025, permettendo di valutare la robustezza del modello in contesti regolatori diversi.

Codice: `src/kilowattchiaro_engine/historical_backtest.py`, `src/kilowattchiaro_engine/validation.py`

---

## 12. Registro delle Assunzioni

Tabella consolidata di tutti i valori predefiniti con le relative fonti.

### 12.1 Costi e investimento

| Parametro | Valore | Fonte | Codice |
|---|---|---|---|
| Costo pannelli installati | €1.700/kWp | IEA-PVPS 2024, Viessmann Italia, LONGi EU | `config.py:74` |
| Costo batteria 10 kWh LFP | €8.000 | Indagine mercato 2025-2026 | `config.py:75` |
| Manutenzione annuale | €75/anno | Media industria | `config.py:79` |
| Sostituzione inverter | €1.750 distribuita su anni 10-12 | Prezzi di mercato | `config.py:80-81` |
| Sostituzione batteria | Distribuita su anni 13-15 | Ciclo vita LFP moderno: 6.000+ cicli | `config.py:82` |
| Benchmark costo floor | €1.400/kWp | Mercato italiano 2026 | `quote_comparison.py:94` |
| Benchmark costo central | €1.700/kWp | Mercato italiano 2026 | `quote_comparison.py:96` |
| Benchmark costo ceiling | €2.500/kWp | Mercato italiano 2026 | `quote_comparison.py:95` |

### 12.2 Parametri tecnici

| Parametro | Valore | Fonte | Codice |
|---|---|---|---|
| Degradazione pannelli | 0,5%/anno | IEC 61215, garanzie produttore | `config.py:83` |
| Fade batteria | 2,0%/anno lineare | Letteratura invecchiamento LFP | `config.py:84` |
| Perdite di sistema PVGIS | 14% | Default PVGIS (BOS+cavi+inverter) | `pvgis.py` |
| Efficienza round-trip batteria | 90% | Standard LFP (HTW Berlin) | `hourly_match.py:20` |
| Efficienza carica/scarica | √0,90 ≈ 94,9% | Simmetrica | `hourly_match.py:21-22` |
| C-rate batteria | 0,5 | Standard residenziale LFP | `hourly_match.py:23` |
| Autoconsumo senza batteria (fallback) | 30% | Consenso industria, conservativo | `config.py:77` |
| Autoconsumo con batteria (fallback) | 70% | Consenso industria | `config.py:78` |

### 12.3 Modello finanziario

| Parametro | Valore | Fonte | Codice |
|---|---|---|---|
| Tasso di sconto | 3% reale | Costo opportunità residenziale IT | `solar_eval.py:85` |
| Sensibilità NPV | 3%, 5%, 8% | Letteratura IDR famiglie EU | `solar_eval.py:60` |
| Detrazione fiscale | 50% | Legge di Bilancio 2026 | `solar_eval.py:113` |
| Tetto detrazione | €48.000 | Legge di Bilancio 2026 | `solar_eval.py:113` |
| Durata detrazione | 10 anni | Art. 16-bis TUIR | `solar_eval.py:114` |
| Orizzonte di analisi | 20 anni | Standard analisi PV | `solar_eval.py:41` |
| Orizzonte payback | 25 anni | Esteso per scenari lenti | `solar_eval.py:42` |
| Tariffa retail all-in | €0,25/kWh | ARERA Q1 2026 | `config.py:73` |
| Prezzo RID minimo garantito | €0,0475/kWh | GSE PMG 2026 | `config.py:76` |

### 12.4 Stima consumi

| Parametro | Valore | Fonte | Codice |
|---|---|---|---|
| Scaldabagno elettrico (resistenza) | +1.500 kWh/anno | Letteratura, dati di mercato | `household_estimation.py:21` |
| Scaldacqua a pompa di calore | +700 kWh/anno | Letteratura, dati di mercato | `household_estimation.py:22` |
| Cottura elettrica / induzione | +300 kWh/anno | Stime conservative | `household_estimation.py:23` |
| Scalatura occupanti 3ª persona | +15% | Besagni & Borgarello 2018, Eurostat | `household_estimation.py:26` |
| Scalatura occupanti 4ª persona | +10% | Besagni & Borgarello 2018, Eurostat | `household_estimation.py:26` |
| Scalatura occupanti 5ª+ persona | +8% ciascuno | Besagni & Borgarello 2018, Eurostat | `household_estimation.py:26` |

### 12.5 Proiezione prezzi

| Parametro | Valore | Fonte | Codice |
|---|---|---|---|
| CPI assunto | 2,0%/anno | Target BCE | `price_projection.py:42` |
| Wholesale base Q1 2026 | €0,137/kWh | ARERA Q1 2026 | `price_components.py:226` |
| Rete base Q1 2026 | €0,062/kWh | ARERA Q1 2026 | `price_components.py:227` |
| Oneri sistema base Q1 2026 | €0,030/kWh | ARERA Q1 2026 | `price_components.py:228` |
| Imposte base Q1 2026 | €0,028/kWh | ARERA Q1 2026 | `price_components.py:229` |
| Margine fornitore base | €0,022/kWh | Stima mercato | `price_components.py:230` |
| Escalation rete | CPI + 0,5% reale | ARERA trend storico | `price_projection.py:93` |
| Escalation oneri sistema | CPI − 2,0% reale | Rolloff Conto Energia | `price_projection.py:94` |
| Escalation imposte | CPI − 1,0% reale | ECCO Climate, ARERA | `price_projection.py:95` |
| Escalation margine fornitore | CPI (piatto reale) | Stima | `price_projection.py:96` |
| Spread ENTSO-E Reference | CPI + 1,0% | TYNDP 2024 | `price_projection.py:50` |
| Spread High Electrification | CPI + 2,0% | Piano MASE | `price_projection.py:56` |
| Spread Abundant Renewables | CPI − 2,0% | PNIEC | `price_projection.py:62` |
| Spread Status Quo | CPI + 0,0% | Baseline conservativo | `price_projection.py:68` |
| Spread Energy Crisis | CPI + 1,5% | ~2022 ogni 8 anni | `price_projection.py:74` |

### 12.6 Monte Carlo

| Parametro | Valore | Fonte | Codice |
|---|---|---|---|
| θ (mean-reversion) | 0,5 | Lucia & Schwartz 2002, Weron 2014 | `price_components.py:166` |
| σ (volatilità annuale) | 0,30 | Mercato italiano post-liberalizzazione | `price_components.py:167` |
| σ regolato | 0,005 | Rumore gaussiano componenti regolate | `price_components.py:169` |
| N simulazioni (default) | 1.000 | Bilanciamento precisione/performance | `price_components.py:164` |

---

*Ultimo aggiornamento: 22 marzo 2026*
