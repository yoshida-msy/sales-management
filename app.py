import sqlite3
import io
import csv
import random
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, make_response

app = Flask(__name__)
app.secret_key = "sales_pro_simple_key"

DATABASE = "database.db"

# データベース接続
def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

# データベース初期化
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

# アプリ起動時に初期化
init_db()

# --- メイン機能 ---

@app.route("/")
def index():
    with get_db() as conn:
        # KPIデータ
        total_sales = conn.execute("SELECT SUM(total_price) FROM orders WHERE status != 'キャンセル'").fetchone()[0] or 0
        today_sales = conn.execute("SELECT SUM(total_price) FROM orders WHERE date(order_date) = date('now') AND status != 'キャンセル'").fetchone()[0] or 0
        uninvoiced = conn.execute("SELECT COUNT(*) FROM orders WHERE status = '未請求'").fetchone()[0] or 0
        low_stock = conn.execute("SELECT COUNT(*) FROM products WHERE stock < 5").fetchone()[0] or 0
        
        # グラフ用：月別売上推移（直近6ヶ月）
        monthly = conn.execute("""
            SELECT strftime('%Y-%m', order_date) as m, SUM(total_price) as s 
            FROM orders WHERE status != 'キャンセル' 
            GROUP BY m ORDER BY m DESC LIMIT 6
        """).fetchall()
        m_labels = [r["m"] for r in reversed(monthly)]
        m_values = [r["s"] for r in reversed(monthly)]
        
        # グラフ用：受注ステータス分布
        status_data = conn.execute("SELECT status, COUNT(*) as c FROM orders GROUP BY status").fetchall()
        s_labels = [r["status"] for r in status_data]
        s_values = [r["c"] for r in status_data]

        # 最近の受注5件（p.name を name として取得）
        recent = conn.execute("""
            SELECT o.*, p.name as name 
            FROM orders o 
            JOIN products p ON o.product_id = p.id 
            ORDER BY o.order_date DESC LIMIT 5
        """).fetchall()

    return render_template("dashboard.html", total_sales=total_sales, today_sales=today_sales, 
                           uninvoiced=uninvoiced, low_stock=low_stock,
                           m_labels=m_labels, m_values=m_values,
                           s_labels=s_labels, s_values=s_values, recent=recent)

@app.route("/products", methods=["GET", "POST"])
def products():
    conn = get_db()
    if request.method == "POST":
        conn.execute("INSERT INTO products (name, price, stock) VALUES (?, ?, ?)", 
                     (request.form["name"], request.form["price"], request.form["stock"]))
        conn.commit()
        flash("商品を登録しました")
        return redirect(url_for("products"))
    
    q = request.args.get("q", "")
    items = conn.execute("SELECT * FROM products WHERE name LIKE ?", (f"%{q}%",)).fetchall()
    return render_template("products.html", products=items, q=q)

@app.route("/products/edit/<int:id>", methods=["GET", "POST"])
def edit_product(id):
    conn = get_db()
    if request.method == "POST":
        conn.execute("UPDATE products SET name=?, price=?, stock=? WHERE id=?",
                     (request.form["name"], request.form["price"], request.form["stock"], id))
        conn.commit()
        flash("商品情報を更新しました")
        return redirect(url_for("products"))
    item = conn.execute("SELECT * FROM products WHERE id = ?", (id,)).fetchone()
    return render_template("edit_product.html", product=item)

@app.route("/products/delete/<int:id>", methods=["POST"])
def delete_product(id):
    with get_db() as conn:
        conn.execute("DELETE FROM products WHERE id = ?", (id,))
        conn.commit()
    flash("商品を削除しました")
    return redirect(url_for("products"))

@app.route("/orders")
def orders():
    q = request.args.get("q", "")
    status = request.args.get("status", "")
    # p.name を name として取得するように統一
    query = "SELECT o.*, p.name as name FROM orders o JOIN products p ON o.product_id = p.id WHERE p.name LIKE ?"
    params = [f"%{q}%"]
    if status:
        query += " AND o.status = ?"
        params.append(status)
    query += " ORDER BY o.order_date DESC"
    with get_db() as conn:
        items = conn.execute(query, params).fetchall()
    return render_template("orders.html", orders=items, q=q, status_filter=status)

@app.route("/orders/add", methods=["GET", "POST"])
def add_order():
    conn = get_db()
    if request.method == "POST":
        p_id = request.form["product_id"]
        qty = int(request.form["quantity"])
        p = conn.execute("SELECT * FROM products WHERE id = ?", (p_id,)).fetchone()
        if p and p["stock"] >= qty:
            total = p["price"] * qty
            conn.execute("INSERT INTO orders (product_id, quantity, total_price, status, order_date) VALUES (?, ?, ?, ?, ?)",
                         (p_id, qty, total, "未請求", datetime.now()))
            conn.execute("UPDATE products SET stock = stock - ? WHERE id = ?", (qty, p_id))
            conn.commit()
            flash("受注を登録しました")
            return redirect(url_for("orders"))
        flash("在庫が不足しています")
    prods = conn.execute("SELECT * FROM products").fetchall()
    return render_template("add_order.html", products=prods)

@app.route("/orders/update_status/<int:id>", methods=["POST"])
def update_status(id):
    with get_db() as conn:
        conn.execute("UPDATE orders SET status = ? WHERE id = ?", (request.form["status"], id))
        conn.commit()
    flash("ステータスを更新しました")
    return redirect(request.referrer or url_for("orders"))

@app.route("/export/csv")
def export_csv():
    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(["ID", "日付", "商品名", "数量", "合計金額", "状態"])
    with get_db() as conn:
        rows = conn.execute("""
            SELECT o.id, o.order_date, p.name, o.quantity, o.total_price, o.status 
            FROM orders o 
            JOIN products p ON o.product_id = p.id
        """).fetchall()
        for r in rows: cw.writerow(list(r))
    resp = make_response(si.getvalue().encode("utf-8-sig"))
    resp.headers["Content-Disposition"] = "attachment; filename=orders.csv"
    resp.headers["Content-type"] = "text/csv"
    return resp

if __name__ == "__main__":
    app.run(debug=True)
