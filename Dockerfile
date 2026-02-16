FROM python:3.11-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y build-essential && \
    rm -rf /var/lib/apt/lists/*

# Copy project definition first (better layer caching)
COPY pyproject.toml .
COPY src ./src

# Install project as package
RUN pip install --upgrade pip && \
    pip install .

# Install Streamlit (if not inside pyproject)
RUN pip install streamlit

# Copy UI
COPY ui ./ui

# Expose Streamlit port
EXPOSE 8501

# Start Streamlit
CMD ["streamlit", "run", "ui/app.py", "--server.port=8501", "--server.address=0.0.0.0"]
