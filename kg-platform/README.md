# AI-powered Knowledge Graph Analytics Platform

Plateforme complète décrite dans le cahier des charges : **sources hétérogènes → crawler → HDFS → extraction LLM +
résolution d'entités → Spark ETL → Neo4j → (Analytique de graphe | GraphRAG) → évaluation comparative**, avec API,
interface, monitoring et contrôle d'accès.

```
Sources (fichiers PDF/Word/HTML/JSON, e-mails .eml, base SQL, sites web, API)
   │  1. kgp/crawler.py            collecte + parsing + métadonnées (sha256, robots.txt)
   ▼
HDFS  /data/raw
   │  2. kgp/extraction.py         LLM (Ollama / Claude / OpenAI / mock) -> entités + relations + preuve
   │     kgp/resolution.py         résolution d'entités (normalisation, fuzzy, initiales, acronymes)
   ▼
HDFS  /data/extracted -> /data/resolved
   │  3a. spark/spark_etl.py       validation, déduplication, agrégation (support, confiance, sources)
   ▼
HDFS  /data/curated
   │  3b. kgp/load_neo4j.py        MERGE idempotent, contraintes, index plein texte
   ▼
Neo4j  (un seul graphe de connaissances)
   ├─ 4. kgp/analytics.py          Cypher + Neo4j GDS (PageRank, Louvain, betweenness, plus court chemin)
   └─ 5. kgp/graphrag.py           liaison d'entités -> sous-graphe k-sauts -> contexte -> LLM + provenance
                   │
                   ▼
           6. kgp/evaluate.py      précision, rappel, latence, coût, explicabilité -> reports/benchmark.md
           7. kgp/api.py + ui/     API FastAPI (clés + rôles, audit, /metrics Prometheus) + interface Streamlit
```

Le corpus d'exemple est **fictif** (aucune personne réelle) : 10 documents (txt, html, pdf, docx, eml, json, 4 extraits SQL)
et 44 triplets de référence (`data/ground_truth/triples.json`) + 15 questions (`questions.json`).

---

## 1. Prérequis

- Docker Desktop (Windows/macOS) ou Docker Engine + Compose v2 (Linux). **8 Go de RAM** alloués à Docker minimum.
- Ports libres : 9870 (HDFS), 7474/7687 (Neo4j), 8000 (API), 8501 (UI).
- Internet au premier lancement (images Docker, plugin Neo4j GDS).
- Mac Apple Silicon : si une image Hadoop refuse de démarrer, ajoutez `platform: linux/amd64` aux services `namenode` et `datanode`.

## 2. Exécution pas à pas (Docker)

Depuis le dossier du projet (le fichier `.env` est déjà prêt) :

```bash
# 1) Démarrer HDFS, Neo4j, API, UI (la 1re fois : plusieurs minutes de téléchargement)
docker compose up -d --build

# 2) Attendre que HDFS et Neo4j soient prêts + créer les dossiers HDFS
docker compose run --rm tools python -m kgp.pipeline wait

# 3) Phase 1 : crawler -> HDFS
docker compose run --rm tools python -m kgp.pipeline crawl

# 4) Phase 2 : extraction LLM + résolution d'entités
docker compose run --rm tools python -m kgp.pipeline extract
docker compose run --rm tools python -m kgp.pipeline resolve

# 5) Phase 3 : Spark ETL puis chargement dans Neo4j
docker compose --profile etl run --rm spark-etl
docker compose run --rm tools python -m kgp.pipeline load --reset

# 6) Phases 4-5 : analytique et GraphRAG
docker compose run --rm tools python -m kgp.pipeline analytics
docker compose run --rm tools python -m kgp.pipeline ask "Which investors have funded companies that partner with Orion Cloud?"

# 7) Phase 6 : benchmark  ->  reports/benchmark.md et benchmark.json
docker compose run --rm tools python -m kgp.pipeline eval
```

Avec `make` (Linux/macOS/WSL) : `make up && make pipeline && make eval`.

Interfaces :

| Service | URL |
|---|---|
| Interface Streamlit (GraphRAG, analytique, graphe, benchmark) | http://localhost:8501 |
| API (Swagger) | http://localhost:8000/docs  (en-tête `X-API-Key: demo-admin-key`) |
| Neo4j Browser | http://localhost:7474  (neo4j / `NEO4J_PASSWORD` du `.env`) |
| HDFS NameNode | http://localhost:9870 |
| Métriques Prometheus | http://localhost:8000/metrics |

Exemples Cypher dans Neo4j Browser :

```cypher
MATCH (n:Entity)-[r]->(m:Entity) RETURN n, r, m LIMIT 100;
MATCH (:Entity {name:'Cedar Capital'})-[:INVESTED_IN]->(c)-[:LOCATED_IN]->(city) RETURN c.name, city.name;
MATCH ()-[r:WORKS_FOR]->() RETURN r.sources, r.evidence, r.confidence LIMIT 5;  // provenance
```

## 3. Choisir le LLM

