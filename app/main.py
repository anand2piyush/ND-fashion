import json
import re
import secrets
import shutil
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlparse

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.database import BASE_DIR, db, init_db
from app.security import create_session, hash_password, read_session, verify_password
from owner_config import OWNER_PASSWORD, OWNER_USERNAME


UPLOAD_DIR = BASE_DIR / "static" / "uploads"
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
ORDER_STATUSES = [
    "Placed",
    "Confirmed",
    "Packed",
    "Shipped",
    "Out for delivery",
    "Delivered",
    "Cancelled",
]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    init_db()
    yield


app = FastAPI(title="Shree NavDurga Vastralaya", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def money(value):
    return f"₹{value:,.0f}"


templates.env.filters["money"] = money


def normalize_instagram_url(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    if "://" not in value:
        value = f"https://{value}"
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").lower()
    if hostname not in {"instagram.com", "www.instagram.com"}:
        raise ValueError("Instagram URLs must use instagram.com.")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2 or parts[0] not in {"p", "reel", "reels", "tv"}:
        raise ValueError("Use a direct Instagram post or reel URL.")
    media_type = "reel" if parts[0] in {"reel", "reels"} else parts[0]
    return f"https://www.instagram.com/{media_type}/{parts[1]}/"


def instagram_embed_url(value: str) -> str:
    canonical = normalize_instagram_url(value)
    return f"{canonical}embed/" if canonical else ""


templates.env.filters["instagram_embed_url"] = instagram_embed_url


def current_customer(request: Request):
    session = read_session(request.cookies.get("ndf_session"))
    if not session or session.get("role") != "customer":
        return None
    with db() as connection:
        return connection.execute(
            "SELECT id, name, mobile, email FROM customers WHERE id = ?",
            (session["sub"],),
        ).fetchone()


def owner_logged_in(request: Request):
    session = read_session(request.cookies.get("ndf_session"))
    return bool(
        session
        and session.get("role") == "owner"
        and session.get("sub") == OWNER_USERNAME
    )


def page_context(request: Request, **kwargs):
    customer = current_customer(request)
    cart_count = 0
    if customer:
        with db() as connection:
            cart_count = connection.execute(
                "SELECT COALESCE(SUM(quantity), 0) AS count FROM cart_items WHERE customer_id = ?",
                (customer["id"],),
            ).fetchone()["count"]
    return {
        "request": request,
        "customer": customer,
        "cart_count": cart_count,
        **kwargs,
    }


def redirect_to_login(request: Request):
    return RedirectResponse(
        f"/login?next={quote(request.url.path)}", status_code=303
    )


def product_query(where="", order_by="p.created_at DESC", params=()):
    with db() as connection:
        rows = connection.execute(
            f"""
            SELECT p.*, c.name AS category_name, c.slug AS category_slug,
                   (SELECT image_url FROM product_images
                    WHERE product_id = p.id ORDER BY sort_order LIMIT 1) AS image_url
            FROM products p
            JOIN categories c ON c.id = p.category_id
            WHERE p.is_active = 1 {where}
            ORDER BY {order_by}
            """,
            params,
        ).fetchall()
    return rows


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    with db() as connection:
        categories = connection.execute(
            """
            SELECT c.*, COUNT(p.id) AS product_count
            FROM categories c LEFT JOIN products p
            ON p.category_id = c.id AND p.is_active = 1
            GROUP BY c.id ORDER BY c.id
            """
        ).fetchall()
    new_arrivals = product_query("AND p.is_new = 1", "p.created_at DESC", ())[:8]
    return templates.TemplateResponse(
        request,
        "home.html",
        page_context(
            request, categories=categories, products=new_arrivals, title="Home"
        ),
    )


@app.get("/products", response_class=HTMLResponse)
def products(request: Request, q: str = "", category: str = ""):
    clauses = []
    params = []
    if q.strip():
        clauses.append(
            "AND (p.name LIKE ? OR p.brand LIKE ? OR p.short_detail LIKE ?)"
        )
        term = f"%{q.strip()}%"
        params.extend([term, term, term])
    if category:
        clauses.append("AND c.slug = ?")
        params.append(category)
    with db() as connection:
        categories = connection.execute(
            "SELECT * FROM categories ORDER BY name"
        ).fetchall()
    rows = product_query(" ".join(clauses), "p.created_at DESC", tuple(params))
    return templates.TemplateResponse(
        request,
        "products.html",
        page_context(
            request,
            products=rows,
            categories=categories,
            q=q,
            selected_category=category,
            title="Shop Sarees",
        ),
    )


@app.get("/products/{product_id}", response_class=HTMLResponse)
def product_detail(request: Request, product_id: int):
    with db() as connection:
        product = connection.execute(
            """
            SELECT p.*, c.name AS category_name
            FROM products p JOIN categories c ON c.id = p.category_id
            WHERE p.id = ? AND p.is_active = 1
            """,
            (product_id,),
        ).fetchone()
        if not product:
            return templates.TemplateResponse(
                request,
                "message.html",
                page_context(
                    request,
                    title="Product not found",
                    heading="Product not found",
                    message="This product is no longer available.",
                ),
                status_code=404,
            )
        images = connection.execute(
            "SELECT * FROM product_images WHERE product_id = ? ORDER BY sort_order",
            (product_id,),
        ).fetchall()
        reviews = connection.execute(
            """
            SELECT r.*, c.name AS customer_name
            FROM reviews r JOIN customers c ON c.id = r.customer_id
            WHERE r.product_id = ? AND r.is_visible = 1
            ORDER BY r.created_at DESC
            """,
            (product_id,),
        ).fetchall()
    instagram_urls = [
        url
        for url in (product["instagram_url_1"], product["instagram_url_2"])
        if url
    ]
    discounted_price = product["price"] * (100 - product["discount_percent"]) / 100
    return templates.TemplateResponse(
        request,
        "product_detail.html",
        page_context(
            request,
            product=product,
            images=images,
            instagram_urls=instagram_urls,
            reviews=reviews,
            discounted_price=discounted_price,
            title=product["name"],
        ),
    )


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    return templates.TemplateResponse(
        request,
        "auth.html",
        page_context(request, mode="register", title="Create account"),
    )


@app.post("/register")
def register(
    request: Request,
    name: str = Form(...),
    mobile: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
):
    error = None
    mobile = re.sub(r"\D", "", mobile)
    email = email.strip().lower()
    if len(name.strip()) < 2:
        error = "Please enter your full name."
    elif len(mobile) < 10:
        error = "Please enter a valid mobile number."
    elif not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        error = "Please enter a valid email address."
    elif len(password) < 8:
        error = "Password must be at least 8 characters."
    if not error:
        try:
            with db() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO customers (name, mobile, email, password_hash)
                    VALUES (?, ?, ?, ?)
                    """,
                    (name.strip(), mobile, email, hash_password(password)),
                )
                customer_id = cursor.lastrowid
            response = RedirectResponse("/profile", status_code=303)
            response.set_cookie(
                "ndf_session",
                create_session(str(customer_id), "customer"),
                httponly=True,
                samesite="lax",
                max_age=60 * 60 * 24 * 14,
            )
            return response
        except Exception as exc:
            if "UNIQUE constraint failed" in str(exc):
                error = "That mobile number or email is already registered."
            else:
                raise
    return templates.TemplateResponse(
        request,
        "auth.html",
        page_context(
            request,
            mode="register",
            title="Create account",
            error=error,
            form={"name": name, "mobile": mobile, "email": email},
        ),
        status_code=400,
    )


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/"):
    return templates.TemplateResponse(
        request,
        "auth.html",
        page_context(request, mode="login", next=next, title="Sign in"),
    )


@app.post("/login")
def login(
    request: Request,
    identifier: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
):
    with db() as connection:
        customer = connection.execute(
            """
            SELECT * FROM customers
            WHERE lower(email) = lower(?) OR mobile = ?
            """,
            (identifier.strip(), re.sub(r"\D", "", identifier)),
        ).fetchone()
    if not customer or not verify_password(password, customer["password_hash"]):
        return templates.TemplateResponse(
            request,
            "auth.html",
            page_context(
                request,
                mode="login",
                next=next,
                title="Sign in",
                error="Incorrect email/mobile number or password.",
            ),
            status_code=401,
        )
    safe_next = next if next.startswith("/") and not next.startswith("//") else "/"
    response = RedirectResponse(safe_next, status_code=303)
    response.set_cookie(
        "ndf_session",
        create_session(str(customer["id"]), "customer"),
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 14,
    )
    return response


@app.post("/logout")
def logout():
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie("ndf_session")
    return response


@app.get("/profile", response_class=HTMLResponse)
def profile(request: Request):
    customer = current_customer(request)
    if not customer:
        return redirect_to_login(request)
    with db() as connection:
        addresses = connection.execute(
            "SELECT * FROM addresses WHERE customer_id = ? ORDER BY id",
            (customer["id"],),
        ).fetchall()
    return templates.TemplateResponse(
        request,
        "profile.html",
        page_context(
            request, addresses=addresses, address_limit=3, title="My profile"
        ),
    )


@app.post("/profile")
def update_profile(
    request: Request,
    name: str = Form(...),
    mobile: str = Form(...),
    email: str = Form(...),
):
    customer = current_customer(request)
    if not customer:
        return redirect_to_login(request)
    try:
        with db() as connection:
            connection.execute(
                "UPDATE customers SET name = ?, mobile = ?, email = ? WHERE id = ?",
                (
                    name.strip(),
                    re.sub(r"\D", "", mobile),
                    email.strip().lower(),
                    customer["id"],
                ),
            )
        return RedirectResponse("/profile?saved=1", status_code=303)
    except Exception as exc:
        if "UNIQUE constraint failed" in str(exc):
            return RedirectResponse("/profile?error=duplicate", status_code=303)
        raise


@app.post("/addresses")
def add_address(
    request: Request,
    label: str = Form(...),
    recipient_name: str = Form(...),
    mobile: str = Form(...),
    line1: str = Form(...),
    line2: str = Form(""),
    city: str = Form(...),
    state: str = Form(...),
    pin_code: str = Form(...),
):
    customer = current_customer(request)
    if not customer:
        return redirect_to_login(request)
    with db() as connection:
        count = connection.execute(
            "SELECT COUNT(*) AS count FROM addresses WHERE customer_id = ?",
            (customer["id"],),
        ).fetchone()["count"]
        if count >= 3:
            return RedirectResponse("/profile?error=limit", status_code=303)
        connection.execute(
            """
            INSERT INTO addresses (
                customer_id, label, recipient_name, mobile, line1, line2,
                city, state, pin_code
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                customer["id"],
                label if label in {"Home", "Office", "Other"} else "Other",
                recipient_name.strip(),
                re.sub(r"\D", "", mobile),
                line1.strip(),
                line2.strip(),
                city.strip(),
                state.strip(),
                re.sub(r"\D", "", pin_code),
            ),
        )
    return RedirectResponse("/profile?address_saved=1", status_code=303)


@app.post("/addresses/{address_id}/delete")
def delete_address(request: Request, address_id: int):
    customer = current_customer(request)
    if not customer:
        return redirect_to_login(request)
    with db() as connection:
        connection.execute(
            "DELETE FROM addresses WHERE id = ? AND customer_id = ?",
            (address_id, customer["id"]),
        )
    return RedirectResponse("/profile", status_code=303)


@app.post("/cart/add/{product_id}")
def add_to_cart(
    request: Request,
    product_id: int,
    quantity: int = Form(1),
    action: str = Form("cart"),
):
    customer = current_customer(request)
    if not customer:
        return redirect_to_login(request)
    quantity = max(1, min(quantity, 10))
    with db() as connection:
        product = connection.execute(
            "SELECT stock FROM products WHERE id = ? AND is_active = 1",
            (product_id,),
        ).fetchone()
        if not product or product["stock"] < 1:
            return RedirectResponse(f"/products/{product_id}?error=stock", status_code=303)
        connection.execute(
            """
            INSERT INTO cart_items (customer_id, product_id, quantity)
            VALUES (?, ?, ?)
            ON CONFLICT(customer_id, product_id)
            DO UPDATE SET quantity = MIN(quantity + excluded.quantity, 10)
            """,
            (customer["id"], product_id, quantity),
        )
    return RedirectResponse("/checkout" if action == "buy" else "/cart", status_code=303)


@app.get("/cart", response_class=HTMLResponse)
def cart(request: Request):
    customer = current_customer(request)
    if not customer:
        return redirect_to_login(request)
    items, total = get_cart(customer["id"])
    return templates.TemplateResponse(
        request,
        "cart.html",
        page_context(request, items=items, total=total, title="Your cart"),
    )


def get_cart(customer_id):
    with db() as connection:
        items = connection.execute(
            """
            SELECT ci.*, p.name, p.price, p.discount_percent, p.stock,
                   (p.price * (100 - p.discount_percent) / 100.0) AS final_price,
                   (SELECT image_url FROM product_images
                    WHERE product_id = p.id ORDER BY sort_order LIMIT 1) AS image_url
            FROM cart_items ci JOIN products p ON p.id = ci.product_id
            WHERE ci.customer_id = ? AND p.is_active = 1
            ORDER BY ci.id DESC
            """,
            (customer_id,),
        ).fetchall()
    total = sum(item["final_price"] * item["quantity"] for item in items)
    return items, total


@app.post("/cart/{item_id}")
def update_cart(
    request: Request,
    item_id: int,
    quantity: int = Form(...),
):
    customer = current_customer(request)
    if not customer:
        return redirect_to_login(request)
    with db() as connection:
        if quantity <= 0:
            connection.execute(
                "DELETE FROM cart_items WHERE id = ? AND customer_id = ?",
                (item_id, customer["id"]),
            )
        else:
            connection.execute(
                """
                UPDATE cart_items SET quantity = ?
                WHERE id = ? AND customer_id = ?
                """,
                (min(quantity, 10), item_id, customer["id"]),
            )
    return RedirectResponse("/cart", status_code=303)


@app.get("/checkout", response_class=HTMLResponse)
def checkout(request: Request):
    customer = current_customer(request)
    if not customer:
        return redirect_to_login(request)
    items, total = get_cart(customer["id"])
    if not items:
        return RedirectResponse("/cart", status_code=303)
    with db() as connection:
        addresses = connection.execute(
            "SELECT * FROM addresses WHERE customer_id = ? ORDER BY id",
            (customer["id"],),
        ).fetchall()
    return templates.TemplateResponse(
        request,
        "checkout.html",
        page_context(
            request,
            items=items,
            total=total,
            addresses=addresses,
            title="Confirm order",
        ),
    )


@app.post("/checkout")
def place_order(request: Request, address_id: int = Form(...)):
    customer = current_customer(request)
    if not customer:
        return redirect_to_login(request)
    items, total = get_cart(customer["id"])
    if not items:
        return RedirectResponse("/cart", status_code=303)
    with db() as connection:
        address = connection.execute(
            "SELECT * FROM addresses WHERE id = ? AND customer_id = ?",
            (address_id, customer["id"]),
        ).fetchone()
        if not address:
            return RedirectResponse("/checkout?error=address", status_code=303)
        order_number = f"NDV{datetime.now():%y%m%d}{secrets.randbelow(9000) + 1000}"
        tracking_token = secrets.token_urlsafe(18)
        cursor = connection.execute(
            """
            INSERT INTO orders (
                order_number, tracking_token, customer_id, address_snapshot, subtotal
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                order_number,
                tracking_token,
                customer["id"],
                json.dumps(dict(address)),
                total,
            ),
        )
        order_id = cursor.lastrowid
        for item in items:
            connection.execute(
                """
                INSERT INTO order_items (
                    order_id, product_id, product_name, unit_price, quantity
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    order_id,
                    item["product_id"],
                    item["name"],
                    item["final_price"],
                    item["quantity"],
                ),
            )
            connection.execute(
                "UPDATE products SET stock = MAX(stock - ?, 0) WHERE id = ?",
                (item["quantity"], item["product_id"]),
            )
        connection.execute(
            "DELETE FROM cart_items WHERE customer_id = ?", (customer["id"],)
        )
    return RedirectResponse(f"/orders/{order_id}?placed=1", status_code=303)


@app.get("/orders", response_class=HTMLResponse)
def orders(request: Request):
    customer = current_customer(request)
    if not customer:
        return redirect_to_login(request)
    with db() as connection:
        rows = connection.execute(
            """
            SELECT o.*, COUNT(oi.id) AS item_count
            FROM orders o LEFT JOIN order_items oi ON oi.order_id = o.id
            WHERE o.customer_id = ? GROUP BY o.id ORDER BY o.created_at DESC
            """,
            (customer["id"],),
        ).fetchall()
    return templates.TemplateResponse(
        request,
        "orders.html",
        page_context(request, orders=rows, title="My orders"),
    )


@app.get("/orders/{order_id}", response_class=HTMLResponse)
def order_detail(request: Request, order_id: int):
    customer = current_customer(request)
    if not customer:
        return redirect_to_login(request)
    with db() as connection:
        order = connection.execute(
            "SELECT * FROM orders WHERE id = ? AND customer_id = ?",
            (order_id, customer["id"]),
        ).fetchone()
        items = (
            connection.execute(
                "SELECT * FROM order_items WHERE order_id = ?", (order_id,)
            ).fetchall()
            if order
            else []
        )
    if not order:
        return RedirectResponse("/orders", status_code=303)
    return templates.TemplateResponse(
        request,
        "order_detail.html",
        page_context(
            request,
            order=order,
            items=items,
            address=json.loads(order["address_snapshot"]),
            statuses=ORDER_STATUSES,
            title=f"Order {order['order_number']}",
        ),
    )


@app.get("/track/{tracking_token}", response_class=HTMLResponse)
def public_tracking(request: Request, tracking_token: str):
    with db() as connection:
        order = connection.execute(
            """
            SELECT o.order_number, o.status, o.updated_at, o.created_at,
                   COUNT(oi.id) AS item_count
            FROM orders o LEFT JOIN order_items oi ON oi.order_id = o.id
            WHERE o.tracking_token = ? GROUP BY o.id
            """,
            (tracking_token,),
        ).fetchone()
    return templates.TemplateResponse(
        request,
        "tracking.html",
        page_context(request, order=order, title="Track order"),
        status_code=200 if order else 404,
    )


@app.post("/products/{product_id}/reviews")
def submit_review(
    request: Request,
    product_id: int,
    rating: int = Form(...),
    comment: str = Form(...),
):
    customer = current_customer(request)
    if not customer:
        return redirect_to_login(request)
    with db() as connection:
        connection.execute(
            """
            INSERT INTO reviews (product_id, customer_id, rating, comment)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(product_id, customer_id)
            DO UPDATE SET rating = excluded.rating, comment = excluded.comment,
                          is_visible = 1, created_at = CURRENT_TIMESTAMP
            """,
            (
                product_id,
                customer["id"],
                max(1, min(rating, 5)),
                comment.strip()[:1000],
            ),
        )
    return RedirectResponse(f"/products/{product_id}#reviews", status_code=303)


@app.get("/owner/login", response_class=HTMLResponse)
def owner_login_page(request: Request):
    return templates.TemplateResponse(
        request,
        "owner_login.html",
        page_context(request, title="Owner login"),
    )


@app.post("/owner/login")
def owner_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    if not (
        secrets.compare_digest(username, OWNER_USERNAME)
        and secrets.compare_digest(password, OWNER_PASSWORD)
    ):
        return templates.TemplateResponse(
            request,
            "owner_login.html",
            page_context(
                request, title="Owner login", error="Incorrect owner credentials."
            ),
            status_code=401,
        )
    response = RedirectResponse("/owner", status_code=303)
    response.set_cookie(
        "ndf_session",
        create_session(OWNER_USERNAME, "owner"),
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24,
    )
    return response


@app.get("/owner", response_class=HTMLResponse)
def owner_dashboard(request: Request):
    if not owner_logged_in(request):
        return RedirectResponse("/owner/login", status_code=303)
    with db() as connection:
        stats = {
            "orders": connection.execute(
                "SELECT COUNT(*) AS count FROM orders"
            ).fetchone()["count"],
            "pending": connection.execute(
                "SELECT COUNT(*) AS count FROM orders WHERE status IN ('Placed', 'Confirmed')"
            ).fetchone()["count"],
            "products": connection.execute(
                "SELECT COUNT(*) AS count FROM products WHERE is_active = 1"
            ).fetchone()["count"],
            "customers": connection.execute(
                "SELECT COUNT(*) AS count FROM customers"
            ).fetchone()["count"],
        }
        orders = connection.execute(
            """
            SELECT o.*, c.name AS customer_name, c.mobile AS customer_mobile
            FROM orders o JOIN customers c ON c.id = o.customer_id
            ORDER BY o.created_at DESC LIMIT 20
            """
        ).fetchall()
        products = connection.execute(
            """
            SELECT p.*, c.name AS category_name
            FROM products p JOIN categories c ON c.id = p.category_id
            ORDER BY p.created_at DESC
            """
        ).fetchall()
        reviews = connection.execute(
            """
            SELECT r.*, c.name AS customer_name, p.name AS product_name
            FROM reviews r JOIN customers c ON c.id = r.customer_id
            JOIN products p ON p.id = r.product_id
            ORDER BY r.created_at DESC
            """
        ).fetchall()
    return templates.TemplateResponse(
        request,
        "owner_dashboard.html",
        page_context(
            request,
            stats=stats,
            orders=orders,
            products=products,
            reviews=reviews,
            statuses=ORDER_STATUSES,
            title="Owner dashboard",
        ),
    )


@app.post("/owner/orders/{order_id}")
def owner_update_order(
    request: Request,
    order_id: int,
    status: str = Form(...),
    owner_note: str = Form(""),
):
    if not owner_logged_in(request):
        return RedirectResponse("/owner/login", status_code=303)
    if status not in ORDER_STATUSES:
        status = "Placed"
    with db() as connection:
        connection.execute(
            """
            UPDATE orders SET status = ?, owner_note = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, owner_note.strip(), order_id),
        )
    return RedirectResponse("/owner#orders", status_code=303)


@app.get("/owner/products/new", response_class=HTMLResponse)
def owner_new_product(request: Request):
    if not owner_logged_in(request):
        return RedirectResponse("/owner/login", status_code=303)
    with db() as connection:
        categories = connection.execute(
            "SELECT * FROM categories ORDER BY name"
        ).fetchall()
    return templates.TemplateResponse(
        request,
        "owner_product.html",
        page_context(
            request, categories=categories, product=None, title="Add product"
        ),
    )


@app.get("/owner/products/{product_id}/edit", response_class=HTMLResponse)
def owner_edit_product(request: Request, product_id: int):
    if not owner_logged_in(request):
        return RedirectResponse("/owner/login", status_code=303)
    with db() as connection:
        categories = connection.execute(
            "SELECT * FROM categories ORDER BY name"
        ).fetchall()
        product = connection.execute(
            "SELECT * FROM products WHERE id = ?", (product_id,)
        ).fetchone()
    return templates.TemplateResponse(
        request,
        "owner_product.html",
        page_context(
            request,
            categories=categories,
            product=product,
            title="Edit product",
        ),
    )


async def save_product_images(connection, product_id: int, images):
    existing_count = connection.execute(
        "SELECT COUNT(*) AS count FROM product_images WHERE product_id = ?",
        (product_id,),
    ).fetchone()["count"]
    for index, image in enumerate(images[:6]):
        if not image.filename or image.content_type not in ALLOWED_IMAGE_TYPES:
            continue
        extension = Path(image.filename).suffix.lower()
        if extension not in {".jpg", ".jpeg", ".png", ".webp"}:
            extension = ".jpg"
        filename = f"{product_id}-{secrets.token_hex(8)}{extension}"
        target = UPLOAD_DIR / filename
        with target.open("wb") as output:
            shutil.copyfileobj(image.file, output)
        connection.execute(
            """
            INSERT INTO product_images (product_id, image_url, sort_order)
            VALUES (?, ?, ?)
            """,
            (product_id, f"/static/uploads/{filename}", existing_count + index),
        )


@app.post("/owner/products/save")
async def owner_save_product(
    request: Request,
    product_id: int = Form(0),
    category_id: int = Form(...),
    name: str = Form(...),
    brand: str = Form(...),
    short_detail: str = Form(...),
    description: str = Form(...),
    price: float = Form(...),
    discount_percent: int = Form(0),
    stock: int = Form(0),
    is_new: int = Form(0),
    is_active: int = Form(0),
    instagram_url_1: str = Form(""),
    instagram_url_2: str = Form(""),
    images: list[UploadFile] = File(default=[]),
):
    if not owner_logged_in(request):
        return RedirectResponse("/owner/login", status_code=303)
    try:
        instagram_urls = (
            normalize_instagram_url(instagram_url_1),
            normalize_instagram_url(instagram_url_2),
        )
    except ValueError:
        destination = (
            f"/owner/products/{product_id}/edit"
            if product_id
            else "/owner/products/new"
        )
        return RedirectResponse(
            f"{destination}?error=instagram", status_code=303
        )
    with db() as connection:
        values = (
            category_id,
            name.strip(),
            brand.strip(),
            short_detail.strip(),
            description.strip(),
            max(0, price),
            max(0, min(discount_percent, 90)),
            max(0, stock),
            1 if is_new else 0,
            1 if is_active else 0,
            *instagram_urls,
        )
        if product_id:
            connection.execute(
                """
                UPDATE products SET category_id = ?, name = ?, brand = ?,
                    short_detail = ?, description = ?, price = ?,
                    discount_percent = ?, stock = ?, is_new = ?, is_active = ?,
                    instagram_url_1 = ?, instagram_url_2 = ?
                WHERE id = ?
                """,
                (*values, product_id),
            )
        else:
            cursor = connection.execute(
                """
                INSERT INTO products (
                    category_id, name, brand, short_detail, description,
                    price, discount_percent, stock, is_new, is_active,
                    instagram_url_1, instagram_url_2
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            product_id = cursor.lastrowid
        await save_product_images(connection, product_id, images)
    return RedirectResponse("/owner#products", status_code=303)


@app.post("/owner/reviews/{review_id}/toggle")
def owner_toggle_review(request: Request, review_id: int):
    if not owner_logged_in(request):
        return RedirectResponse("/owner/login", status_code=303)
    with db() as connection:
        connection.execute(
            "UPDATE reviews SET is_visible = CASE is_visible WHEN 1 THEN 0 ELSE 1 END WHERE id = ?",
            (review_id,),
        )
    return RedirectResponse("/owner#reviews", status_code=303)


@app.get("/health")
def health():
    return {"status": "ok", "service": "Shree NavDurga Vastralaya"}
