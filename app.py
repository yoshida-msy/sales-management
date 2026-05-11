import sqlite3
import io
import csv
import random
import os
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, make_response, send_file
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

# PDF生成用ライブラリ
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "sales-pro-secret-key-2026")

# --- ログイン管理 ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

# --- データベース設定 ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, "database.db")

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

# ユーザーモデル (ロール権限を追加)
class User(UserMixin):
    def __init__(self, id, username, role):
        self.id = id
        self.username = username
        self.role = role # 'admin' or 'staff'

@login_manager.user_loader
def load_user(user_id):
    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if user: return User(user["id"], user["username"], user["role"])
    return None

# --- DB初期化 (スキーマ拡張) ---
def init_db():
    with get_db() as conn:
        # ユーザー: roleカラム追加
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT, 
                username TEXT UNIQUE, 
                password TEXT,
                role TEXT DEFAULT 'staff'
            )
        """)
        # 顧客テーブル新規追加
        conn.execute("""
            CREATE TABLE IF NOT EXISTS customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_name TEXT NOT NULL,
                contact_name TEXT,
                email TEXT,
                phone TEXT,
                address TEXT
            )
        """)
        # 商品テーブル
        conn.execute("CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, price INTEGER NOT NULL, stock INTEGER NOT NULL)")
        
        # 受注テーブル: customer_id, created_by, updated_at 追加
        # 注意: 既存テーブルがある場合は ALTER TABLE が必要ですが、ここでは簡略化のため新規作成ベースで記述
        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT, 
                product_id INTEGER NOT NULL,
                customer_id INTEGER,
                quantity INTEGER NOT NULL, 
                total_price INTEGER NOT NULL,
                status TEXT NOT NULL, 
                order_date DATETIME NOT NULL,
                created_by TEXT,
                updated_at DATETIME,
                FOREIGN KEY (product_id) REFERENCES products (id),
                FOREIGN KEY (customer_id) REFERENCES customers (id)
            )
        """)
        
        # 初期データの投入
        if not conn.execute("SELECT * FROM users WHERE username = 'admin'").fetchone():
            conn.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)", 
                         ("admin", generate_password_hash("password"), "admin"))
        if not conn.execute("SELECT * FROM users WHERE username = 'staff'").fetchone():
            conn.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)", 
                         ("staff", generate_password_hash("password"), "staff"))
        
        # デモ顧客の投入
        if conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 0:
            sample_custs = [
                ("株式会社テクノ未来", "田中 太郎", "tanaka@example.com", "03-1234-5678", "東京都千代田区1-1"),
                ("グローバル商事", "佐藤 次郎", "sato@example.com", "06-9876-5432", "大阪府大阪市北区2-2"),
                ("スマートシステム", "鈴木 一郎", "suzuki@example.com", "052-111-2222", "愛知県名古屋市中区3-3")
            ]
            conn.executemany("INSERT INTO customers (company_name, contact_name, email, phone, address) VALUES (?, ?, ?, ?, ?)", sample_custs)
            
        conn.commit()

init_db()

# --- 認証ルート ---

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username, password = request.form["username"], request.form["password"]
        with get_db() as conn:
            user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            if user and check_password_hash(user["password"], password):
                login_user(User(user["id"], user["username"], user["role"]))
                return redirect(url_for("index"))
        flash("ログインに失敗しました")
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

# --- 顧客管理 ---

@app.route("/customers", methods=["GET", "POST"])
@login_required
def customers():
    conn = get_db()
    if request.method == "POST":
        conn.execute("""
            INSERT INTO customers (company_name, contact_name, email, phone, address) 
            VALUES (?, ?, ?, ?, ?)""", 
            (request.form["company_name"], request.form["contact_name"], 
             request.form["email"], request.form["phone"], request.form["address"]))
        conn.commit()
        flash("顧客を登録しました")
        return redirect(url_for("customers"))
    
    q = request.args.get("q", "")
    items = conn.execute("SELECT * FROM customers WHERE company_name LIKE ?", (f"%{q}%",)).fetchall()
    return render_template("customers.html", customers=items, q=q)

