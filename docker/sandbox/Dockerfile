FROM python:3.11-slim

# System deps for pygame, GUI, VNC
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb x11-utils xdotool \
    libsdl2-dev libsdl2-image-dev libsdl2-mixer-dev \
    libsdl2-ttf-dev libfreetype6-dev libportmidi-dev \
    novnc websockify x11vnc \
    && rm -rf /var/lib/apt/lists/*

# Pre-install common Python packages
RUN pip install --no-cache-dir \
    pygame numpy pandas matplotlib scipy pillow \
    flask fastapi 'uvicorn[standard]' streamlit requests \
    beautifulsoup4 rich sympy networkx scikit-learn \
    opencv-python-headless seaborn plotly dash

# Non-root user
RUN useradd -m -s /bin/bash sandbox

# Entrypoint
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER sandbox
WORKDIR /home/sandbox

ENTRYPOINT ["/entrypoint.sh"]
