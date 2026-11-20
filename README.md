# Chat Support System

Django support portal with customer tickets and messaging, staff queues, and
Gemini-powered ticket classification and FAQ responses.

## Setup

On Windows, from this directory run:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Set `DJANGO_DEBUG=true` in your local `.env` so Django's development server can
serve the admin and app static assets. Never enable it in production.

Set `DJANGO_SECRET_KEY` and, if you want the AI features, `GEMINI_API_KEY` in
your local `.env`. Generate a Django key with:

```powershell
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

The default model is `gemini-3.8-flash`; override it with `GEMINI_MODEL` if
needed. The configured model must support Gemini's Interactions API. Without a
key, tickets can still be submitted at Medium priority, but the FAQ assistant
and AI classification will be unavailable. Gemini interactions are sent with
storage disabled. The local `.env` is ignored by Git; do not commit it. Create
staff users in Django admin, then add a `StaffProfile` and select their
department.
Clients sign in at `/accounts/login/`; staff sign in at `/staff/login/`.
Staff accounts must have a `StaffProfile`, and cannot sign in through the client
login. Likewise, client accounts cannot sign in through the staff login.

The portal is available at `/`; staff use `/staff/queue/` and `/staff/tickets/`.

## Push to Git

The repository ignores `.env`, the local SQLite database, `.venv`, and generated
static files. From the project root:

```powershell
git init
git add .
git status --short
git check-ignore -v .env db.sqlite3 .venv staticfiles
```

Confirm `.env` and `db.sqlite3` do not appear in `git status`. Then create a
commit and connect the remote repository you created on GitHub:

```powershell
git commit -m "Prepare support system for deployment"
git branch -M main
git remote add origin https://github.com/<your-username>/<your-repository>.git
git push -u origin main
```

## Deploy to Render

The project includes a Render Blueprint in `render.yaml`. It uses PostgreSQL
for deployed data, WhiteNoise for static assets, and Gunicorn to serve Django.
The local `db.sqlite3` is not deployed; migrations create the schema in the
Render database. The Blueprint redirects HTTP to HTTPS and enables HSTS for
one year on the app hostname; HSTS subdomains and preload remain disabled.

1. Push the project to GitHub using the steps above.
2. In Render, create a PostgreSQL database and copy its **internal database
   URL**. Choose a plan appropriate for your data and uptime needs.
3. In the Render Dashboard, create a **Blueprint Instance** and select your
   repository. When prompted, set `DATABASE_URL` to the internal URL and
   `GEMINI_API_KEY` to your Gemini key. Also set
   `DJANGO_SUPERUSER_USERNAME`, `DJANGO_SUPERUSER_EMAIL`, and
   `DJANGO_SUPERUSER_PASSWORD` to bootstrap the first admin. Render generates
   `DJANGO_SECRET_KEY`.
4. Deploy. The build script installs dependencies, collects static files,
   applies migrations, and creates the admin account if it does not already
   exist.
5. Sign in to `/admin/` with those credentials. Create staff accounts and
   their `StaffProfile` records there. After the first successful build, remove
   the three `DJANGO_SUPERUSER_*` variables from the Render environment; the
   created account remains in PostgreSQL.

If the service has already been deployed, add the three `DJANGO_SUPERUSER_*`
variables in its Render **Environment** settings and trigger a new deploy. No
Render Shell is needed.

For a custom domain, add its hostname to `DJANGO_ALLOWED_HOSTS` and its HTTPS
origin to `DJANGO_CSRF_TRUSTED_ORIGINS` in the Render service environment.
