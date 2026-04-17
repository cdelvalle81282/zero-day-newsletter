FROM python:3.12-slim

WORKDIR /opt/zeroday

# Install dependencies
COPY server/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Copy everything
COPY . .

EXPOSE 8001

CMD ["python3", "server/app.py"]
