# Use RunPod base image (configurable via build arg)
ARG BASE_IMAGE=runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404
FROM ${BASE_IMAGE}

# Environment variables
ENV PYTHONUNBUFFERED=1 
ENV WHISPER_LANGUAGE=en
ENV WHISPER_CACHE_DIR=/workspace/models
ENV WHISPER_MODEL_SIZE=large-v2
ENV ENABLE_LOCAL_WHISPER=True
ENV SKIP_CORRECTION=False

# Supported modes: pod, serverless
ARG MODE_TO_RUN=pod
ENV MODE_TO_RUN=$MODE_TO_RUN

# Set up the working directory
ARG WORKSPACE_DIR=/workspace
ENV WORKSPACE_DIR=${WORKSPACE_DIR}
WORKDIR $WORKSPACE_DIR

# Install dependencies in a single RUN command to reduce layers and clean up in the same layer to reduce image size
RUN apt-get update --yes --quiet && \
    DEBIAN_FRONTEND=noninteractive apt-get install --yes --quiet --no-install-recommends \
    software-properties-common \
    gpg-agent \
    build-essential \
    apt-utils \
    ca-certificates \
    curl && \
    add-apt-repository --yes ppa:deadsnakes/ppa && \
    apt-get update --yes --quiet && \
    DEBIAN_FRONTEND=noninteractive apt-get install --yes --quiet --no-install-recommends

# Create and activate a Python virtual environment
RUN python3 -m venv /app/venv
ENV PATH="/app/venv/bin:$PATH"

# Install Python packages
RUN pip install -I --no-cache-dir \
    asyncio \
    requests \
    cryptography \
    karaoke-gen[local-whisper] \
    runpod  

# Install requirements.txt
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip uninstall -y yt-dlp && \
    python3 -m spacy download en_core_web_sm && \
    pip install -U --pre "yt-dlp[default]" && \
    pip install -U deno && \
    pip uninstall -y onnxruntime && \ 
    pip install -I --no-cache-dir onnxruntime-gpu 
# Delete's the default start.sh file from Runpod (so we can replace it with our own below)
RUN rm ../start.sh

# Copy all of our files into the container
COPY handler.py $WORKSPACE_DIR/handler.py
COPY start.sh $WORKSPACE_DIR/start.sh
COPY pull_changes.py $WORKSPACE_DIR/pull_changes.py
RUN python3 $WORKSPACE_DIR/pull_changes.py
RUN cp -R $WORKSPACE_DIR/karaoke_gen_changes/* /usr/local/lib/python3.12/dist-packages/karaoke_gen/
# Make sure start.sh is executable
RUN chmod +x start.sh

# Make sure that the start.sh is in the path
RUN ls -la $WORKSPACE_DIR/start.sh

# depot build -t justinrunpod/pod-server-base:1.0 . --push --platform linux/amd64
CMD $WORKSPACE_DIR/start.sh
