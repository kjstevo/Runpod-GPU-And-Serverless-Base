# syntax=docker/dockerfile:1
ARG BASE_IMAGE=runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404
FROM ${BASE_IMAGE}

ENV PYTHONUNBUFFERED=1
ENV WHISPER_LANGUAGE=en
ENV WHISPER_MODEL_SIZE=large-v3-turbo
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

# RUN python3 -m venv /app/venv
#ENV PATH="/app/venv/bin:$PATH"

# Stable heavy deps — only re-runs when these packages or versions change
# Clone once, install both targets from local path
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -I  --no-cache-dir  cryptography && \
    pip uninstall -y yt-dlp && \
    pip install -U  --no-cache-dir  --pre "yt-dlp[default]" && \
    pip install -U  --no-cache-dir  deno && \
    pip install  --no-cache-dir  torch==2.8.0+cu128 torchaudio==2.8.0+cu128 --index-url https://download.pytorch.org/whl/cu128 && \
    pip install  --no-cache-dir  torchvision==0.23.0 && \
    pip uninstall -y onnxruntime && \
    pip install  --no-cache-dir  onnxruntime-gpu 

RUN git clone --depth=1 --single-branch https://github.com/kjstevo/karaoke-gen.git /tmp/karaoke-gen && \
    pip install --no-cache-dir /tmp/karaoke-gen/stubs/onnxruntime-stub && \
    pip install --no-cache-dir /tmp/karaoke-gen && \
    python3 -m spacy download en_core_web_sm && \
    pip install  --no-cache-dir  whisper-timestamped && \
    rm -rf /tmp/karaoke-gen
# User deps — re-runs whenever requirements.txt changes
COPY requirements.txt ./requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir -r requirements.txt

RUN rm ../start.sh

COPY handler.py $WORKSPACE_DIR/handler.py
COPY start.sh $WORKSPACE_DIR/start.sh
COPY bootstrap.sh $WORKSPACE_DIR/bootstrap.sh
COPY style.json $WORKSPACE_DIR/style.json
RUN chmod +x $WORKSPACE_DIR/bootstrap.sh $WORKSPACE_DIR/start.sh
RUN pip cache purge
CMD $WORKSPACE_DIR/bootstrap.sh
