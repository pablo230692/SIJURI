FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . .

# Create data directory for cache
RUN mkdir -p data static

EXPOSE 3000

# Use gunicorn for production
CMD ["gunicorn", "--bind", "0.0.0.0:3000", "--workers", "2", "--timeout", "120", "app:app"]
