import sqlite3
from flask import Flask, render_template, request, redirect, url_for, flash
from datetime import datetime

app = Flask(__name__)
app.secret_key = "sales_tool_secret_key"  # フラッシュメッセージ用

DATABASE = "database.db"

# データベースに接続する関数
def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row  # 結果を辞書形式で取得できるようにする
    return conn

# データベースの初期化（テーブル作成）
def init_db():
    with get_db() as conn:
        # 商品テーブル
        conn.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                price INTEGER NOT NULL,
                stock INTEGER NOT NULL
            )
        """)
        # 受注テーブル
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

# アプリ起動時にデータベースを初期化
init_db()

# --- ルーティング ---

# 1. ダッシュボード（売上管理）
@app.route("/")
def index():
    with get_db() as conn:
        # 全売上の合計を計算
        total_sales = conn.execute("SELECT SUM(total_price) FROM orders WHERE status != 'キャンセル'").fetchone()[0] or 0
        # 受注件数
        order_count = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] or 0
        # 最近の受注5件
        recent_orders = conn.execute("""
            SELECT o.id, strftime('%Y-%m-%d %H:%M:%S', o.order_date) as order_date, 
                   o.product_id, o.quantity, o.total_price, o.status, p.name as product_name 
            FROM orders o 
            JOIN products p ON o.product_id = p.id 
            ORDER BY o.order_date DESC LIMIT 5
        """).fetchall()
    
    return render_template("dashboard.html", total_sales=total_sales, order_count=order_count, recent_orders=recent_orders)

# 2. 商品管理
@app.route("/products", methods=["GET", "POST"])
def products():
    conn = get_db()
    if request.method == "POST":
        name = request.form["name"]
        price = int(request.form["price"])
        stock = int(request.form["stock"])
        
        conn.execute("INSERT INTO products (name, price, stock) VALUES (?, ?, ?)", (name, price, stock))
        conn.commit()
        flash("商品を登録しました")
        return redirect(url_for("products"))
    
    products_list = conn.execute("SELECT * FROM products").fetchall()
    return render_template("products.html", products=products_list)

# 3. 受注管理（一覧）
@app.route("/orders")
def orders():
    with get_db() as conn:
        orders_list = conn.execute("""
            SELECT o.id, strftime('%Y-%m-%d %H:%M:%S', o.order_date) as order_date, 
                   o.product_id, o.quantity, o.total_price, o.status, p.name as product_name 
            FROM orders o 
            JOIN products p ON o.product_id = p.id 
            ORDER BY o.order_date DESC
        """).fetchall()
    return render_template("orders.html", orders=orders_list)

# 4. 受注登録
@app.route("/orders/add", methods=["GET", "POST"])
def add_order():
    conn = get_db()
    if request.method == "POST":
        product_id = int(request.form["product_id"])
        quantity = int(request.form["quantity"])
        
        # 商品情報の取得（単価と在庫の確認）
        product = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        
        if product and product["stock"] >= quantity:
            total_price = product["price"] * quantity
            # 受注を登録
            conn.execute("""
                INSERT INTO orders (product_id, quantity, total_price, status, order_date) 
                VALUES (?, ?, ?, ?, ?)
            """, (product_id, quantity, total_price, "未請求", datetime.now()))
            
            # 在庫を減算（自動在庫管理）
            conn.execute("UPDATE products SET stock = stock - ? WHERE id = ?", (quantity, product_id))
            conn.commit()
            flash("受注を登録しました（在庫を更新しました）")
            return redirect(url_for("orders"))
        else:
            flash("在庫が足りないため受注できません")
    
    products_list = conn.execute("SELECT * FROM products").fetchall()
    return render_template("add_order.html", products=products_list)

# 5. 請求ステータスの更新
@app.route("/orders/update_status/<int:order_id>", methods=["POST"])
def update_status(order_id):
    new_status = request.form["status"]
    with get_db() as conn:
        conn.execute("UPDATE orders SET status = ? WHERE id = ?", (new_status, order_id))
        conn.commit()
    flash(f"ステータスを {new_status} に更新しました")
    return redirect(url_for("orders"))

if __name__ == "__main__":
    app.run(debug=True)
