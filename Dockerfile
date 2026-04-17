FROM python:3.13-slim

WORKDIR /app

# Install dependencies first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY server.py server_http.py wiki_client.py config.py ./

EXPOSE 8000

CMD ["python3", "server_http.py"]
