# Shepmule Digital Books V4

Cloud-ready Flask application for Shepmule Investments Limited.

## What changed from V3

- Replaced the local SQLite-only data layer with **PostgreSQL support via SQLAlchemy**.
- Added **login authentication** with securely hashed passwords.
- Added environment-variable configuration for secrets and database credentials.
- Added CSRF protection for form submissions.
- Added `/health` for cloud health checks.
- Added Gunicorn production server configuration.
- Added Render Blueprint configuration.
- Kept SQLite as a local fallback so the project remains easy to run in VS Code.
- Kept the V3 document/PDF layouts and Shepmule branding assets.
- Recent documents can be regenerated as PDFs after they have been saved.

## Document types

- Invoice
- Quotation
- Receipt
- Delivery Note
- Cash Sale

Each generated PDF is one A4 page.

## Local development

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5000`.

When no `DATABASE_URL` is present, the app uses a local `shepmule.db` SQLite database. The default local login is:

```text
Username: admin
Password: ChangeMe123!
```

Change the password by setting `ADMIN_PASSWORD` before starting the app.

## PostgreSQL / production environment

Set:

```text
SECRET_KEY=<long-random-secret>
DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/DATABASE
ADMIN_USERNAME=<admin username>
ADMIN_PASSWORD=<strong admin password>
```

The application converts standard `postgresql://` and legacy `postgres://` URLs to the psycopg SQLAlchemy driver automatically.

## Production command

```bash
gunicorn --bind 0.0.0.0:$PORT app:app
```

## Render

`render.yaml` contains the web-service configuration. Create/configure a PostgreSQL database with your chosen provider, then set `DATABASE_URL` in the Render service environment variables. Also set `ADMIN_PASSWORD` and keep `SECRET_KEY` private.

## Security

Do not commit `.env`, passwords, API keys, or database credentials to GitHub. The `.gitignore` already excludes `.env` and local SQLite files.

For a larger production system, add Flask-Migrate/Alembic migrations, role-based permissions, audit logs, backups, password reset/change, rate limiting, and a separate customer/product data model.
