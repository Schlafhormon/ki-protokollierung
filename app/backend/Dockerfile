# Backend Dockerfile for CPU-only deployment with pre-bundled ML models
# For GPU support, use Dockerfile.gpu
#
# Build with: docker build --build-arg HF_TOKEN=your_token -t backend:cpu .

FROM python:3.10-slim

WORKDIR /app

# Install system dependencies (ffmpeg required by whisperx, curl for health checks)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install uv for package management
RUN pip install uv

# Copy project files
COPY pyproject.toml ./
COPY *.py ./

# Create uploads directory
RUN mkdir -p uploads

# Install dependencies
RUN uv pip install --system -e .

# Build argument for HuggingFace token (used only during build to download models)
ARG HF_TOKEN
# Make ARG available as environment variable for Python to read
ENV HF_TOKEN=${HF_TOKEN}

# Set cache directories inside the container
ENV HF_HOME=/app/.cache/huggingface
ENV TORCH_HOME=/app/.cache/torch

# Download all ML models at build time
# This eliminates the need for HF_TOKEN at runtime
# Reuses the load_models() function from transcribe.py
RUN python -c "from transcribe import load_models; load_models()"

# Clear the HF_TOKEN from environment (security: don't leak token in final image)
ENV HF_TOKEN=

# Set flag indicating models are pre-cached (runtime won't require HF_TOKEN)
ENV MODELS_PRECACHED=1

# Expose port
EXPOSE 8010

# Health check with reduced start_period since models are pre-loaded
HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=120s \
    CMD curl -f http://localhost:8010/health || exit 1

# Run the application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8010"]
