FROM python:3.12-slim

WORKDIR /opt/zeroday

# Install dependencies
COPY server/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Create non-root user
RUN addgroup --system app && adduser --system --ingroup app app

# Copy everything and set ownership
COPY --chown=app:app . .

USER app

EXPOSE 8001

CMD ["python3", "server/app.py"]