@app.route("/customers/delete/<int:id>", methods=["POST"])
@login_required
def delete_customer(id):
    if current_user.role != 'admin':
        flash("管理者権限が必要です")
        return redirect(url_for("customers"))
    with get_db() as conn:
        conn.execute("DELETE FROM customers WHERE id = ?", (id,))
        conn.commit()
    flash("顧客を削除しました")
    return redirect(url_for("customers"))

# --- 受注管理 (PDF生成追加) ---

@app.route("/orders/pdf/<int:id>")
@login_required
def generate_pdf(id):
    """
    請求書PDFを生成してダウンロードさせます
    """
    with get_db() as conn:
        order = conn.execute("""
            SELECT o.*, p.name as product_name, p.price, c.company_name, c.address 
            FROM orders o 
            JOIN products p ON o.product_id = p.id 
            LEFT JOIN customers c ON o.customer_id = c.id
            WHERE o.id = ?""", (id,)).fetchone()
    
    if not order: return "Order not found", 404

    # メモリ上にPDFを作成
    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)
    
    # フォント設定 (Render等の環境で日本語フォントがない場合は標準フォントにフォールバック)
    # 実務では日本語TTFファイルをプロジェクトに含めるのが一般的です
    p.setFont("Helvetica", 16)
    p.drawString(100, 800, f"INVOICE (Order ID: {order['id']})")
    
    p.setFont("Helvetica", 12)
    p.drawString(100, 750, f"Customer: {order['company_name'] or 'N/A'}")
    p.drawString(100, 730, f"Date: {order['order_date']}")
    
    p.line(100, 710, 500, 710)
    
    p.drawString(100, 680, f"Product: {order['product_name']}")
    p.drawString(100, 660, f"Quantity: {order['quantity']}")
    p.drawString(100, 640, f"Total Price: {order['total_price']:,} JPY")
    
    p.showPage()
    p.save()
    
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name=f"invoice_{id}.pdf", mimetype='application/pdf')

# --- メイン機能 (既存機能の拡張) ---

@app.route("/")
@login_required
def index():
    with get_db() as conn:
        # KPIデータ
        total_sales = conn.execute("SELECT SUM(total_price) FROM orders WHERE status != 'キャンセル'").fetchone()[0] or 0
        today_sales = conn.execute("SELECT SUM(total_price) FROM orders WHERE date(order_date) = date('now') AND status != 'キャンセル'").fetchone()[0] or 0
        uninvoiced = conn.execute("SELECT COUNT(*) FROM orders WHERE status = '未請求'").fetchone()[0] or 0
        low_stock = conn.execute("SELECT COUNT(*) FROM products WHERE stock < 5").fetchone()[0] or 0
        
        # グラフデータ取得 (以前の実装を継承)
        monthly = conn.execute("SELECT strftime('%Y-%m', order_date) as m, SUM(total_price) as s FROM orders GROUP BY m ORDER BY m DESC LIMIT 6").fetchall()
        daily = conn.execute("SELECT date(order_date) as d, SUM(total_price) as s FROM orders WHERE order_date >= date('now', '-7 days') GROUP BY d ORDER BY d ASC").fetchall()
        status_data = conn.execute("SELECT status, COUNT(*) as c FROM orders GROUP BY status").fetchall()
        ranking = conn.execute("""
            SELECT p.name, SUM(o.total_price) as total, SUM(o.quantity) as qty
            FROM orders o JOIN products p ON o.product_id = p.id
            WHERE o.status != 'キャンセル'
            GROUP BY p.id ORDER BY total DESC LIMIT 5
        """).fetchall()

    return render_template("dashboard.html", 
                           total_sales=total_sales, today_sales=today_sales, 
                           uninvoiced=uninvoiced, low_stock=low_stock,
                           m_labels=[r["m"] for r in reversed(monthly)], m_values=[r["s"] for r in reversed(monthly)],
                           d_labels=[r["d"] for r in daily], d_values=[r["s"] for r in daily],
                           s_labels=[r["status"] for r in status_data], s_values=[r["c"] for r in status_data],
                           ranking=ranking)

