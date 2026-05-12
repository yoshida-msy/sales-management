import sqlite3
import io
import csv
import random
import os
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, make_response, send_file
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

# PDF生成用
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4

app = Flask(__name__, instance_relative_config=True)
app.secret_key = os.environ.get("SECRET_KEY", "sales-pro-secret-key-2026")

# =================================================================
# なぜデプロイするとデータが消えるのか？ (Render Freeの仕様)
# =================================================================
# Renderの無料プランでは「エフェメラル・ファイルシステム（一時的なディスク）」
# という仕組みが採用されています。これは、
# 1. デプロイ（コード更新）した時
# 2. アプリが再起動した時（無料プランは一定時間使わないと止まります）
# に、サーバーの中にある「コード以外のファイル（作成したdatabase.dbなど）」
# が全て消去され、初期状態（Gitの中身だけ）に戻されてしまうからです。
# 
# 根本解決には：
# - 有料の「Persistent Disk」をアタッチする
# - Render提供の「Managed PostgreSQL（DBサービス）」に移行する
# のいずれかが必要ですが、まずはプログラム側で「無駄なリセット」を防ぐ修正をします。
# =================================================================

# --- データベース設定 ---
# データベースファイルを 'instance' フォルダ内に保存するようにします
# (Flaskの推奨構成: コードとデータを分離するため)
os.makedirs(app.instance_path, exist_ok=True)
DATABASE = os.path.join(app.instance_path, "database.db")

def get_db():
    """データベース接続を取得します"""
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

# --- ログイン管理 ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

class User(UserMixin):
    def __init__(self, id, username, role):
        self.id = id
        self.username = username
        self.role = role

@login_manager.user_loader
def load_user(user_id):
    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if user: return User(user["id"], user["username"], user["role"])
    return None

# --- データベース初期化ロジック (改善版) ---

def init_db():
    """
    テーブル作成のみを行います。
    'IF NOT EXISTS' を使うことで、すでにテーブルがある場合は何もしません。
    """
    with get_db() as conn:
        # ユーザー
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT, 
                username TEXT UNIQUE, 
                password TEXT,
                role TEXT DEFAULT 'staff'
            )
        """)
        # 顧客
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
        # 商品
        conn.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT, 
                name TEXT NOT NULL, 
                price INTEGER NOT NULL, 
                stock INTEGER NOT NULL
            )
        """)
        # 受注
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
        conn.commit()

def seed_db():
    """
    初期データの投入を行います。
    すでにデータがある場合はスキップし、既存データを守ります。
    """
    with get_db() as conn:
        # 管理者がいない場合のみ作成
        if not conn.execute("SELECT * FROM users WHERE username = 'admin'").fetchone():
            conn.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)", 
                         ("admin", generate_password_hash("password"), "admin"))
            print("Default admin created.")

        # デモデータは「開発環境」かつ「商品が一件もない」場合のみ投入
        # 本番環境(Render)でデータが消えないようにガードをかけます
        if app.debug and conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0:
            sample_prods = [("高性能ノートPC", 120000, 15), ("27インチモニター", 32000, 10)]
            conn.executemany("INSERT INTO products (name, price, stock) VALUES (?, ?, ?)", sample_prods)
            print("Seed data inserted in debug mode.")
            
        conn.commit()

# アプリ起動時に「安全に」初期化を実行
with app.app_context():
    init_db()
    seed_db()

# --- Flask CLI コマンド (手動リセット用) ---
@app.cli.command("init-db")
def init_db_command():
    """データベースを初期化（または修復）します。既存データは消しません。"""
    init_db()
    seed_db()
    print("Database initialized safely.")

# --- 以下、既存のルート定義 (変更なし) ---

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

@app.route("/")
@login_required
def index():
    with get_db() as conn:
        total_sales = conn.execute("SELECT SUM(total_price) FROM orders WHERE status != 'キャンセル'").fetchone()[0] or 0
        today_sales = conn.execute("SELECT SUM(total_price) FROM orders WHERE date(order_date) = date('now') AND status != 'キャンセル'").fetchone()[0] or 0
        uninvoiced = conn.execute("SELECT COUNT(*) FROM orders WHERE status = '未請求'").fetchone()[0] or 0
        low_stock = conn.execute("SELECT COUNT(*) FROM products WHERE stock < 5").fetchone()[0] or 0
        
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

