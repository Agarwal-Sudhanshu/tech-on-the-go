FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libsndfile1 git \
    && rm -rf /var/lib/apt/lists/*

# CPU-only PyTorch wheels (no GPU on this machine), then the voice-clone model.
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu chatterbox-tts

# Fonts for the video slides.
RUN apt-get update && apt-get install -y --no-install-recommends fonts-inter fontconfig \
    && rm -rf /var/lib/apt/lists/*

# The project folder is mounted at /proj at run time; scripts run from there.
WORKDIR /proj
ENTRYPOINT ["python"]