@app.route("/products", methods=["GET", "POST"])
@login_required
def products():
    conn = get_db()
    if request.method == "POST":
        conn.execute("INSERT INTO products (name, price, stock) VALUES (?, ?, ?)", 
                     (request.form["name"], request.form["price"], request.form["stock"]))
        conn.commit()
        flash("商品を登録しました")
        return redirect(url_for("products"))
    q = request.args.get("q", "")
    items = conn.execute("SELECT * FROM products WHERE name LIKE ? ORDER BY stock ASC", (f"%{q}%",)).fetchall()
    return render_template("products.html", products=items, q=q)

@app.route("/products/edit/<int:id>", methods=["GET", "POST"])
@login_required
def edit_product(id):
    """商品情報を編集する"""
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
@login_required
def delete_product(id):
    if current_user.role != 'admin':
        flash("管理者権限が必要です")
        return redirect(url_for("products"))
    with get_db() as conn:
        conn.execute("DELETE FROM products WHERE id = ?", (id,))
        conn.commit()
    flash("商品を削除しました")
    return redirect(url_for("products"))

@app.route("/orders")
@login_required
def orders():
    q = request.args.get("q", "")
    status = request.args.get("status", "")
    query = """
        SELECT o.*, p.name, c.company_name 
        FROM orders o 
        JOIN products p ON o.product_id = p.id 
        LEFT JOIN customers c ON o.customer_id = c.id
        WHERE p.name LIKE ?"""
    params = [f"%{q}%"]
    if status:
        query += " AND o.status = ?"
        params.append(status)
    query += " ORDER BY o.order_date DESC"
    with get_db() as conn:
        items = conn.execute(query, params).fetchall()
    return render_template("orders.html", orders=items, q=q, status_filter=status)

@app.route("/orders/add", methods=["GET", "POST"])
@login_required
def add_order():
    conn = get_db()
    if request.method == "POST":
        p_id = request.form["product_id"]
        c_id = request.form["customer_id"]
        qty = int(request.form["quantity"])
        p = conn.execute("SELECT * FROM products WHERE id = ?", (p_id,)).fetchone()
        if p and p["stock"] >= qty:
            conn.execute("""
                INSERT INTO orders (product_id, customer_id, quantity, total_price, status, order_date, created_by) 
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (p_id, c_id, qty, p["price"] * qty, "未請求", datetime.now(), current_user.username))
            conn.execute("UPDATE products SET stock = stock - ? WHERE id = ?", (qty, p_id))
            conn.commit()
            flash("受注を登録しました")
            return redirect(url_for("orders"))
        flash("在庫が不足しています")
    
    prods = conn.execute("SELECT * FROM products").fetchall()
    custs = conn.execute("SELECT * FROM customers").fetchall()
    return render_template("add_order.html", products=prods, customers=custs)

@app.route("/orders/update/<int:id>", methods=["POST"])
@login_required
def update_status(id):
    new_status = request.form["status"]
    with get_db() as conn:
        conn.execute("UPDATE orders SET status = ?, updated_at = ? WHERE id = ?", (new_status, datetime.now(), id))
        conn.commit()
    flash("ステータスを更新しました")
    return redirect(url_for("orders"))

@app.route("/export/csv")
@login_required
def export_csv():
    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(["ID", "日付", "顧客名", "商品名", "数量", "合計金額", "状態"])
    with get_db() as conn:
        rows = conn.execute("""
            SELECT o.id, o.order_date, c.company_name, p.name, o.quantity, o.total_price, o.status 
            FROM orders o 
            JOIN products p ON o.product_id = p.id 
            LEFT JOIN customers c ON o.customer_id = c.id
        """).fetchall()
        for r in rows: cw.writerow(list(r))
    resp = make_response(si.getvalue().encode("utf-8-sig"))
    resp.headers["Content-Disposition"] = "attachment; filename=orders.csv"
    resp.headers["Content-type"] = "text/csv"
    return resp

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
