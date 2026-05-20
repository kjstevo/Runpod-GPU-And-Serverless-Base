# syntax=docker/dockerfile:1
ARG BASE_IMAGE=runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04
FROM ${BASE_IMAGE}

ENV PYTHONUNBUFFERED=1
ENV WHISPER_LANGUAGE=en
ENV WHISPER_CACHE_DIR=/workspace/models
ENV WHISPER_MODEL_SIZE=large-v2
ENV ENABLE_LOCAL_WHISPER=True
ENV SKIP_CORRECTION=False

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
        fonts-noto && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /app/venv
ENV PATH="/app/venv/bin:$PATH"

# Stable heavy deps — only re-runs when these packages or versions change
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip && \
    pip install -I cryptography && \
    pip install karaoke-gen[local-whisper] runpod requests && \
    pip uninstall -y yt-dlp && \
    python3 -m spacy download en_core_web_sm && \
    pip install -U --pre "yt-dlp[default]" && \
    pip install -U deno && \
    pip install torch==2.4.0+cu124 torchaudio==2.4.0+cu124 --index-url https://download.pytorch.org/whl/cu124 && \
    pip uninstall -y onnxruntime && \
    pip install onnxruntime-gpu

# User deps — re-runs whenever requirements.txt changes
COPY requirements.txt ./requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

RUN rm ../start.sh

COPY handler.py $WORKSPACE_DIR/handler.py
COPY start.sh $WORKSPACE_DIR/start.sh
COPY pull_changes.py $WORKSPACE_DIR/pull_changes.py
COPY bootstrap.sh $WORKSPACE_DIR/bootstrap.sh

RUN chmod +x $WORKSPACE_DIR/start.sh && \
    chmod +x $WORKSPACE_DIR/bootstrap.sh

# depot build -t justinrunpod/pod-server-base:1.0 . --push --platform linux/amd64
CMD $WORKSPACE_DIR/bootstrap.sh
