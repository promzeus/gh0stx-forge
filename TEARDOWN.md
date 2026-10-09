# TEARDOWN — снос всех ресурсов (zero-cost)

> Личный GPU-distill эксперимент на `example-cluster` (acct 123456789012, eu-north-1, профиль
> `example-provision`). Создано 2026-06-26. Удалить всё ниже, чтобы не осталось следа / текущей стоимости.
> ⚠ НЕ запускать, пока эксперимент активен — на PVC/S3 лежат единственные артефакты
> `pruned_v2` / `decensored_v2`. Сначала убедиться, что нужное скачано/больше не нужно.

## Стоимость, пока живо (чтобы решить, что гасить первым)

- PVC gp3 2500Gi @ ~$0.08/ГБ-мес ≈ **$6–8/день** простоя.
- S3 `ai-monitoring-test-123456789012` ~794 ГБ ≈ ~$14/мес.
- GPU-нода тарифицируется только пока активен под с прогоном.

## AWS (acct 123456789012, eu-north-1, `AWS_PROFILE=example-provision`)

```bash
# S3 bucket + все объекты
aws s3 rb s3://ai-monitoring-test-123456789012 --force --region eu-north-1
# IAM роль + inline-политика
aws iam delete-role-policy --role-name ai-monitoring-test-s3 --policy-name s3-bucket-access
aws iam delete-role --role-name ai-monitoring-test-s3
# EKS pod-identity association (cluster example-cluster, ns distill, sa ai-monitoring-test)
aws eks list-pod-identity-associations --cluster-name example-cluster --region eu-north-1
aws eks delete-pod-identity-association --cluster-name example-cluster --association-id <ID> --region eu-north-1
```

## Kubernetes (context `example-cluster`)

```bash
kubectl config current-context   # должно быть example-cluster ПЕРЕД удалением
# GPU nodeclaim
kubectl delete nodeclaim -l workload=gpu-distill
kubectl delete nodeclaim -l workload=cpu-prune
# NodePool + EC2NodeClass (парами по имени)
kubectl delete nodepool gpu-distill cpu-prune
kubectl delete ec2nodeclass gpu-distill cpu-prune
# namespace distill — снесёт все поды/configmaps/SA И PVC distill-data (2500Gi gp3 EBS)
kubectl delete namespace distill
```

⚠ Корректное имя PVC — **`distill-data`** (не `distill-workspace` из старого `04-pvc.yaml`).
⚠ PVC = Retain: после удаления namespace проверить, что базовый EBS-том тоже удалён
(`aws ec2 describe-volumes --filters Name=tag:kubernetes.io/created-for/pvc/name,Values=distill-data`),
иначе останется orphan-том за деньги.

## Terraform

Zone-c subnet: `example-cluster/infra/zone-c.tf` (commit 58f1369) в репо `ops/terraform-configs`
(путь `example-org/ops/terraform-configs`, ветка main). `git rm` файла + `terraform apply`
(standalone additive ресурсы — 1c subnet/RT/route/assoc — чисто уничтожаются).

## Локальный скретч (не тарифицируется)

`~/gb10/`, `~/distill/`, план `~/.claude/plans/zany-watching-duckling.md`.
