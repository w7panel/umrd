# Release checklist

## 1. Version bump

Update version in `pyproject.toml`:
```toml
[project]
version = "1.8.1"  # increment
```

## 2. Build

```bash
./scripts/build.sh
```

## 3. Test the wheel (optional)

```bash
# Create venv and test
python3 -m venv test-env
source test-env/bin/activate
pip install dist/umrd-*.whl
umrd --help
deactivate
rm -rf test-env
```

## 4. Publish to PyPI (requires account)

```bash
# Install build tools
pip install build twine

# Upload to PyPI
twine upload dist/*
```

## 5. Alternative: GitHub Releases

```bash
# Create git tag
git tag v1.8.1
git push origin v1.8.1

# Create release on GitHub with the dist/ files
```

---

## User Installation Options

### Option A: From PyPI (recommended when published)

```bash
pip install umrd
```

### Option B: From wheel file

```bash
# Download the wheel from GitHub releases or PyPI
pip install umrd-1.8.0.eks-12-py3-none-any.whl
```

### Option C: From source

```bash
git clone https://github.com/w7panel/umrd.git
cd umrd
pip install .
```

### Option D: Systemd (recommended)

```bash
# Clone and install
git clone https://github.com/w7panel/umrd.git
cd umrd
./scripts/install.sh
sudo systemctl enable --now umrd
```

### Option E: Full manual setup

```bash
sudo pip install umrd
sudo cp service/umrd.service /etc/systemd/system/
sudo systemctl daemon-reload
echo "/sys/fs/cgroup/memory/kubepods" | sudo tee /run/umrd/allowlist.cfg
sudo systemctl enable --now umrd
```

---

## Kubernetes DaemonSet Deployment

```yaml
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: umrd
  namespace: kube-system
spec:
  selector:
    matchLabels:
      app: umrd
  template:
    metadata:
      labels:
        app: umrd
    spec:
      hostPID: true
      containers:
      - name: umrd
        image: python:3.11-slim
        command: ["python3", "-m", "umrd"]
        args:
        - "--mode=2"
        - "--allowlist=/config/allowlist.cfg"
        - "--open-zram"
        securityContext:
          privileged: true
        volumeMounts:
        - name: cgroup
          mountPath: /sys/fs/cgroup
          readOnly: false
        - name: config
          mountPath: /config
        - name: run
          mountPath: /run/umrd
      volumes:
      - name: cgroup
        hostPath:
          path: /sys/fs/cgroup
      - name: config
        configMap:
          name: umrd-config
      - name: run
        hostPath:
          path: /run/umrd
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: umrd-config
  namespace: kube-system
data:
  allowlist.cfg: |
    /sys/fs/cgroup/memory/kubepods
```
