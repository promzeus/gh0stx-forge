import sys, yaml
d = yaml.safe_load(sys.stdin)
if d:
    m = d.get('metadata', {})
    for k in ['managedFields','resourceVersion','uid','creationTimestamp','generation','ownerReferences','selfLink']:
        m.pop(k, None)
    a = m.get('annotations', {}) or {}
    for k in list(a):
        if 'last-applied' in k or 'kubectl.kubernetes' in k or k.endswith('nodepool-hash') or k.endswith('nodepool-hash-version') or 'nodeclass-hash' in k:
            a.pop(k, None)
    if not a: m.pop('annotations', None)
    d.pop('status', None)
    sys.stdout.write(yaml.safe_dump(d, sort_keys=False))