@app.route("/customers", methods=["GET", "POST"])
@login_required
def customers():
    conn = get_db()
    if request.method == "POST":
        conn.execute("INSERT INTO customers (company_name, contact_name, email, phone, address) VALUES (?, ?, ?, ?, ?)", 
            (request.form["company_name"], request.form["contact_name"], request.form["email"], request.form["phone"], request.form["address"]))
        conn.commit()
        flash("顧客を登録しました")
        return redirect(url_for("customers"))
    q = request.args.get("q", "")
    items = conn.execute("SELECT * FROM customers WHERE company_name LIKE ?", (f"%{q}%",)).fetchall()
    return render_template("customers.html", customers=items, q=q)

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
    low_stock = request.args.get("low_stock", "")
    min_price = request.args.get("min_price", "")
    max_price = request.args.get("max_price", "")

    query = "SELECT * FROM products WHERE 1=1"
    params = []

    if q:
        query += " AND name LIKE ?"
        params.append(f"%{q}%")
    if low_stock:
        query += " AND stock < 5"
    if min_price:
        query += " AND price >= ?"
        params.append(min_price)
    if max_price:
        query += " AND price <= ?"
        params.append(max_price)

    query += " ORDER BY stock ASC"
    items = conn.execute(query, params).fetchall()
    return render_template("products.html", products=items, q=q, low_stock=low_stock, min_price=min_price, max_price=max_price)

@app.route("/products/edit/<int:id>", methods=["GET", "POST"])
@login_required
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
    customer_q = request.args.get("customer_q", "")
    status = request.args.get("status", "")
    date_from = request.args.get("date_from", "")
    date_to = request.args.get("date_to", "")
    uninvoiced = request.args.get("uninvoiced", "")

    query = """
        SELECT o.*, p.name, c.company_name 
        FROM orders o 
        JOIN products p ON o.product_id = p.id 
        LEFT JOIN customers c ON o.customer_id = c.id
        WHERE 1=1"""
    params = []

    if q:
        query += " AND p.name LIKE ?"
        params.append(f"%{q}%")
    if customer_q:
        query += " AND c.company_name LIKE ?"
        params.append(f"%{customer_q}%")
    if status:
        query += " AND o.status = ?"
        params.append(status)
    if date_from:
        query += " AND date(o.order_date) >= ?"
        params.append(date_from)
    if date_to:
        query += " AND date(o.order_date) <= ?"
        params.append(date_to)
    if uninvoiced:
        query += " AND o.status = '未請求'"

    query += " ORDER BY o.order_date DESC"
    
    with get_db() as conn:
        items = conn.execute(query, params).fetchall()
        
    return render_template("orders.html", orders=items, 
                           q=q, customer_q=customer_q, status_filter=status,
                           date_from=date_from, date_to=date_to, uninvoiced=uninvoiced)

@app.route("/orders/add", methods=["GET", "POST"])
@login_required
def add_order():
    conn = get_db()
    if request.method == "POST":
        p_id, c_id, qty = request.form["product_id"], request.form["customer_id"], int(request.form["quantity"])
        p = conn.execute("SELECT * FROM products WHERE id = ?", (p_id,)).fetchone()
        if p and p["stock"] >= qty:
            conn.execute("INSERT INTO orders (product_id, customer_id, quantity, total_price, status, order_date, created_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (p_id, c_id, qty, p["price"] * qty, "未請求", datetime.now(), current_user.username))
            conn.execute("UPDATE products SET stock = stock - ? WHERE id = ?", (qty, p_id))
            conn.commit()
            flash("受注を登録しました")
            return redirect(url_for("orders"))
        flash("在庫が不足しています")
    prods, custs = conn.execute("SELECT * FROM products").fetchall(), conn.execute("SELECT * FROM customers").fetchall()
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

@app.route("/orders/pdf/<int:id>")
@login_required
def generate_pdf(id):
    with get_db() as conn:
        order = conn.execute("SELECT o.*, p.name as product_name, p.price, c.company_name FROM orders o JOIN products p ON o.product_id = p.id LEFT JOIN customers c ON o.customer_id = c.id WHERE o.id = ?", (id,)).fetchone()
    if not order: return "Order not found", 404
    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)
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

@app.route("/export/csv")
@login_required
def export_csv():
    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(["ID", "日付", "顧客名", "商品名", "数量", "合計金額", "状態"])
    with get_db() as conn:
        rows = conn.execute("SELECT o.id, o.order_date, c.company_name, p.name, o.quantity, o.total_price, o.status FROM orders o JOIN products p ON o.product_id = p.id LEFT JOIN customers c ON o.customer_id = c.id").fetchall()
        for r in rows: cw.writerow(list(r))
    resp = make_response(si.getvalue().encode("utf-8-sig"))
    resp.headers["Content-Disposition"] = "attachment; filename=orders.csv"
    resp.headers["Content-type"] = "text/csv"
    return resp

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
