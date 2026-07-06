FROM python:3.11-slim

# Set working directory & environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies (cron for scheduling)
RUN apt-get update && apt-get install -y --no-install-recommends \
    cron \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt /app/
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . /app/

# Setup the cron configuration file (runs daily at 3:00 AM UTC)
# Note: Cron requires an empty line at the end of the file to execute
RUN echo "0 3 * * * cd /app && python main.py >> /var/log/cron.log 2>&1" > /etc/cron.d/winback-cron && \
    chmod 0644 /etc/cron.d/winback-cron && \
    crontab /etc/cron.d/winback-cron

# Grant execution rights to the entrypoint script
RUN chmod +x /app/entrypoint.sh

# Expose log output
RUN touch /var/log/cron.log

ENTRYPOINT ["/app/entrypoint.sh"]
