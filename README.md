# ⏱️ Interval Reminder Telegram Bot

A persistent interval reminder and task tracking Telegram bot designed to keep you focused. Sends recurring reminders and persistent **"nagging" alerts** until you acknowledge that your task is completed.

---

## 🌟 Key Features

* ⏰ **10-Minute Reminder Cycle:** Sends high-priority reminder messages every 10 minutes (or 1 minute in Fast Test Mode).
* 🚨 **Persistent 3-Minute Nagging Loop:** If you don't acknowledge the reminder by tapping **"DONE"**, the bot alerts you every 3 minutes until you tap DONE.
* 🎛️ **Interactive Inline Dashboard:** Control everything using inline buttons directly inside Telegram:
  * `[ ✅ DONE ]` — Acknowledges task and resets the next interval.
  * `[ ⏸ Pause / ▶️ Resume ]` — Pause or resume reminders at any time.
  * `[ ⚡ Fast Test Mode: ON/OFF ]` — Toggles between 10m/3m and 1m/15s test intervals.
  * `[ 🔔 Trigger Now ]` — Test trigger an alert immediately.
  * `[ 🔄 Refresh Status ]` — Update the dashboard in real-time.
* 💾 **Persistent Multi-User State:** Powered by SQLite — timers and settings automatically resume even across bot or server restarts.
* 🌐 **Built-in Cloud Healthcheck Server:** Built-in lightweight HTTP server on port 8080 (`/` and `/health`) so it can be deployed on 100% free cloud hosting (Render, Koyeb, Hugging Face, Railway) with zero maintenance.

---

## 🤖 Bot Commands

| Command | Description |
| :--- | :--- |
| `/start` | Launch interactive dashboard and start reminder loop |
| `/status` | View current timer, state, and dashboard buttons |
| `/done` | Acknowledge and complete the current reminder |
| `/pause` / `/stop` | Pause recurring reminders |
| `/resume` | Resume recurring reminders |
| `/test` | Toggle Fast Test Mode (1m base / 15s nagging) |
| `/trigger` | Manually trigger a reminder alert immediately |
| `/help` | Display instructions and command list |

---

## ⚡ 100% Free Cloud Deployment (Zero Local Setup)

### Step 1: Create your Telegram Bot Token
1. Open Telegram and search for [`@BotFather`](https://t.me/BotFather).
2. Send `/newbot`, provide a name and username for your bot.
3. Copy the HTTP API token provided by BotFather.

---

### Step 2: Deploy to Render.com (100% Free)
1. Push this repository to your GitHub account (or use your existing repository).
2. Sign up / log in to [Render.com](https://render.com) (Free, no credit card required).
3. Click **New +** > **Web Service**.
4. Connect your GitHub repository `interval-reminder-tele`.
5. Configure the service:
   * **Runtime:** `Python 3`
   * **Build Command:** `pip install -r requirements.txt`
   * **Start Command:** `python bot.py`
   * **Instance Type:** `Free`
6. Scroll down to **Environment Variables** and add:
   * `TELEGRAM_BOT_TOKEN`: `<your_bot_token_from_botfather>`
7. Click **Create Web Service**. Your bot is now live 24/7!

---

### Alternative: Deploy with Docker / Koyeb / Railway
This project includes a `Dockerfile` and `Procfile`. You can deploy directly on any container platform:
```bash
docker build -t interval-reminder-tele .
docker run -e TELEGRAM_BOT_TOKEN="your_token" -p 8080:8080 interval-reminder-tele
```

---

## 💻 Local Development Setup (Optional)

1. Clone the repository:
   ```bash
   git clone https://github.com/sohan565/interval-reminder-tele.git
   cd interval-reminder-tele
   ```

2. Create and activate a virtual environment:
   ```bash
   python -m venv venv
   # Windows:
   venv\Scripts\activate
   # Linux/macOS:
   source venv/bin/activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Create `.env` file from `.env.example`:
   ```bash
   cp .env.example .env
   ```
   Set `TELEGRAM_BOT_TOKEN` in `.env`.

5. Run the bot:
   ```bash
   python bot.py
   ```
