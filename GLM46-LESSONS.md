# GLM46-LESSONS — грабли пайплайна GLM-4.6 и рабочие скрипты

Ошибки, пойманные при сборке аблитерации GLM-4.6 (2026-09-27), с причиной и фиксом, плюс рабочие
манифесты целиком. Цель — повторный прогон не наступает на то же. Дополняет [PIPELINE-GLM46.md](PIPELINE-GLM46.md).

## Ошибки

Премиса «прун не нужен, NVFP4 влезает» — ГЛАВНАЯ ошибка. NVFP4 это 4 бита + FP8-масштаб на
группу 16 = ~0.56 Б/параметр (4-битный флор, ниже не бывает). 353B → ~198ГБ, в 122ГБ GB10 не
лезет. «~106ГБ» из первой сессии — арифметическая ошибка (даже правило bf16×0.30 даёт 665×0.30≈200).
Фикс: REAP-прун 353B→~151B (64/160 эксп/блок) → NVFP4 ~85ГБ. Бюджет GB10 (122ГБ usable) под
контекст: KV = 2·kv_heads(8)·head_dim(128)·layers(92) = 188416 элем/токен; fp8 KV = 22.6ГБ на 120k.
Итог 120k: веса 85 + KV 22.6 + оверхед 6 = ~114ГБ, запас ~8ГБ. fp4 KV в vLLM стабильного нет,
качество на длинном контексте просядет — берём fp8.


`FileNotFoundError: /model/glm46/mtp.safetensors` на `from_pretrained`. Синк шёл с
`--exclude "mtp.safetensors"`. `model.safetensors.index.json` ссылается на mtp-шард, загрузчик
открывает каждый файл из индекса и падает на отсутствующем. Фикс — синкать полный набор из
`glm46-base/`, ничего не исключать. mtp-веса HF игнорирует (не в декодер-стеке), `num_layers=92`.

Pod EVICTED по ephemeral-storage на save (диск 1300Gi и 1500Gi). `from_pretrained` mmap-ит входные
safetensors; аблитерация трогает только часть тензоров (down_proj, o_proj, shared, embed), остальные
читаются из mmap при записи, поэтому вход ~709G держится на диске до конца процесса. `rm` мид-прогоне
отвязывает имена, но блоки не освобождаются (в `/proc/PID/fd` пусто, блоки в `/proc/PID/maps` как
`(deleted)`; `df` 692G при `du` 24M). Пик = вход 709G + выход 705G = ~1414G. kubelet держит резерв
~10% и выселяет, когда свободного меньше (на 1500Gi свободно упало до ~86GiB при пороге ~150GiB).
Фикс — корень ноды 2000Gi, трюк с удалением убран, `df` до/после в лог.

Спот-отзыв ноды мид-прогоне: `Pod was terminated in response to imminent node shutdown`, exit 137,
r7i.48xlarge в eu-north-1a отозвана AWS через ~4 мин. Одношаговая джоба на ~2ч на споте ловит отзыв.
Фикс — `backoffLimit: 3`, ретрай сам поднимает новый pod и ноду (каждый ретрай пере-синкает и
пере-мерит, чекпоинта нет). При повторных отзывах (2+ за прогон, как было в eu-north-1a 2026-09-27)
пул волатилен — переключить nodepool `capacity-type` на `on-demand` для этого ~1.5ч прогона
(r7i.48xlarge on-demand ~$12/ч, гарантия без отзыва); дешёвый спот при серии отзывов дороже по факту.

Диагностика через `du` обманывала: `du /model` = 24M, но `df` = 692G. Мерить занятость под mmap
только через `df` и `/proc/PID/maps`, не `du` и не `/proc/PID/fd`.

