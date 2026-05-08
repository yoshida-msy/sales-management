import sqlite3
import io
import csv
import random
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, make_response

app = Flask(__name__)
app.secret_key = "sales_pro_dashboard_key"

DATABASE = "database.db"

# データベース接続
def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

# データベース初期化
def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                price INTEGER NOT NULL,
                stock INTEGER NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL,
                total_price INTEGER NOT NULL,
                status TEXT NOT NULL,
                order_date DATETIME NOT NULL,
                FOREIGN KEY (product_id) REFERENCES products (id)
            )
        """)
        conn.commit()

# 初期データ投入 (Seed機能)
def seed_db():
    with get_db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        if count == 0:
            sample_products = [
                ("高性能ノートPC", 120000, 15), ("ワイヤレスマウス", 3500, 50),
                ("27インチモニター", 32000, 8), ("メカニカルキーボード", 12000, 20),
                ("USB-C ハブ", 5800, 30), ("Webカメラ Pro", 8500, 3),
                ("エルゴノミクスチェア", 45000, 5), ("ポータブルSSD 1TB", 15000, 12)
            ]
            conn.executemany("INSERT INTO products (name, price, stock) VALUES (?, ?, ?)", sample_products)
            
            p_rows = conn.execute("SELECT id, price FROM products").fetchall()
            for _ in range(50): # グラフが綺麗に見えるよう50件生成
                p = random.choice(p_rows)
                days_ago = random.randint(0, 90) # 過去90日分
                o_time = datetime.now() - timedelta(days=days_ago)
                conn.execute("INSERT INTO orders (product_id, quantity, total_price, status, order_date) VALUES (?, ?, ?, ?, ?)",
                             (p["id"], random.randint(1, 2), p["price"], random.choice(["未請求", "請求済", "入金済"]), o_time))
            conn.commit()

init_db()
seed_db()

# --- ルーティング ---

@app.route("/")
def index():
    with get_db() as conn:
        # 1. KPI算出
        total_sales = conn.execute("SELECT SUM(total_price) FROM orders WHERE status != 'キャンセル'").fetchone()[0] or 0
        today_sales = conn.execute("SELECT SUM(total_price) FROM orders WHERE status != 'キャンセル' AND date(order_date) = date('now')").fetchone()[0] or 0
        uninvoiced = conn.execute("SELECT COUNT(*) FROM orders WHERE status = '未請求'").fetchone()[0] or 0
        low_stock = conn.execute("SELECT COUNT(*) FROM products WHERE stock < 5").fetchone()[0] or 0
        
        # 2. 月別売上グラフデータ (直近6ヶ月)
        monthly_raw = conn.execute("""
            SELECT strftime('%Y-%m', order_date) as m, SUM(total_price) as s 
            FROM orders WHERE status != 'キャンセル' GROUP BY m ORDER BY m DESC LIMIT 6
        """).fetchall()
        m_labels = [r["m"] for r in reversed(monthly_raw)]
        m_values = [r["s"] for r in reversed(monthly_raw)]
        
        # 3. 日別売上グラフデータ (直近7日間)
        daily_raw = conn.execute("""
            SELECT date(order_date) as d, SUM(total_price) as s 
            FROM orders WHERE status != 'キャンセル' AND order_date >= date('now', '-7 days')
            GROUP BY d ORDER BY d ASC
        """).fetchall()
        d_labels = [r["d"] for r in daily_raw]
        d_values = [r["s"] for r in daily_raw]
        
        # 4. ステータス別比率
        status_raw = conn.execute("SELECT status, COUNT(*) as c FROM orders GROUP BY status").fetchall()
        s_labels = [r["status"] for r in status_raw]
        s_values = [r["c"] for r in status_raw]

        recent = conn.execute("SELECT o.*, p.name FROM orders o JOIN products p ON o.product_id = p.id ORDER BY o.order_date DESC LIMIT 5").fetchall()

    return render_template("dashboard.html", 
                           total_sales=total_sales, today_sales=today_sales, 
                           uninvoiced=uninvoiced, low_stock=low_stock,
                           m_labels=m_labels, m_values=m_values,
                           d_labels=d_labels, d_values=d_values,
                           s_labels=s_labels, s_values=s_values, recent=recent)

# 他のルート (products, orders, edit, delete, export) は既存通り
@app.route("/products", methods=["GET", "POST"])
def products():
    conn = get_db()
    if request.method == "POST":
        conn.execute("INSERT INTO products (name, price, stock) VALUES (?, ?, ?)", (request.form["name"], request.form["price"], request.form["stock"]))
        conn.commit()
        return redirect(url_for("products"))
    items = conn.execute("SELECT * FROM products").fetchall()
    return render_template("products.html", products=items)

@app.route("/orders")
def orders():
    with get_db() as conn:
        items = conn.execute("SELECT o.*, p.name FROM orders o JOIN products p ON o.product_id = p.id ORDER BY o.order_date DESC").fetchall()
    return render_template("orders.html", orders=items)

@app.route("/orders/add", methods=["GET", "POST"])
def add_order():
    conn = get_db()
    if request.method == "POST":
        p_id = request.form["product_id"]
        qty = int(request.form["quantity"])
        p = conn.execute("SELECT * FROM products WHERE id = ?", (p_id,)).fetchone()
        if p and p["stock"] >= qty:
            conn.execute("INSERT INTO orders (product_id, quantity, total_price, status, order_date) VALUES (?, ?, ?, ?, ?)",
                         (p_id, qty, p["price"] * qty, "未請求", datetime.now()))
            conn.execute("UPDATE products SET stock = stock - ? WHERE id = ?", (qty, p_id))
            conn.commit()
            return redirect(url_for("orders"))
    prods = conn.execute("SELECT * FROM products").fetchall()
    return render_template("add_order.html", products=prods)

if __name__ == "__main__":
    app.run(debug=True)
