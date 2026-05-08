import sqlite3
import io
import csv
import random
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, make_response

app = Flask(__name__)
app.secret_key = "sales_pro_stable_key"

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
            print("初期データを投入しています...")
            sample_products = [
                ("高性能ノートPC", 120000, 15),
                ("ワイヤレスマウス", 3500, 50),
                ("27インチモニター", 32000, 8),
                ("メカニカルキーボード", 12000, 20),
                ("USB-C ハブ", 5800, 30),
                ("Webカメラ Pro", 8500, 3),
                ("エルゴノミクスチェア", 45000, 5),
                ("ポータブルSSD 1TB", 15000, 12),
                ("Bluetoothヘッドセット", 7200, 25),
                ("デスクライト LED", 4200, 40)
            ]
            conn.executemany("INSERT INTO products (name, price, stock) VALUES (?, ?, ?)", sample_products)
            
            product_rows = conn.execute("SELECT id, price FROM products").fetchall()
            statuses = ["未請求", "請求済", "入金済"]
            
            for _ in range(25):
                p = random.choice(product_rows)
                qty = random.randint(1, 3)
                days_ago = random.randint(0, 60)
                order_time = datetime.now() - timedelta(days=days_ago)
                conn.execute("INSERT INTO orders (product_id, quantity, total_price, status, order_date) VALUES (?, ?, ?, ?, ?)",
                             (p["id"], qty, p["price"] * qty, random.choice(statuses), order_time))
            conn.commit()

init_db()
seed_db()

# --- ルーティング ---

@app.route("/")
def index():
    with get_db() as conn:
        total_sales = conn.execute("SELECT SUM(total_price) FROM orders WHERE status != 'キャンセル'").fetchone()[0] or 0
        today_sales = conn.execute("SELECT SUM(total_price) FROM orders WHERE status != 'キャンセル' AND date(order_date) = date('now')").fetchone()[0] or 0
        uninvoiced_count = conn.execute("SELECT COUNT(*) FROM orders WHERE status = '未請求'").fetchone()[0] or 0
        low_stock_count = conn.execute("SELECT COUNT(*) FROM products WHERE stock < 5").fetchone()[0] or 0
        
        recent_orders = conn.execute("""
            SELECT o.id, strftime('%Y-%m-%d %H:%M:%S', o.order_date) as order_date, 
                   p.name as product_name, o.quantity, o.total_price, o.status 
            FROM orders o JOIN products p ON o.product_id = p.id 
            ORDER BY o.order_date DESC LIMIT 5
        """).fetchall()

        monthly_data = conn.execute("""
            SELECT strftime('%Y-%m', order_date) as month, SUM(total_price) as sales
            FROM orders WHERE status != 'キャンセル'
            GROUP BY month ORDER BY month DESC LIMIT 6
        """).fetchall()
        
        chart_labels = [row["month"] for row in reversed(monthly_data)]
        chart_values = [row["sales"] for row in reversed(monthly_data)]

    return render_template("dashboard.html", 
                           total_sales=total_sales, today_sales=today_sales,
                           uninvoiced_count=uninvoiced_count, low_stock_count=low_stock_count,
                           recent_orders=recent_orders,
                           chart_labels=chart_labels, chart_values=chart_values)

@app.route("/products", methods=["GET", "POST"])
def products():
    q = request.args.get("q", "")
    conn = get_db()
    if request.method == "POST":
        name = request.form["name"]
        price = int(request.form["price"])
        stock = int(request.form["stock"])
        conn.execute("INSERT INTO products (name, price, stock) VALUES (?, ?, ?)", (name, price, stock))
        conn.commit()
        flash(f"商品「{name}」を登録しました")
        return redirect(url_for("products"))
    
    if q:
        products_list = conn.execute("SELECT * FROM products WHERE name LIKE ?", ('%' + q + '%',)).fetchall()
    else:
        products_list = conn.execute("SELECT * FROM products").fetchall()
    return render_template("products.html", products=products_list, q=q)