Квант OOM на линеаризации MoE. llm-compressor линеаризует эксперты (2D per-expert -> fused) перед
калибровкой; post-load fallback делает 2D->3D->2D и держит обе формы, спайк ~+670G поверх 705G модели
вышиб p4d 1152ГБ (под умер на 68/89, `lastState` пустой = memory-eviction). Фикс без большой ноды —
грузить в контексте `llmcompressor.modeling.moe.linearize.load_quantizable_moe(AutoModelForCausalLM)`:
для `glm4_moe` есть registered mapping (`has_linearize_load_mappings('glm4_moe')==True`), веса грузятся
сразу в линеаризованной 2D-форме, без удвоения, влезает в p4d. llmcompressor пиновать (==0.14.0),
чтобы `-U` не подтянул другой API. Правка в `nvfp4_quant.py` (обёртка вокруг from_pretrained).

GLM4Moe эксперты = фьюзный `Glm4MoeExperts`, НЕ ModuleList (transformers 5.17). Packed 3D:
`gate_up_proj [n_exp,2*moe_inter,H]`, `down_proj [n_exp,H,I]`; роутер `Glm4MoeTopkRouter.weight [n_exp,H]`
+ буфер `e_score_correction_bias [n_exp]`; forward крутит `for expert_idx` по `self.num_experts`.
Прун режет packed-параметры по dim0 `[keep]` (как Qwen), слайсит gate.weight/bias И ставит
`experts.num_experts=n_keep` (иначе one_hot в forward падает). Первый порт под ModuleList упал
`no len()`; структуру ловить meta-инстансом `Glm4MoeMoE(cfg)` за 3 мин, не гонять 705G-загрузку.
Абилитерация уже шла packed-веткой (project_out_packed_experts, 14240 срезов), выход корректен.

GLM4Moe эксперты = фьюзный Glm4MoeExperts, НЕ ModuleList (transformers 5.17). Packed 3D:
gate_up_proj [n_exp,2*moe_inter,H], down_proj [n_exp,H,I]; роутер Glm4MoeTopkRouter.weight [n_exp,H]
+ буфер e_score_correction_bias [n_exp]; forward крутит for expert_idx по self.num_experts.
Прун режет packed-параметры по dim0 [keep] (как Qwen), слайсит gate.weight/bias И ставит
experts.num_experts=n_keep (иначе one_hot в forward падает). Первый порт под ModuleList упал
no len(); структуру ловить meta-инстансом Glm4MoeMoE(cfg) за 3 мин, не гонять 705G-загрузку.
Абилитерация уже шла packed-веткой (project_out_packed_experts), выход корректен.

## Грабли kubectl/Monitor (zsh)

`KC="kubectl --request-timeout=15s"; $KC get ...` → `command not found`. zsh не расщепляет
раскрытие `$KC` по пробелам (SH_WORD_SPLIT off). Звать kubectl напрямую, не через переменную.

`kubectl logs -f --request-timeout=30s` обрывает follow через 30с, стрим кончается, монитор ложно
рапортует «ended». На follow не ставить `--request-timeout`; оборачивать в цикл с переподключением.

`kubectl get custom-columns=...[?(@.type=="Ready")]...` → zsh `no matches found` (глоб `[?(...)]`).
jsonpath брать в одинарные кавычки.

Терминал джобы с `backoffLimit>0` мерить по `conditions[type=Complete/Failed].status`, не по
`status.failed` — счётчик failed растёт на каждой упавшей попытке и даёт ложный «FAILED».

Кастомный node-taint глушит GPU: `nvidia-device-plugin` daemonset терпит только свои taint'ы
(`nvidia.com/gpu`), не произвольные. Нода с taint `glm-quant` не пустила device-plugin, GPU не
зарегистрировались (`capacity.nvidia.com/gpu` пусто), под встал в `Insufficient nvidia.com/gpu`.
Taint GPU-нод = `nvidia.com/gpu:NoSchedule` (его терпят и плагин, и джоба); снять с живой ноды —
`kubectl taint node <n> glm-quant:NoSchedule-`, GPU появятся за ~15с.

Осиротевший под жёг ноду: delete джобы сквозь мигающий API прошёл частично — Job исчез, а Pending-под
остался; позже Karpenter поднял p4d под него, но `Insufficient gpu` не дал стартовать, и p4d висел
вхолостую ~2.5ч (~$11). Мораль: после delete через нестабильный API сверять `get job` И `get pods`,
осиротевшие поды сносить, простаивающие GPU-ноды ловить `kubectl get nodes` по цене.

