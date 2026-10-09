# Execution-grounded training pipeline — thin CLI over the Python scripts (spec section 32).
# Runs on the box (gh0stx@192.168.8.239). Main logic stays in Python; targets are shortcuts.
#
#   make status                          serve state, served models, free RAM, dataset size
#   make validate       TASK=<id>        contract check for one task (spec section 2)
#   make repair         TASK=<id>        Student FAIL -> Teacher -> grader PASS -> DATA
#   make harvest        TASK=<id>        Student solves in sandbox, PASS trace -> DATA
#   make dataset-check  DATA=<jsonl>     confirm each row labels assistant tokens
#   make train          DATA=<jsonl>     QLoRA on the nvfp4 base -> OUT_DIR adapter
#   make evaluate       TASK=<id> MODEL=<id>   run one task against a served model, grade
#   make serve-down / serve-up           stop / start the production serve container
#   make serve-eval                      temp serve of OUT_DIR adapter as gh0stx-r2
#   make serve-restore                   drop eval container, bring production serve back
#   make micro-train                     step 3: dataset-check -> serve window -> train -> eval

SHELL      := /bin/bash
TRAINDIR   := $(HOME)/gb10/training
HARNESS    := $(TRAINDIR)/harness
PY         := python3
TRAIN_PY   := $(HOME)/.venvs/train/bin/python

TASKS      ?= $(HARNESS)/tasks/safe_subset.json
TASK       ?= py_offbyone_chunk
MODEL      ?= gh0stx-r2
DATA       ?= /tmp/repair_offbyone.jsonl
RULES      ?= $(HARNESS)/gh0stx-rules.md
# BASE_DIR must equal the served base so the trained adapter composes at eval time.
BASE_DIR   ?= $(HOME)/nvfp4_v4_releases/gh0stx_rft_v5
OUT_DIR    ?= $(TRAINDIR)/adapters/egt

SERVE_CT   := vllm-dflash
EVAL_CT    := vllm-egt-eval
IMG        := aeon-vllm:v024
API        := http://localhost:8000/v1/chat/completions

# training hyperparams (spec section 19); EPOCHS high because micro-train overfits 1 example
EPOCHS       ?= 20
LR           ?= 2e-4
R            ?= 16
ALPHA        ?= 32
MAXLEN       ?= 8192
GA           ?= 1
CHUNK        ?= 2048
SAVE_STEPS   ?= 50
STUDENT_TURNS?= 12
MAX_TURNS    ?= 20
# requant loader saves the adapter to OUT_DIR/epoch_<N>; last epoch is EPOCHS-1
LASTEP       := $(shell echo $$(( $(EPOCHS) - 1 )))
ADAPTER      ?= $(OUT_DIR)/epoch_$(LASTEP)

.PHONY: help status validate repair harvest dataset-check train failfast \
        serve-down serve-up serve-eval evaluate serve-restore micro-train

help:
	@sed -n '3,15p' $(firstword $(MAKEFILE_LIST))

status:
	@sudo -n docker ps --format ' {{.Names}}  {{.Image}}  {{.Status}}' | grep -E 'vllm|egt' || true
	@curl -s --max-time 5 http://localhost:8000/v1/models \
	  | $(PY) -c 'import sys,json;print(" served:",[m["id"] for m in json.load(sys.stdin).get("data",[])])' 2>/dev/null \
	  || echo ' served: (serve down)'
	@free -g | awk '/Mem:/{print " ram: used="$$3"G avail="$$7"G / "$$2"G total"}'
	@test -f $(DATA) && echo " data: $(DATA) ($$(wc -l < $(DATA)) lines)" || echo " data: $(DATA) (absent)"

validate:
	@$(PY) $(HARNESS)/validate_task.py $(TASKS) $(TASK)

repair:
	@test -n "$$DEEPSEEK_API_KEY" || { echo "DEEPSEEK_API_KEY not in env (source ~/.zshrc)"; exit 1; }
	STUDENT_TURNS=$(STUDENT_TURNS) $(PY) $(HARNESS)/repair_cycle.py $(TASKS) $(TASK) $(DATA)