@app.route("/products/edit/<int:id>", methods=["GET", "POST"])
def edit_product(id):
    conn = get_db()
    if request.method == "POST":
        conn.execute("UPDATE products SET name=?, price=?, stock=? WHERE id=?", 
                     (request.form["name"], int(request.form["price"]), int(request.form["stock"]), id))
        conn.commit()
        flash("商品情報を更新しました")
        return redirect(url_for("products"))
    product = conn.execute("SELECT * FROM products WHERE id=?", (id,)).fetchone()
    return render_template("edit_product.html", product=product)

@app.route("/products/delete/<int:id>", methods=["POST"])
def delete_product(id):
    with get_db() as conn:
        conn.execute("DELETE FROM products WHERE id=?", (id,))
        conn.commit()
    flash("商品を削除しました")
    return redirect(url_for("products"))

@app.route("/orders")
def orders():
    q = request.args.get("q", "")
    status_filter = request.args.get("status", "")
    query = """
        SELECT o.id, strftime('%Y-%m-%d %H:%M:%S', o.order_date) as order_date, 
               p.name as product_name, o.quantity, o.total_price, o.status 
        FROM orders o JOIN products p ON o.product_id = p.id
        WHERE (p.name LIKE ?)
    """
    params = ['%' + q + '%']
    if status_filter:
        query += " AND o.status = ?"
        params.append(status_filter)
    query += " ORDER BY o.order_date DESC"
    with get_db() as conn:
        orders_list = conn.execute(query, params).fetchall()
    return render_template("orders.html", orders=orders_list, q=q, status_filter=status_filter)

@app.route("/orders/add", methods=["GET", "POST"])
def add_order():
    conn = get_db()
    if request.method == "POST":
        product_id = int(request.form["product_id"])
        quantity = int(request.form["quantity"])
        product = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        if product and product["stock"] >= quantity:
            conn.execute("INSERT INTO orders (product_id, quantity, total_price, status, order_date) VALUES (?, ?, ?, ?, ?)",
                         (product_id, quantity, product["price"] * quantity, "未請求", datetime.now()))
            conn.execute("UPDATE products SET stock = stock - ? WHERE id = ?", (quantity, product_id))
            conn.commit()
            flash("受注を登録しました")
            return redirect(url_for("orders"))
        flash("在庫不足のため登録できません")
    products_list = conn.execute("SELECT * FROM products").fetchall()
    return render_template("add_order.html", products=products_list)

@app.route("/orders/update_status/<int:order_id>", methods=["POST"])
def update_status(order_id):
    new_status = request.form["status"]
    with get_db() as conn:
        conn.execute("UPDATE orders SET status = ? WHERE id = ?", (new_status, order_id))
        conn.commit()
    flash(f"ステータスを更新しました")
    return redirect(request.referrer or url_for("orders"))

@app.route("/export/orders")
def export_orders():
    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(["受注ID", "日時", "商品名", "数量", "合計金額", "ステータス"])
    with get_db() as conn:
        rows = conn.execute("""
            SELECT o.id, strftime('%Y-%m-%d %H:%M:%S', o.order_date), p.name, o.quantity, o.total_price, o.status
            FROM orders o JOIN products p ON o.product_id = p.id ORDER BY o.order_date DESC
        """).fetchall()
        for row in rows: cw.writerow(list(row))
    output = make_response(si.getvalue().encode('utf-8-sig'))
    output.headers["Content-Disposition"] = "attachment; filename=all_orders.csv"
    output.headers["Content-type"] = "text/csv"
    return output

@app.route("/export/recent")
def export_recent():
    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(["受注ID", "日時", "商品名", "数量", "合計金額", "ステータス"])
    with get_db() as conn:
        rows = conn.execute("""
            SELECT o.id, strftime('%Y-%m-%d %H:%M:%S', o.order_date), p.name, o.quantity, o.total_price, o.status
            FROM orders o JOIN products p ON o.product_id = p.id ORDER BY o.order_date DESC LIMIT 5
        """).fetchall()
        for row in rows: cw.writerow(list(row))
    output = make_response(si.getvalue().encode('utf-8-sig'))
    output.headers["Content-Disposition"] = "attachment; filename=recent_orders.csv"
    output.headers["Content-type"] = "text/csv"
    return output

if __name__ == "__main__":
    app.run(debug=True)