Завершённая/упавшая джоба держит ноду часами (do-not-disrupt + не выстреливший ttl). Ожидатель сам
`kubectl delete job`; пустую ноду добить `kubectl delete nodeclaim` (см. memory `karpenter-idle-after-job`).

## Правильный монитор прогона

```bash
export AWS_PROFILE=example-stage AWS_REGION=eu-north-1
while true; do
  comp=$(kubectl -n distill get job glm46-abliterate -o jsonpath='{.status.conditions[?(@.type=="Complete")].status}' 2>/dev/null || echo "")
  fail=$(kubectl -n distill get job glm46-abliterate -o jsonpath='{.status.conditions[?(@.type=="Failed")].status}' 2>/dev/null || echo "")
  [ "$comp" = "True" ] && { echo "JOB COMPLETE"; break; }
  [ "$fail" = "True" ] && { echo "JOB FAILED"; break; }
  kubectl -n distill logs -f -l app=glm46-abliterate --tail=2 --max-log-requests=1 2>/dev/null \
    | grep --line-buffered -E "disk before|num_layers|abliteration applied|output size|disk after|GLM46_ABLITERATE_DONE|node shutdown|Evicted|Traceback|OOM|ENOSPC|Error"
  sleep 15
done
```

## Ноды по стадиям

bf16 GLM-4.6 = ~705ГБ, грузится CPU-resident, размер ноды меряется по этому, не по 4-бит размеру.

| стадия | нода | диск | почему |
|---|---|---|---|
| ADJUST | r7i.48xlarge spot $1.64 (1536ГБ) | 2000Gi | вход+выход+резерв kubelet |
| QUANT | p4d.24xlarge spot $4.54 (1152ГБ, 8×A100-40) | 1000Gi | дешевле g6e.24xlarge $4.67 и влезает; g6e.12xlarge 393ГБ мало |

## Рабочие манифесты

Источник правды на диске: `pods/glm-abliterate-nodeclass.yaml`, `pods/glm46-abliterate-job.yaml`,
`pods/glm-quant-nodeclass.yaml`, `pods/glm46-nvfp4-job.yaml`; скрипты `pipeline/merge_abliterate.py`,
`pipeline/nvfp4_quant.py` (в S3 `s3://ai-monitoring-test-123456789012/glm46/`). Аблитерация ниже целиком.

### pods/glm-abliterate-nodeclass.yaml
```yaml
apiVersion: karpenter.k8s.aws/v1
kind: EC2NodeClass
metadata:
  name: glm-abliterate
spec:
  role: Karpenter-example-cluster-20250502225446311500000002
  amiSelectorTerms:
    - alias: al2023@latest
  subnetSelectorTerms:
    - tags: { karpenter.sh/discovery: example-cluster }
  securityGroupSelectorTerms:
    - tags: { karpenter.sh/discovery: example-cluster }
  blockDeviceMappings:
    - deviceName: /dev/xvda
      ebs: { volumeSize: 2000Gi, volumeType: gp3, iops: 8000, throughput: 800, deleteOnTermination: true, encrypted: true }
---
apiVersion: karpenter.sh/v1
kind: NodePool
metadata:
  name: glm-abliterate
spec:
  template:
    metadata:
      labels: { workload: glm-abliterate }
    spec:
      requirements:
        - { key: karpenter.sh/capacity-type, operator: In, values: ["spot"] }
        - { key: node.kubernetes.io/instance-type, operator: In, values: ["r7i.48xlarge"] }
      taints:
        - { key: glm-abliterate, effect: NoSchedule }
      nodeClassRef: { group: karpenter.k8s.aws, kind: EC2NodeClass, name: glm-abliterate }
      expireAfter: 8h
  limits: { cpu: 400 }
  disruption: { consolidationPolicy: WhenEmpty, consolidateAfter: 5m }
```

