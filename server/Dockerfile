FROM python:3.12-slim

WORKDIR /opt/zeroday

# Install dependencies
COPY server/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Create non-root user with pinned UID/GID so host bind-mount ownership can be set
RUN addgroup --system --gid 10001 app && adduser --system --uid 10001 --ingroup app app

# Copy everything and set ownership
COPY --chown=app:app . .

USER app

EXPOSE 8001

CMD ["python3", "server/app.py"]
