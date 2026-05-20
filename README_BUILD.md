# RunPod Multi-Version Build Commands

## Build and Push Commands for Different PyTorch/CUDA Versions

### PyTorch 2.0.1 - CUDA 11.8 (Original)
```bash
docker build -t kjstevo/dual-mode-worker:latest \
  --build-arg BASE_IMAGE=runpod/pytorch:2.0.1-py3.10-cuda11.8.0-devel-ubuntu22.04 \
  . --push --platform linux/amd64
```

### PyTorch 2.2.0 - CUDA 12.1
```bash
docker build -t kjstevo/dual-mode-worker:latest \
  --build-arg BASE_IMAGE=runpod/pytorch:2.2.0-py3.10-cuda12.1.1-devel-ubuntu22.04 \
  . --push --platform linux/amd64
```

### PyTorch 2.4.0 - CUDA 12.4
```bash
docker build -t kjstevo/dual-mode-worker:latest \
  --build-arg BASE_IMAGE=runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04 \
  . --push --platform linux/amd64
```

### PyTorch 2.8.0 - CUDA 12.8 (Latest)
```bash
docker build -t kjstevo/dual-mode-worker:latest \
  --build-arg BASE_IMAGE=runpod/pytorch:2.8.0-py3.11-cuda12.8.1-cudnn-devel-ubuntu22.04 \
  . --push --platform linux/amd64
```

## Version Matrix

| PyTorch | Python | CUDA | cuDNN | Ubuntu |
|---------|--------|------|-------|--------|
| 2.0.1 | 3.10 | 11.8.0 | Yes | 22.04 |
| 2.2.0 | 3.10 | 12.1.1 | Yes | 22.04 |
| 2.4.0 | 3.11 | 12.4.1 | Yes | 22.04 |
| 2.8.0 | 3.11 | 12.8.1 | Yes | 22.04 |

## Notes

- All images are built for `linux/amd64` platform
- Images are automatically pushed to Docker Hub
- Ensure you're logged in to Docker Hub: `docker login`
- The Dockerfile uses `ARG BASE_IMAGE` to allow dynamic base image selection