### pods/glm46-abliterate-job.yaml (ключевое)
```yaml
apiVersion: batch/v1
kind: Job
metadata: { name: glm46-abliterate, namespace: distill }
spec:
  backoffLimit: 3                 # авто-ретрай на спот-отзыв
  activeDeadlineSeconds: 25200
  ttlSecondsAfterFinished: 300
  template:
    metadata:
      labels: { app: glm46-abliterate }
      annotations: { karpenter.sh/do-not-disrupt: "true" }
    spec:
      restartPolicy: Never
      serviceAccountName: ai-monitoring-test
      nodeSelector: { workload: glm-abliterate }
      tolerations:
        - { key: glm-abliterate, effect: NoSchedule, operator: Exists }
        - { key: karpenter.sh/disruption, effect: NoSchedule, operator: Exists }
      containers:
        - name: abliterate
          image: vllm/vllm-openai:latest
          command: ["/bin/bash","-lc"]
          args:
            - |
              set -euo pipefail
              B=ai-monitoring-test-123456789012
              python3 -m pip install -q -U transformers accelerate safetensors huggingface_hub hf_xet sentencepiece tiktoken blobfile awscli 2>&1 | tail -1
              export HF_HOME=/model/hf TMPDIR=/tmp
              mkdir -p /model/glm46 /work && cd /work
              aws s3 cp s3://$B/glm46/merge_abliterate.py ./merge_abliterate.py
              aws s3 cp s3://$B/glm46/extra_prompts.json  ./extra_prompts.json
              aws s3 sync s3://$B/glm46-base/ /model/glm46 --no-progress 2>&1 | tail -1   # НЕ исключать mtp.safetensors
              test -f /model/glm46/config.json || { echo FATAL; exit 1; }
              echo "[job] disk before: $(df -h /model | tail -1)"
              ABL_NORM_PRESERVE=1 ABL_DO_OPROJ=1 ABL_DO_GDN_OUT=1 ABL_DO_EXPERT_DOWN=1 \
              ABL_DO_SHARED_DOWN=1 ABL_DO_DENSE_DOWN=1 ABL_DO_EMBED=1 ABL_BATCH=8 ABL_SHARD=20GB OMP_NUM_THREADS=180 \
                stdbuf -oL python3 merge_abliterate.py --skip-lora --model /model/glm46 --out /model/glm46-abl 2>&1 \
                | grep --line-buffered -vE "prompts$|Loading weights:"
              test -f /model/glm46-abl/config.json || { echo "FATAL: no output"; exit 1; }
              echo "[job] disk after: $(df -h /model | tail -1)"
              aws s3 sync /model/glm46-abl/ s3://$B/glm46-abliterated/ --no-progress 2>&1 | tail -1
              echo "[job] ===== GLM46_ABLITERATE_DONE $(date -u) ====="
          env:
            - { name: AWS_REGION, value: "eu-north-1" }
            - { name: TMPDIR, value: "/tmp" }
          resources:
            requests: { cpu: "80", memory: 1400Gi }
          volumeMounts:
            - { name: model, mountPath: /model }
            - { name: tmp, mountPath: /tmp }
      volumes:
        - name: model
          emptyDir: { sizeLimit: 1780Gi }
        - name: tmp
          emptyDir: { sizeLimit: 40Gi }
```

## Порядок запуска

```bash
export AWS_PROFILE=example-stage AWS_REGION=eu-north-1
cd /Users/flash/gb10
# скрипты в S3 (один раз, если менялись)
aws s3 cp pipeline/merge_abliterate.py s3://ai-monitoring-test-123456789012/glm46/merge_abliterate.py
aws s3 cp pipeline/nvfp4_quant.py      s3://ai-monitoring-test-123456789012/glm46/nvfp4_quant.py
# ADJUST
kubectl apply -f pods/glm-abliterate-nodeclass.yaml
kubectl apply -f pods/glm46-abliterate-job.yaml
# по GLM46_ABLITERATE_DONE:
kubectl delete job glm46-abliterate -n distill
kubectl delete nodepool glm-abliterate ; kubectl delete ec2nodeclass glm-abliterate
# QUANT
kubectl apply -f pods/glm-quant-nodeclass.yaml
kubectl apply -f pods/glm46-nvfp4-job.yaml
# по GLM46_NVFP4_DONE: те же delete job + nodepool/ec2nodeclass glm-quant
```
