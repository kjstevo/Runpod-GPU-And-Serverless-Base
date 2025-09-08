# Use RunPod base image (configurable via build arg)
ARG BASE_IMAGE=runpod/pytorch:2.0.1-py3.10-cuda11.8.0-devel-ubuntu22.04
FROM ${BASE_IMAGE}

# Environment variables
ENV PYTHONUNBUFFERED=1 \
    MODE_TO_RUN=${MODE_TO_RUN:-pod} \
    WORKSPACE_DIR=/app

# Set working directory
WORKDIR $WORKSPACE_DIR

# Install RunPod SDK (minimal dependencies)
RUN pip install --no-cache-dir runpod

# Copy essential files
COPY handler.py start.sh ./

# Make start script executable
RUN chmod +x start.sh

# Run the start script
CMD ["./start.sh"]