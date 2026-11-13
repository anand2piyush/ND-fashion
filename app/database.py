import sqlite3
from contextlib import contextmanager
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATABASE_PATH = DATA_DIR / "navdurga.db"


@contextmanager
def db():
    DATA_DIR.mkdir(exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def init_db():
    with db() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                mobile TEXT NOT NULL UNIQUE,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS addresses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER NOT NULL,
                label TEXT NOT NULL CHECK(label IN ('Home', 'Office', 'Other')),
                recipient_name TEXT NOT NULL,
                mobile TEXT NOT NULL,
                line1 TEXT NOT NULL,
                line2 TEXT,
                city TEXT NOT NULL,
                state TEXT NOT NULL,
                pin_code TEXT NOT NULL,
                FOREIGN KEY(customer_id) REFERENCES customers(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                slug TEXT NOT NULL UNIQUE
            );

            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                brand TEXT NOT NULL,
                short_detail TEXT NOT NULL,
                description TEXT NOT NULL,
                price REAL NOT NULL,
                discount_percent INTEGER NOT NULL DEFAULT 0,
                stock INTEGER NOT NULL DEFAULT 0,
                is_new INTEGER NOT NULL DEFAULT 1,
                is_active INTEGER NOT NULL DEFAULT 1,
                instagram_url_1 TEXT,
                instagram_url_2 TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(category_id) REFERENCES categories(id)
            );

            CREATE TABLE IF NOT EXISTS product_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                image_url TEXT NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS cart_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 1,
                UNIQUE(customer_id, product_id),
                FOREIGN KEY(customer_id) REFERENCES customers(id) ON DELETE CASCADE,
                FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_number TEXT NOT NULL UNIQUE,
                tracking_token TEXT NOT NULL UNIQUE,
                customer_id INTEGER NOT NULL,
                address_snapshot TEXT NOT NULL,
                subtotal REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'Placed',
                owner_note TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(customer_id) REFERENCES customers(id)
            );

            CREATE TABLE IF NOT EXISTS order_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                product_name TEXT NOT NULL,
                unit_price REAL NOT NULL,
                quantity INTEGER NOT NULL,
                FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE,
                FOREIGN KEY(product_id) REFERENCES products(id)
            );

            CREATE TABLE IF NOT EXISTS reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                customer_id INTEGER NOT NULL,
                rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
                comment TEXT NOT NULL,
                is_visible INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(product_id, customer_id),
                FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE,
                FOREIGN KEY(customer_id) REFERENCES customers(id) ON DELETE CASCADE
            );
            """
        )
        migrate_schema(connection)
        seed_data(connection)


def migrate_schema(connection):
    """Apply small SQLite migrations for databases created by older versions."""
    product_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(products)")
    }
    if "instagram_url_1" not in product_columns:
        connection.execute("ALTER TABLE products ADD COLUMN instagram_url_1 TEXT")
    if "instagram_url_2" not in product_columns:
        connection.execute("ALTER TABLE products ADD COLUMN instagram_url_2 TEXT")


def seed_data(connection):
    category_count = connection.execute(
        "SELECT COUNT(*) AS count FROM categories"
    ).fetchone()["count"]
    if category_count:
        return

    categories = [
        ("Banarasi Sarees", "banarasi"),
        ("Silk Sarees", "silk"),
        ("Cotton Sarees", "cotton"),
        ("Designer Sarees", "designer"),
        ("Kurtis & Suits", "kurtis-suits"),
    ]
    connection.executemany(
        "INSERT INTO categories (name, slug) VALUES (?, ?)", categories
    )
    category_ids = {
        row["slug"]: row["id"]
        for row in connection.execute("SELECT id, slug FROM categories")
    }

    products = [
        (
            category_ids["banarasi"],
            "Rani Pink Banarasi Silk Saree",
            "Shree NavDurga",
            "Traditional zari weave with a rich festive finish",
            "A graceful Banarasi silk saree with detailed zari work, woven border and matching blouse piece. Ideal for weddings and festive occasions.",
            6499,
            20,
            8,
            1,
        ),
        (
            category_ids["silk"],
            "Emerald Kanjivaram Silk Saree",
            "Shree NavDurga",
            "Temple border and classic gold-toned pallu",
            "A statement Kanjivaram-inspired silk saree in emerald green with a traditional contrast border and blouse piece.",
            8299,
            15,
            5,
            1,
        ),
        (
            category_ids["cotton"],
            "Indigo Handloom Cotton Saree",
            "NavDurga Everyday",
            "Lightweight cotton for elegant everyday wear",
            "Breathable handloom-style cotton with a soft drape and subtle woven detailing. Includes an unstitched blouse piece.",
            2199,
            10,
            14,
            1,
        ),
        (
            category_ids["designer"],
            "Wine Sequin Evening Saree",
            "NavDurga Studio",
            "Contemporary shimmer with a fluid party-ready drape",
            "A modern designer saree with delicate sequin accents and a coordinated blouse fabric, made for receptions and evening celebrations.",
            4999,
            25,
            6,
            1,
        ),
        (
            category_ids["kurtis-suits"],
            "Mustard Embroidered Kurta Set",
            "NavDurga Studio",
            "Three-piece festive set with delicate embroidery",
            "A comfortable kurta, trousers and dupatta set with tonal embroidery and a flattering straight silhouette.",
            2899,
            12,
            10,
            0,
        ),
    ]
    connection.executemany(
        """
        INSERT INTO products (
            category_id, name, brand, short_detail, description, price,
            discount_percent, stock, is_new
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        products,
    )

    palettes = [
        ("#8f1d43", "#e9a7bc"),
        ("#075b4c", "#c6a15b"),
        ("#173f70", "#a9c6d9"),
        ("#641b42", "#d9a2bc"),
        ("#c98618", "#f0d18a"),
    ]
    product_ids = [
        row["id"] for row in connection.execute("SELECT id FROM products ORDER BY id")
    ]
    for product_id, (color1, color2) in zip(product_ids, palettes):
        for index in range(1, 4):
            connection.execute(
                """
                INSERT INTO product_images (product_id, image_url, sort_order)
                VALUES (?, ?, ?)
                """,
                (
                    product_id,
                    f"/static/images/product-placeholder.svg?c1={color1[1:]}&c2={color2[1:]}&v={index}",
                    index,
                ),
            )
