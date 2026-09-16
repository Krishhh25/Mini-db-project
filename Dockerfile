# Minimal image: MiniDB's core has zero dependencies, so this is just
# Python + the source. Multi-stage isn't needed for something this small.
FROM python:3.12-slim

WORKDIR /app
COPY minidb/ ./minidb/
COPY repl.py client.py ./

# Where the database file lives. Mount a volume here in production so
# data survives container restarts/redeploys.
VOLUME ["/data"]

EXPOSE 9999

# MINIDB_AUTH_TOKEN should be set at `docker run` / platform secret time,
# not baked into the image. See DEPLOY.md.
CMD ["python", "-m", "minidb.server", "--host", "0.0.0.0", "--port", "9999", "--file", "/data/data.db"]