Par défaut `LLM_PROVIDER=mock` : extracteur à règles + GraphRAG « retrieval seul ». Cela valide toute la chaîne **sans LLM**
mais **les chiffres du benchmark ne sont alors pas représentatifs** d'un vrai GraphRAG. Pour de vrais résultats :

- **Ollama (local, gratuit)** : dans `.env` mettre `LLM_PROVIDER=ollama` (modèle `qwen2.5:7b`, ou `qwen2.5:3b` si peu de RAM), puis
  `docker compose --profile llm-local up -d ollama && docker compose exec ollama ollama pull qwen2.5:7b`.
- **Claude** : `LLM_PROVIDER=anthropic`, `ANTHROPIC_API_KEY=...` (modèle par défaut `claude-sonnet-5-5`).
- **OpenAI / compatible** : `LLM_PROVIDER=openai`, `OPENAI_API_KEY`, éventuellement `OPENAI_BASE_URL`.

Renseignez `LLM_PRICE_IN/OUT` (USD par million de tokens) pour que le benchmark estime le coût d'inférence.
Après changement : `docker compose up -d api ui` puis `extract --force`, `resolve`, ETL, `load --reset`, `eval`.

## 4. Sans Docker (test rapide)

```bash
python -m venv .venv && source .venv/bin/activate   # Windows : .venv\Scripts\activate
pip install -r requirements.txt
python -m pytest -q tests          # pipeline complet en local (stockage = dossier ./storage, extracteur mock, ETL Python)
```

Les commandes `crawl/extract/resolve/etl --engine python` fonctionnent avec `STORAGE_BACKEND=local` ; `load`, `ask`, `analytics`
et `eval` (partie QA) nécessitent Neo4j.

## 5. Questions de recherche couvertes (section 3 du cahier des charges)

1. **Précision d'extraction** : `eval` compare les triplets extraits aux 44 triplets de référence.
2. **Effet de la résolution d'entités** : même mesure avec `ENTITY_RESOLUTION` on/off (nœuds en double, F1).
3. **GraphRAG vs Cypher** : 15 questions (recherche, 1-saut, multi-sauts, agrégation, chemin, sémantique) ; les questions
   sémantiques n'ont pas de requête Cypher fixe (colonne « n/a »).
4. **Compromis** : rappel, précision, latence, tokens, coût par méthode.
5. **Provenance** : chaque relation porte `sources` (documents) et `evidence` (phrase) ; GraphRAG renvoie les faits cités.

## 6. Données personnelles, sécurité, gouvernance

- N'activer le crawler web que sur des sources **licites et autorisées** (robots.txt respecté, délai entre requêtes).
- Accès : clés API avec rôles `reader` / `admin` (`API_KEYS` dans `.env` — **changez les clés de démo**) ; `/cypher` réservé à l'admin
  et limité à la lecture ; journal d'audit dans `reports/audit.log`.
- Rétention : `python -m kgp.pipeline purge --days 90` supprime les documents bruts/extraits plus anciens.
- Les relations sont extraites automatiquement : le prompt GraphRAG impose de ne pas présenter une association inférée comme un fait vérifié.

## 7. Dépannage

| Symptôme | Piste |
|---|---|
| `wait` boucle sur HDFS | NameNode en *safe mode* au démarrage : attendre 1–2 min, voir `docker compose logs namenode datanode` |
| `api` ne démarre pas | Neo4j pas encore *healthy* (téléchargement GDS) : `docker compose logs -f neo4j` |
| Pas de GDS | Les algorithmes retombent automatiquement sur `networkx` (champ `engine` dans les résultats) |
| Extraction LLM lente/JSON invalide | Modèle plus gros, ou `CHUNK_CHARS=800` ; les chunks invalides sont ignorés et comptés |
| Spark : `UnknownHost datanode` | Vérifier que les conteneurs sont sur le même réseau compose (`docker compose ps`) |
| Repartir de zéro | `make clean` (supprime volumes HDFS et Neo4j) |

## 8. Limites connues

- Spark tourne en mode `local[*]` dans un conteneur (même code qu'en cluster : changer `--master` pour un vrai cluster).
- La résolution d'entités est en O(n²) par type : pour des millions de mentions, ajouter du blocking ou la porter dans Spark.
- Le corpus fourni est petit : le GraphRAG y voit presque tout le graphe ; les écarts avec Cypher se creusent sur un graphe plus grand.
- Le chargement Neo4j, GDS, HDFS et Ollama n'ont pas pu être exécutés dans l'environnement où le projet a été généré :
  ils sont écrits selon la documentation officielle mais à valider chez vous (voir dépannage).

## 9. Structure

```
docker-compose.yml  .env  Makefile  requirements.txt  hadoop.env
config/sources.yaml            sources du crawler
data/sources/…                 corpus d'exemple      data/ground_truth/…  vérité terrain
kgp/                           crawler, extraction, resolution, etl_python, load_neo4j, analytics, graphrag, evaluate, api, pipeline
spark/spark_etl.py             job Spark          ui/app.py   interface Streamlit
scripts/generate_samples.py    régénère PDF/DOCX/SQLite      tests/   pytest
```
