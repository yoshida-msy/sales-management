import sqlite3
import io
import csv
import random
import os
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, make_response
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
# 秘密鍵は環境変数から取得することを推奨しますが、一旦既存のものを維持
app.secret_key = os.environ.get("SECRET_KEY", "sales-pro-secret-key-2026")

# --- ログイン管理 (Flask-Login) ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

# --- データベース設定 (Render対応: 絶対パスを使用) ---
# アプリの実行ファイルのディレクトリを取得し、そこに database.db を作成するようにします
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, "database.db")

def get_db():
    """データベース接続を作成します"""
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

# ユーザーモデル
class User(UserMixin):
    def __init__(self, id, username):
        self.id = id
        self.username = username

@login_manager.user_loader
def load_user(user_id):
    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if user:
            return User(user["id"], user["username"])
    return None

# --- データベース初期化 & デモデータ投入 ---
def init_db():
    """
    テーブル作成と初期デモデータの投入を行います。
    すでにテーブルがある場合は何もしません。
    """
    with get_db() as conn:
        # ユーザーテーブル
        conn.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT)")
        # 商品テーブル
        conn.execute("CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, price INTEGER NOT NULL, stock INTEGER NOT NULL)")
        # 受注テーブル
        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL, total_price INTEGER NOT NULL,
                status TEXT NOT NULL, order_date DATETIME NOT NULL,
                FOREIGN KEY (product_id) REFERENCES products (id)
            )
        """)
        
        # テスト用管理者アカウント作成 (admin / password)
        if not conn.execute("SELECT * FROM users WHERE username = 'admin'").fetchone():
            hashed_pw = generate_password_hash("password")
            conn.execute("INSERT INTO users (username, password) VALUES (?, ?)", ("admin", hashed_pw))
        
        # デモ商品・受注の投入 (空の場合のみ)
        if conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0:
            sample_prods = [
                ("高性能ノートPC", 120000, 15), 
                ("27インチモニター", 32000, 10), 
                ("ワイヤレスマウス", 3500, 50), 
                ("メカニカルキーボード", 12000, 20),
                ("USB-C ハブ", 5800, 30),
                ("Webカメラ Pro", 8500, 15),
                ("エルゴノミクスチェア", 45000, 5),
                ("デスクライト LED", 4200, 25),
                ("Bluetoothヘッドセット", 7200, 40),
                ("ポータブルSSD 1TB", 15000, 12)
            ]
            conn.executemany("INSERT INTO products (name, price, stock) VALUES (?, ?, ?)", sample_prods)
            
            p_ids = [r["id"] for r in conn.execute("SELECT id FROM products").fetchall()]
            for _ in range(30):
                d = datetime.now() - timedelta(days=random.randint(0, 60))
                conn.execute("INSERT INTO orders (product_id, quantity, total_price, status, order_date) VALUES (?, ?, ?, ?, ?)",
                             (random.choice(p_ids), random.randint(1, 3), random.randint(5000, 150000), 
                              random.choice(["未請求", "請求済", "入金済"]), d))
        conn.commit()

# アプリ起動時に一度だけDBを初期化するようにします
# Gunicornのプリロード機能などに対応するため、ここで呼び出します
init_db()

# --- 認証ルート ---

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        with get_db() as conn:
            user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            if user and check_password_hash(user["password"], password):
                login_user(User(user["id"], user["username"]))
                return redirect(url_for("index"))
        flash("ユーザー名またはパスワードが正しくありません")
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

# --- メイン機能 ---

@app.route("/")
@login_required
def index():
    """売上分析ダッシュボード"""
    with get_db() as conn:
        # KPIデータ
        total_sales = conn.execute("SELECT SUM(total_price) FROM orders WHERE status != 'キャンセル'").fetchone()[0] or 0
        today_sales = conn.execute("SELECT SUM(total_price) FROM orders WHERE date(order_date) = date('now') AND status != 'キャンセル'").fetchone()[0] or 0
        uninvoiced = conn.execute("SELECT COUNT(*) FROM orders WHERE status = '未請求'").fetchone()[0] or 0
        low_stock = conn.execute("SELECT COUNT(*) FROM products WHERE stock < 5").fetchone()[0] or 0
        
        # 月別売上グラフ (直近6ヶ月)
        monthly = conn.execute("""
            SELECT strftime('%Y-%m', order_date) as m, SUM(total_price) as s 
            FROM orders GROUP BY m ORDER BY m DESC LIMIT 6
        """).fetchall()
        m_labels = [r["m"] for r in reversed(monthly)]
        m_values = [r["s"] for r in reversed(monthly)]
        
        # 日別売上グラフ (直近7日間)
        daily = conn.execute("""
            SELECT date(order_date) as d, SUM(total_price) as s 
            FROM orders WHERE order_date >= date('now', '-7 days')
            GROUP BY d ORDER BY d ASC
        """).fetchall()
        d_labels = [r["d"] for r in daily]
        d_values = [r["s"] for r in daily]
        
        # ステータス別件数
        status_data = conn.execute("SELECT status, COUNT(*) as c FROM orders GROUP BY status").fetchall()
        s_labels = [r["status"] for r in status_data]
        s_values = [r["c"] for r in status_data]

        recent = conn.execute("""
            SELECT o.*, p.name FROM orders o JOIN products p ON o.product_id = p.id 
            ORDER BY o.order_date DESC LIMIT 5
        """).fetchall()

    return render_template("dashboard.html", total_sales=total_sales, today_sales=today_sales, 
                           uninvoiced=uninvoiced, low_stock=low_stock,
                           m_labels=m_labels, m_values=m_values,
                           d_labels=d_labels, d_values=d_values,
                           s_labels=s_labels, s_values=s_values, recent=recent)

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
    items = conn.execute("SELECT * FROM products WHERE name LIKE ?", (f"%{q}%",)).fetchall()
    return render_template("products.html", products=items, q=q)

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
    query = "SELECT o.*, p.name FROM orders o JOIN products p ON o.product_id = p.id WHERE p.name LIKE ?"
    params = [f"%{q}%"]
    if status:
        query += " AND o.status = ?"
        params.append(status)
    query += " ORDER BY o.order_date DESC"
    with get_db() as conn:
        items = conn.execute(query, params).fetchall()
    return render_template("orders.html", orders=items, q=q, status_filter=status)

@app.route("/orders/update/<int:id>", methods=["POST"])
@login_required
def update_status(id):
    """受注ステータスを更新する"""
    new_status = request.form["status"]
    with get_db() as conn:
        conn.execute("UPDATE orders SET status = ? WHERE id = ?", (new_status, id))
        conn.commit()
    flash("受注ステータスを更新しました")
    return redirect(url_for("orders"))

@app.route("/orders/add", methods=["GET", "POST"])
@login_required
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

@app.route("/export/csv")
@login_required
def export_csv():
    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(["ID", "日付", "商品名", "数量", "合計金額", "状態"])
    with get_db() as conn:
        rows = conn.execute("SELECT o.id, o.order_date, p.name, o.quantity, o.total_price, o.status FROM orders o JOIN products p ON o.product_id = p.id").fetchall()
        for r in rows: cw.writerow(list(r))
    resp = make_response(si.getvalue().encode("utf-8-sig"))
    resp.headers["Content-Disposition"] = "attachment; filename=orders_export.csv"
    resp.headers["Content-type"] = "text/csv"
    return resp

if __name__ == "__main__":
    # ローカル実行時は環境変数 PORT がなければ 5000 で起動
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