harvest:
	MODEL=$(MODEL) API=$(API) RULES=$(RULES) MAX_TURNS=$(MAX_TURNS) \
	  $(PY) $(HARNESS)/run_task.py $(TASKS) $(TASK) $(DATA)

dataset-check:
	BASE_DIR=$(BASE_DIR) $(PY) $(HARNESS)/dataset_check.py $(DATA)

# Path-C requant loader (meta-construct + nf4 experts from cache). NOT train_qlora_nvfp4.py:
# that one uses generic from_pretrained, which materializes 262GB dense experts on this MoE
# checkpoint and OOMs/freezes the box. See LOCAL_TRAINING.md and memory egt-local-train-oom.
train:
	mkdir -p $(OUT_DIR)
	DATA=$(DATA) OUT=$(OUT_DIR) EPOCHS=$(EPOCHS) GA=$(GA) LR=$(LR) \
	  MAXLEN=$(MAXLEN) CHUNK=$(CHUNK) SAVE_STEPS=$(SAVE_STEPS) \
	  $(TRAIN_PY) $(HOME)/train_141b_requant.py

failfast:
	DATA=$(DATA) OUT=$(OUT_DIR) MAXLEN=$(MAXLEN) CHUNK=$(CHUNK) FAILFAST=1 \
	  $(TRAIN_PY) $(HOME)/train_141b_requant.py

evaluate:
	MODEL=$(MODEL) API=$(API) RULES=$(RULES) MAX_TURNS=$(MAX_TURNS) \
	  $(PY) $(HARNESS)/run_task.py $(TASKS) $(TASK) /tmp/eval_$(TASK).jsonl

serve-down:
	sudo -n docker stop $(SERVE_CT)

serve-up:
	sudo -n docker start $(SERVE_CT)

# No --rm (keep logs on crash). runai_streamer loads 95GB in ~3min vs ~18min default.
# enforce-eager skips CUDA-graph capture, which OOM-crashed the container at 0.85 util.
serve-eval:
	-sudo -n docker rm -f $(EVAL_CT) 2>/dev/null
	sudo -n docker run -d --name $(EVAL_CT) --gpus all --ipc=host -p 8000:8000 \
	  -e TORCH_CUDA_ARCH_LIST=12.1a -e VLLM_USE_FLASHINFER_SAMPLER=1 \
	  -v $(BASE_DIR):/model:ro -v $(ADAPTER):/adapter:ro \
	  $(IMG) -lc "vllm serve /model --served-model-name gh0stx \
	    --quantization compressed-tensors --gpu-memory-utilization 0.82 \
	    --load-format runai_streamer --enforce-eager \
	    --max-model-len 16384 --max-num-seqs 2 \
	    --trust-remote-code --language-model-only \
	    --enable-auto-tool-choice --tool-call-parser qwen3_coder --reasoning-parser qwen3 \
	    --mamba-cache-dtype float16 --enable-lora --max-loras 1 --max-lora-rank $(R) \
	    --lora-modules gh0stx-r2=/adapter --host 0.0.0.0 --port 8000"
	@echo "waiting for eval serve..."; for i in $$(seq 1 90); do \
	  curl -s --max-time 3 http://localhost:8000/v1/models >/dev/null 2>&1 && { echo " ready"; break; }; \
	  sleep 5; done

serve-restore:
	-sudo -n docker rm -f $(EVAL_CT) 2>/dev/null
	sudo -n docker start $(SERVE_CT)
	@echo "waiting for production serve..."; for i in $$(seq 1 90); do \
	  curl -s --max-time 3 http://localhost:8000/v1/models >/dev/null 2>&1 && { echo " ready"; break; }; \
	  sleep 5; done

micro-train:
	$(MAKE) dataset-check DATA=$(DATA)
	@set -e; trap '$(MAKE) serve-restore' EXIT; \
	  $(MAKE) serve-down; \
	  $(MAKE) train; \
	  $(MAKE) serve-eval; \
	  $(MAKE) evaluate MODEL=gh0stx-r2 TASK=$(TASK)
