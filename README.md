# Alkansya: Your digital piggy bank

> Finally, an alkansya you don't have to break.

An online banking simulator built with Flask. It upgrades the console *Simple Banking System*
(deposit / withdraw / check balance) into a responsive web app.

## Features

**Admin account**
- Create customers and issue a default PIN (or generate a random one)
- Optional opening deposit
- Unlock locked accounts and issue temporary PINs
- Dashboard stats and a full audit trail of every event

**Customer account**
- Deposit and withdraw (simulated)
- Check balance (PIN-protected, hides again after 30 seconds)
- Change PIN

**Security**
- Every transaction must be authorized with the PIN
- After each transaction: a receipt with a reference number, then automatic sign-out in 30 seconds unless the customer chooses to keep banking. The balance is hidden again, and the next transaction needs the PIN again.
- 3 wrong PINs lock the account until an admin unlocks it
- The default PIN must be changed on first sign-in, and weak PINs (1111, 1234) are rejected
- Automatic sign-out after 5 minutes of inactivity
- PINs and passwords are stored as hashes; CSRF protection on every form and API call
- Per-transaction limits: ₱100,000 deposit and ₱50,000 withdrawal
- Every sign-in, transaction and admin action is saved to the database

## Tech stack
Flask 3 · Flask-SQLAlchemy · PostgreSQL (psycopg2-binary) · Gunicorn · Jinja2 · Tailwind CSS ·
Lucide icons · Vanilla JS (Fetch API) · python-dotenv · Render

## Run locally
```bash
pip install -r requirements.txt
python app.py
```
Open http://127.0.0.1:5000 and sign in as `admin` / `admin123`. Locally the app uses SQLite.

## Deploy on Render
1. Push this folder to a GitHub repository.
2. In Render, choose **New → Blueprint** and pick the repository. Render reads `render.yaml` and creates the web service and a free PostgreSQL database.
3. Click **Apply**. The admin account is `admin` / `admin123` (set in `render.yaml`).
