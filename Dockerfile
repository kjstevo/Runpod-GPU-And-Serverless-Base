# syntax=docker/dockerfile:1
ARG BASE_IMAGE=runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404
FROM ${BASE_IMAGE}

ENV PYTHONUNBUFFERED=1
ENV WHISPER_LANGUAGE=en
ENV WHISPER_MODEL_SIZE=large-v2
ENV ENABLE_LOCAL_WHISPER=True
ENV SKIP_CORRECTION=False
ENV MAKE1VIDEO=True
ENV WHISPER_CACHE_DIR=/workspace/models


ARG MODE_TO_RUN=pod
ENV MODE_TO_RUN=$MODE_TO_RUN

ARG WORKSPACE_DIR=/app
ENV WORKSPACE_DIR=${WORKSPACE_DIR}
WORKDIR $WORKSPACE_DIR

RUN apt-get update --yes --quiet && \
    DEBIAN_FRONTEND=noninteractive apt-get install --yes --quiet --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
	xclip \
        fonts-noto
    

# RUN python3 -m venv /app/venv
#ENV PATH="/app/venv/bin:$PATH"

# Stable heavy deps — only re-runs when these packages or versions change
# Clone once, install both targets from local path
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install   --no-input  -I "karaoke-gen[local-whisper] @  git+https://github.com/kjstevo/karaoke-gen.git@worktree-Experimental-lyric-sync" && \
    pip install   --no-input "torch>=2.7,<2.9" torchaudio torchvision --index-url https://download.pytorch.org/whl/cu128 && \
    pip install   --no-input   "audio-separator[gpu]>=0.43.0,<0.44.0" && \
    pip uninstall -y nvidia-cudnn-cu13 nvidia-nccl-cu13 nvidia-cusparselt-cu13 nvidia-nvshmem-cu13 && \
    pip uninstall -y onnxruntime onnxruntime-gpu && \
    pip install --no-input "onnxruntime-gpu>=1.16.0,<1.21.0" && \
    python -m spacy download en_core_web_sm

COPY requirements.txt ./requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir -r requirements.txt

RUN rm ../start.sh

COPY handler.py $WORKSPACE_DIR/handler.py
COPY start.sh $WORKSPACE_DIR/start.sh
COPY bootstrap.sh $WORKSPACE_DIR/bootstrap.sh
COPY style.json $WORKSPACE_DIR/style.json
RUN chmod +x $WORKSPACE_DIR/bootstrap.sh $WORKSPACE_DIR/start.sh
RUN pip cache purge && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*
CMD $WORKSPACE_DIR/bootstrap.sh
