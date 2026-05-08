import sqlite3
import io
import csv
from flask import Flask, render_template, request, redirect, url_for, flash, make_response
from datetime import datetime

app = Flask(__name__)
app.secret_key = "sales_pro_ultra_secret"

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

init_db()

# --- ルーティング ---

# 1. ダッシュボード (KPI & グラフ)
@app.route("/")
def index():
    with get_db() as conn:
        # KPIデータ
        total_sales = conn.execute("SELECT SUM(total_price) FROM orders WHERE status != 'キャンセル'").fetchone()[0] or 0
        today_sales = conn.execute("SELECT SUM(total_price) FROM orders WHERE status != 'キャンセル' AND date(order_date) = date('now')").fetchone()[0] or 0
        uninvoiced_count = conn.execute("SELECT COUNT(*) FROM orders WHERE status = '未請求'").fetchone()[0] or 0
        low_stock_count = conn.execute("SELECT COUNT(*) FROM products WHERE stock < 5").fetchone()[0] or 0
        
        # 最近の受注
        recent_orders = conn.execute("""
            SELECT o.id, strftime('%Y-%m-%d %H:%M:%S', o.order_date) as order_date, 
                   p.name as product_name, o.quantity, o.total_price, o.status 
            FROM orders o JOIN products p ON o.product_id = p.id 
            ORDER BY o.order_date DESC LIMIT 5
        """).fetchall()

        # 月別売上データ (Chart.js用) - 直近6ヶ月
        monthly_data = conn.execute("""
            SELECT strftime('%Y-%m', order_date) as month, SUM(total_price) as sales
            FROM orders WHERE status != 'キャンセル'
            GROUP BY month ORDER BY month DESC LIMIT 6
        """).fetchall()
        
        # グラフ用にデータを整形 (逆順にして時系列に)
        chart_labels = [row["month"] for row in reversed(monthly_data)]
        chart_values = [row["sales"] for row in reversed(monthly_data)]

    return render_template("dashboard.html", 
                           total_sales=total_sales, today_sales=today_sales,
                           uninvoiced_count=uninvoiced_count, low_stock_count=low_stock_count,
                           recent_orders=recent_orders,
                           chart_labels=chart_labels, chart_values=chart_values)

# 2. 商品管理 (一覧・検索)
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

# 商品編集
@app.route("/products/edit/<int:id>", methods=["GET", "POST"])
def edit_product(id):
    conn = get_db()
    if request.method == "POST":
        name = request.form["name"]
        price = int(request.form["price"])
        stock = int(request.form["stock"])
        conn.execute("UPDATE products SET name=?, price=?, stock=? WHERE id=?", (name, price, stock, id))
        conn.commit()
        flash("商品情報を更新しました")
        return redirect(url_for("products"))
    
    product = conn.execute("SELECT * FROM products WHERE id=?", (id,)).fetchone()
    return render_template("edit_product.html", product=product)

# 商品削除
@app.route("/products/delete/<int:id>", methods=["POST"])
def delete_product(id):
    with get_db() as conn:
        conn.execute("DELETE FROM products WHERE id=?", (id,))
        conn.commit()
    flash("商品を削除しました")
    return redirect(url_for("products"))

# 3. 受注管理 (一覧・検索・フィルタ)
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

# 4. 受注登録
@app.route("/orders/add", methods=["GET", "POST"])
def add_order():
    conn = get_db()
    if request.method == "POST":
        product_id = int(request.form["product_id"])
        quantity = int(request.form["quantity"])
        product = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        
        if product and product["stock"] >= quantity:
            total_price = product["price"] * quantity
            conn.execute("INSERT INTO orders (product_id, quantity, total_price, status, order_date) VALUES (?, ?, ?, ?, ?)",
                         (product_id, quantity, total_price, "未請求", datetime.now()))
            conn.execute("UPDATE products SET stock = stock - ? WHERE id = ?", (quantity, product_id))
            conn.commit()
            flash("受注を登録し、在庫を更新しました")
            return redirect(url_for("orders"))
        else:
            flash("在庫不足のため登録できません")
            
    products_list = conn.execute("SELECT * FROM products").fetchall()
    return render_template("add_order.html", products=products_list)

# ステータス更新
@app.route("/orders/update_status/<int:order_id>", methods=["POST"])
def update_status(order_id):
    new_status = request.form["status"]
    with get_db() as conn:
        conn.execute("UPDATE orders SET status = ? WHERE id = ?", (new_status, order_id))
        conn.commit()
    flash(f"ステータスを {new_status} に更新しました")
    return redirect(request.referrer or url_for("orders"))

# 5. CSVエクスポート
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
        for row in rows:
            cw.writerow(list(row))
            
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
        for row in rows:
            cw.writerow(list(row))
    output = make_response(si.getvalue().encode('utf-8-sig'))
    output.headers["Content-Disposition"] = "attachment; filename=recent_orders.csv"
    output.headers["Content-type"] = "text/csv"
    return output

if __name__ == "__main__":
    app.run(debug=True)
