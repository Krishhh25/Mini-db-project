# Deploying MiniDB "live"

One thing to know up front: `minidb.server` speaks raw TCP, not HTTP.
That means:
- A browser can't just "open" it like a website — whoever connects needs
  `client.py`, `nc`/`telnet`, or their own socket code.
- There's now optional token auth (`MINIDB_AUTH_TOKEN`) — **use it** for
  anything reachable from the public internet. Without it, anyone who
  finds the port can read, write, and delete everything.
- Traffic is plaintext (no TLS). Fine for a portfolio demo with fake
  data; not fine for anything real.

Pick the option that matches what you actually want:

| You want... | Use |
|---|---|
| A live link to show in an interview *right now*, temporarily | Option A: ngrok tunnel |
| Something that stays up long-term, full control | Option B: a small VPS + systemd |
| Something that stays up long-term, no server to babysit | Option C: Fly.io (Docker) |

---

## Option A — Quick temporary demo (ngrok)

Good for: showing it live on a call, no deployment at all.

1. Run the server locally with a token set:
   ```bash
   export MINIDB_AUTH_TOKEN=demo-secret-123
   python -m minidb.server --host 0.0.0.0 --port 9999 --file demo.db
   ```
2. In another terminal, install [ngrok](https://ngrok.com/download) and expose the port:
   ```bash
   ngrok tcp 9999
   ```
3. ngrok prints a public address like `tcp://0.tcp.ngrok.io:12345`. Whoever
   you're showing this to connects with:
   ```bash
   python client.py --host 0.tcp.ngrok.io --port 12345 --token demo-secret-123
   ```
4. Close the ngrok tunnel when you're done — it's temporary by design
   (free tier addresses also change every time you restart it).

---

## Option B — A small VPS (DigitalOcean, Lightsail, a home server, etc.)

Good for: something that's actually "live" long-term, and you want to
understand every layer of the deploy.

1. **Spin up a small Linux VM** (any cheap Ubuntu droplet/instance works —
   this project needs almost no resources).

2. **SSH in and install Python** (usually already there on Ubuntu):
   ```bash
   sudo apt update && sudo apt install -y python3
   ```

3. **Copy the project to the server.** From your own machine:
   ```bash
   scp -r minidb_project your_user@your_server_ip:~/minidb_project
   ```

4. **Set a real auth token and create a systemd service** so it survives
   reboots and restarts automatically if it crashes. On the server:
   ```bash
   sudo tee /etc/systemd/system/minidb.service > /dev/null << 'EOF'
   [Unit]
   Description=MiniDB server
   After=network.target

   [Service]
   Type=simple
   User=your_user
   WorkingDirectory=/home/your_user/minidb_project
   Environment=MINIDB_AUTH_TOKEN=change-this-to-something-random
   ExecStart=/usr/bin/python3 -m minidb.server --host 0.0.0.0 --port 9999 --file /home/your_user/minidb_project/data.db
   Restart=on-failure

   [Install]
   WantedBy=multi-user.target
   EOF

   sudo systemctl daemon-reload
   sudo systemctl enable --now minidb
   sudo systemctl status minidb
   ```

5. **Open the port in the firewall** (only step that varies by
   provider — this is the generic `ufw` version):
   ```bash
   sudo ufw allow 9999/tcp
   ```
   Most cloud providers also have a separate network firewall/security
   group in their dashboard — you'll need to open port 9999 there too.

6. **Connect from anywhere:**
   ```bash
   python client.py --host your_server_ip --port 9999 --token change-this-to-something-random
   ```

7. **Useful commands going forward:**
   ```bash
   sudo systemctl restart minidb   # after pulling new code
   sudo journalctl -u minidb -f    # tail the server's logs
   ```

---

## Option C — Fly.io (Docker, free tier, no server to manage)

Good for: "live" long-term without SSHing into anything yourself. The
project already has a `Dockerfile` for this.

1. Install the [Fly CLI](https://fly.io/docs/flyctl/install/) and sign up
   (`flyctl auth signup`).

2. From the project root:
   ```bash
   fly launch
   ```
   This detects the `Dockerfile` and walks you through creating an app.
   When it asks about a `fly.toml`, let it generate one — then edit the
   `[[services]]` block it created so the service is TCP (not HTTP) on
   port 9999:
   ```toml
   [[services]]
     internal_port = 9999
     protocol = "tcp"

     [[services.ports]]
       port = 9999
   ```

3. **Add a persistent volume** so the database file survives redeploys:
   ```bash
   fly volumes create minidb_data --size 1
   ```
   and mount it in `fly.toml`:
   ```toml
   [mounts]
     source = "minidb_data"
     destination = "/data"
   ```

4. **Set the auth token as a secret** (never commit it to the repo):
   ```bash
   fly secrets set MINIDB_AUTH_TOKEN=change-this-to-something-random
   ```

5. **Deploy:**
   ```bash
   fly deploy
   ```

6. **Connect from anywhere:**
   ```bash
   python client.py --host your-app-name.fly.dev --port 9999 --token change-this-to-something-random
   ```

---

## Option D — The HTTP demo (recommended if you want a clickable link)

Options A–C all deploy the raw TCP server, which needs `client.py` to
talk to. If what you actually want is **a link a recruiter can open in a
browser**, deploy `web/app.py` instead — it's a small Flask wrapper
around the same `MiniDB` engine, with a terminal-style demo page at `/`.

It's plain HTTP, so it works on almost any free web-app host. Using
[Render](https://render.com) as an example (Railway/Fly.io's HTTP mode
work the same way):

1. Push the project to a GitHub repo.
2. On Render: **New → Web Service**, connect the repo.
3. Set:
   - **Root directory**: leave as the repo root
   - **Dockerfile path**: `Dockerfile.web` (Render auto-detects it, or
     pick "Docker" as the environment)
   - **Add a disk**: mount a persistent disk at `/data` so the database
     file survives redeploys (Render calls this a "Disk" in the service
     settings)
4. **Environment variables**:
   - `MINIDB_AUTH_TOKEN` — set this if you don't want strangers writing
     to your demo's data. Note: the demo page's example buttons don't
     send a token, so if you set one, either share the token separately
     with whoever you're sending the link to, or leave auth off for a
     throwaway public demo with fake data (recommended for a resume
     link — nobody should need a password to click around).
5. Deploy. Render gives you a URL like `https://your-app.onrender.com` —
   that's the link you put on your resume/portfolio.

To run it locally first and make sure it works before deploying:
```bash
cd minidb_project
pip install -r web/requirements.txt
python web/app.py
# open http://localhost:8080
```



- [ ] `MINIDB_AUTH_TOKEN` is set to something random, not left unset
- [ ] The token isn't committed to git or visible in a public repo/README
- [ ] You're okay with the data being plaintext-over-the-wire (no TLS) —
      don't put anything sensitive in it
- [ ] You have a way to stop it later (kill the ngrok tunnel / `systemctl
      stop minidb` / `fly scale count 0` / pause the Render service) so
      it's not running forever unattended
- [ ] If it's a public resume link (Option D), you're fine with it being
      writable by anyone who visits — treat it as a sandbox, not real data

If you want people to be able to just click a link in a browser instead
of running `client.py`, that's exactly what Option D above is for.
