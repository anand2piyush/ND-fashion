# Shree NavDurga Vastralaya

A responsive saree and Indian clothing storefront built with FastAPI, Jinja,
SQLite, HTML, CSS and JavaScript.

## Included

- Homepage with new arrivals and category browsing
- Searchable product catalogue
- Product pages with 1-6 images, discounts, stock and reviews
- Customer registration and login using unique email/mobile numbers
- Customer profile with up to three saved addresses
- Shopping bag, buy-now flow and order requests
- Customer order history and shareable delivery tracking links
- Owner dashboard for orders, products, stock, discounts and review visibility
- Product image uploads in JPG, PNG and WebP formats
- Up to two public Instagram post/reel embeds per product
- Seed catalogue for local development

Online payment is intentionally not included yet. Placed orders are marked for
shop-owner phone confirmation and payment verification.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

The SQLite database is created automatically at `data/navdurga.db`.

## Owner login

The development credentials are stored in `owner_config.py`:

```text
Username: owner
Password: change-me-now
```

Change both values before deployment. The owner portal is available at
`/owner/login`.

## Production checklist

Set a long random `NDF_SECRET_KEY` environment variable, change the owner
credentials, serve the application through HTTPS, add CSRF protection, move
uploaded images to managed object storage, and use PostgreSQL for a production
deployment.

## Mobile apps later

FastAPI was selected so JSON API routes can be added alongside the existing
website and consumed by future Android and iOS applications without replacing
the core database and business rules.
