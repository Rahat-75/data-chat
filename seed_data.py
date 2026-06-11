"""Seed the online store SQLite database with sample data."""

import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
DB_PATH = DATA_DIR / "store.db"

CITIES = [
    ("New York", "USA"),
    ("Los Angeles", "USA"),
    ("Chicago", "USA"),
    ("Houston", "USA"),
    ("London", "UK"),
    ("Berlin", "Germany"),
    ("Paris", "France"),
    ("Toronto", "Canada"),
    ("Sydney", "Australia"),
    ("Mumbai", "India"),
    ("Tokyo", "Japan"),
    ("Dubai", "UAE"),
]

FIRST_NAMES = [
    "Alice", "Bob", "Carla", "David", "Elena", "Frank", "Grace", "Henry",
    "Iris", "James", "Karen", "Leo", "Maya", "Noah", "Olivia", "Paul",
    "Quinn", "Rachel", "Sam", "Tina", "Uma", "Victor", "Wendy", "Xavier",
    "Yara", "Zoe", "Amir", "Bella", "Carlos", "Diana", "Ethan", "Fiona",
    "George", "Hannah", "Ian", "Julia", "Kevin", "Luna", "Marcus", "Nina",
    "Omar", "Priya", "Raj", "Sofia", "Tom", "Ursula", "Vince", "Will",
    "Xena", "Yusuf",
]

PRODUCTS = [
    ("Wireless Mouse", "Electronics", 29.99),
    ("Mechanical Keyboard", "Electronics", 89.99),
    ("USB-C Hub", "Electronics", 45.50),
    ("Noise Cancelling Headphones", "Electronics", 199.00),
    ("4K Monitor", "Electronics", 349.99),
    ("Laptop Stand", "Electronics", 39.99),
    ("Running Shoes", "Sports", 79.99),
    ("Yoga Mat", "Sports", 24.99),
    ("Dumbbell Set", "Sports", 129.00),
    ("Tennis Racket", "Sports", 89.50),
    ("Cycling Helmet", "Sports", 59.99),
    ("Hoodie", "Clothing", 49.99),
    ("Jeans", "Clothing", 69.99),
    ("Summer Dress", "Clothing", 54.00),
    ("Winter Jacket", "Clothing", 120.00),
    ("Sneakers", "Clothing", 85.00),
    ("Coffee Maker", "Home", 79.99),
    ("Blender", "Home", 49.50),
    ("Desk Lamp", "Home", 34.99),
    ("Throw Blanket", "Home", 29.99),
    ("Cookware Set", "Home", 149.00),
    ("Python Crash Course", "Books", 39.99),
    ("Data Science Handbook", "Books", 45.00),
    ("Clean Code", "Books", 42.50),
    ("Design Patterns", "Books", 48.00),
    ("The Pragmatic Programmer", "Books", 44.99),
    ("Smart Watch", "Electronics", 249.99),
    ("Tablet", "Electronics", 399.00),
    ("Backpack", "Clothing", 59.99),
    ("Water Bottle", "Sports", 19.99),
]


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS orders;
        DROP TABLE IF EXISTS products;
        DROP TABLE IF EXISTS customers;

        CREATE TABLE customers (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            city TEXT NOT NULL,
            country TEXT NOT NULL,
            signup_date TEXT NOT NULL
        );

        CREATE TABLE products (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            price REAL NOT NULL
        );

        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            customer_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL,
            order_date TEXT NOT NULL,
            amount REAL NOT NULL,
            FOREIGN KEY (customer_id) REFERENCES customers(id),
            FOREIGN KEY (product_id) REFERENCES products(id)
        );
        """
    )


def seed(conn: sqlite3.Connection) -> None:
    rng = random.Random(42)
    start = date(2024, 1, 1)
    end = date(2025, 5, 31)

    customers = []
    for i, first in enumerate(FIRST_NAMES, start=1):
        city, country = rng.choice(CITIES)
        days_offset = rng.randint(0, 400)
        signup = start + timedelta(days=days_offset)
        customers.append((i, f"{first} {rng.choice(['Smith', 'Lee', 'Garcia', 'Kim', 'Patel'])}", city, country, signup.isoformat()))

    conn.executemany(
        "INSERT INTO customers (id, name, city, country, signup_date) VALUES (?, ?, ?, ?, ?)",
        customers,
    )

    conn.executemany(
        "INSERT INTO products (id, name, category, price) VALUES (?, ?, ?, ?)",
        [(i + 1, name, cat, price) for i, (name, cat, price) in enumerate(PRODUCTS)],
    )

    orders = []
    order_id = 1
    for _ in range(280):
        customer_id = rng.randint(1, len(FIRST_NAMES))
        product_id = rng.randint(1, len(PRODUCTS))
        quantity = rng.randint(1, 4)
        price = PRODUCTS[product_id - 1][2]
        amount = round(price * quantity, 2)
        day_offset = rng.randint(0, (end - start).days)
        order_date = (start + timedelta(days=day_offset)).isoformat()
        orders.append((order_id, customer_id, product_id, quantity, order_date, amount))
        order_id += 1

    conn.executemany(
        """
        INSERT INTO orders (id, customer_id, product_id, quantity, order_date, amount)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        orders,
    )


def seed_database(db_path: Path = DB_PATH) -> Path:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        create_schema(conn)
        seed(conn)
    return db_path


if __name__ == "__main__":
    path = seed_database()
    print(f"Seeded {path}")
