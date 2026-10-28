# Shortcuts (Linux/macOS/WSL). Windows without make: copy the commands from the README.
T = docker compose run --rm tools python -m kgp.pipeline

up:            ## build + start HDFS, Neo4j, API, UI
	docker compose up -d --build
wait:          ## wait for HDFS + Neo4j and create the HDFS folders
	$(T) wait
crawl:         ## step 1
	$(T) crawl
extract:       ## step 2 (LLM)
	$(T) extract --force
resolve:       ## step 2b (entity resolution)
	$(T) resolve
etl:           ## step 3a (Spark)
	docker compose --profile etl run --rm spark-etl
load:          ## step 3b (Neo4j)
	$(T) load --reset
pipeline: wait crawl extract resolve etl load   ## run steps 1-3 end to end
eval:          ## step 6 benchmark -> reports/benchmark.md
	$(T) eval
analytics:     ## step 4
	$(T) analytics
ask:           ## make ask Q="Who founded Atlas Dynamics?"
	$(T) ask "$(Q)"
llm-local:     ## start Ollama and pull the model
	docker compose --profile llm-local up -d ollama
	docker compose exec ollama ollama pull $$(grep OLLAMA_MODEL .env | cut -d= -f2)
test:          ## unit/integration tests (no Docker needed)
	python -m pytest -q tests
down:
	docker compose down
clean:         ## stop and DELETE all volumes (HDFS + Neo4j data)
	docker compose --profile etl --profile tools --profile llm-local down -v
