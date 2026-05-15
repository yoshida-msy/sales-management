import io
import csv
import os
import logging
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, make_response, send_file
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from dotenv import load_dotenv

# PDF生成用
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4

# ローカル開発用 .env 読み込み
load_dotenv()

app = Flask(__name__, instance_relative_config=True)
app.secret_key = os.environ.get("SECRET_KEY", "sales-pro-secret-key-2026")

# ロギング設定 (RenderのLogsで見れるようになります)
logging.basicConfig(level=logging.INFO)
logger = app.logger

# --- データベース設定 ---
basedir = os.path.abspath(os.path.dirname(__file__))
instance_path = os.path.join(basedir, 'instance')
if not os.path.exists(instance_path):
    os.makedirs(instance_path)

default_db = f"sqlite:///{os.path.join(instance_path, 'database.db')}"
db_url = os.environ.get("DATABASE_URL", default_db)

# Render/Neonの postgres:// を postgresql:// に変換
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
migrate = Migrate(app, db)

# --- データベースモデル ---

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), default='staff')
    email = db.Column(db.String(120), unique=True)
    display_name = db.Column(db.String(100))
    avatar_url = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Product(db.Model):
    __tablename__ = 'products'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    price = db.Column(db.Integer, nullable=False)
    stock = db.Column(db.Integer, nullable=False)
    orders = db.relationship('Order', backref='product', lazy=True)

class Customer(db.Model):
    __tablename__ = 'customers'
    id = db.Column(db.Integer, primary_key=True)
    company_name = db.Column(db.String(150), nullable=False)
    contact_name = db.Column(db.String(100))
    email = db.Column(db.String(120))
    phone = db.Column(db.String(20))
    address = db.Column(db.String(200))
    orders = db.relationship('Order', backref='customer', lazy=True)

class Order(db.Model):
    __tablename__ = 'orders'
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id'))
    quantity = db.Column(db.Integer, nullable=False)
    total_price = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='未対応')
    order_date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_by = db.Column(db.String(80))
    updated_at = db.Column(db.DateTime, onupdate=datetime.utcnow)

# --- ログイン管理 ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# --- 初期データ投入 (Seed Data) ---

def seed_db():
    try:
        if not User.query.filter_by(username='admin').first():
            admin = User(
                username='admin', 
                password=generate_password_hash('password'), 
                role='admin',
                email='admin@example.com',
                display_name='System Admin'
            )
            db.session.add(admin)
        if not User.query.filter_by(username='staff').first():
            staff = User(
                username='staff', 
                password=generate_password_hash('password'), 
                role='staff',
                email='staff@example.com',
                display_name='Regular Staff'
            )
            db.session.add(staff)
        if Product.query.count() == 0:
            sample_prods = [
                Product(name="MacBook Pro 14", price=280000, stock=10),
                Product(name="Dell 27インチモニター", price=45000, stock=20),
                Product(name="HHKB キーボード", price=35000, stock=15),
                Product(name="Logicool MX Master 3S", price=15000, stock=50)
            ]
            db.session.add_all(sample_prods)
        if Customer.query.count() == 0:
            sample_custs = [
                Customer(company_name="株式会社テクノ未来", contact_name="田中 太郎", email="tanaka@example.com", phone="03-1111-2222", address="東京都千代田区"),
                Customer(company_name="グローバル商事株式会社", contact_name="佐藤 次郎", email="sato@example.com", phone="06-3333-4444", address="大阪府大阪市")
            ]
            db.session.add_all(sample_custs)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f"SEED ERROR: {str(e)}")

# --- データベース自動移行 (Migration) ---

def migrate_schema():
    """
    不足しているカラムを自動的に追加します (PostgreSQL/SQLite両対応)。
    Render Free環境など、Shellが使えない環境での自動更新用です。
    """
    try:
        from sqlalchemy import text
        is_postgres = db.engine.url.drivername.startswith("postgresql")
        
        # 現在のusersテーブルのカラム一覧を取得
        if is_postgres:
            # PostgreSQL: information_schema を検索
            check_sql = text("SELECT column_name FROM information_schema.columns WHERE table_name='users'")
        else:
            # SQLite: PRAGMA table_info を使用
            check_sql = text("PRAGMA table_info(users)")
            
        result = db.session.execute(check_sql).fetchall()
        existing_columns = [row[0] if is_postgres else row[1] for row in result]
        
        # 追加したいカラムのリスト (カラム名, 型)
        new_columns = [
            ("email", "VARCHAR(120)" if is_postgres else "TEXT"),
            ("display_name", "VARCHAR(100)" if is_postgres else "TEXT"),
            ("avatar_url", "VARCHAR(255)" if is_postgres else "TEXT"),
            ("created_at", "TIMESTAMP" if is_postgres else "DATETIME")
        ]
        
        for col_name, col_type in new_columns:
            if col_name not in existing_columns:
                logger.info(f"MIGRATION: Adding column {col_name} to users table.")
                alter_sql = text(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}")
                db.session.execute(alter_sql)
        
        db.session.commit()
        logger.info("MIGRATION: Schema update completed.")
    except Exception as e:
        db.session.rollback()
        logger.error(f"MIGRATION ERROR: {str(e)}")

# アプリ起動時に安全に実行
with app.app_context():
    db.create_all()  # 新規テーブルの作成
    migrate_schema() # 既存テーブルへのカラム追加
    seed_db()        # 初期データの投入

@app.cli.command("init-db")
def init_db_command():
    db.drop_all()
    db.create_all()
    seed_db()
    print("Database has been RESET successfully.")

# --- ルート定義 ---

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username, password = request.form["username"], request.form["password"]
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for("index"))
        flash("ログインに失敗しました")
    return render_template("login.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    """新規ユーザー登録"""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password")
        confirm_password = request.form.get("confirm_password")

        if not username or not password:
            flash("ユーザー名とパスワードを入力してください")
        elif password != confirm_password:
            flash("パスワードが一致しません")
        elif User.query.filter_by(username=username).first():
            flash("このユーザー名は既に使用されています")
        else:
            try:
                new_user = User(
                    username=username,
                    password=generate_password_hash(password),
                    role='staff' # デフォルトはスタッフ
                )
                db.session.add(new_user)
                db.session.commit()
                flash("アカウントを作成しました。ログインしてください。")
                return redirect(url_for("login"))
            except Exception as e:
                db.session.rollback()
                flash(f"エラーが発生しました: {str(e)}")
                
    return render_template("register.html")

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    """パスワード再設定 (簡易版)"""
    if request.method == "POST":
        identifier = request.form.get("identifier")
        # ユーザー名またはメールアドレスで検索
        user = User.query.filter((User.username == identifier) | (User.email == identifier)).first()
        
        # 実運用ではここでメール送信等を行いますが、今回はメッセージのみ
        flash(f"登録メールアドレスへリセット手順を送信しました（デモ）。")
        return redirect(url_for("login"))
    return render_template("forgot_password.html")

@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    """ユーザー設定ページ"""
    if request.method == "POST":
        try:
            current_user.display_name = request.form.get("display_name", "").strip()
            current_user.email = request.form.get("email", "").strip()
            current_user.avatar_url = request.form.get("avatar_url", "").strip()
            
            db.session.commit()
            flash("プロフィールを更新しました")
        except Exception as e:
            db.session.rollback()
            flash(f"更新エラー: {str(e)}")
        return redirect(url_for("settings"))
        
    return render_template("settings.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

@app.route("/")
@login_required
def index():
    """分析ダッシュボード (PostgreSQL/SQLite両対応版)"""
    try:
        total_sales = db.session.query(db.func.sum(Order.total_price)).filter(Order.status != 'キャンセル').scalar() or 0
        today = datetime.utcnow().date()
        today_sales = db.session.query(db.func.sum(Order.total_price)).filter(
            db.func.date(Order.order_date) == today,
            Order.status != 'キャンセル'
        ).scalar() or 0
        uninvoiced = Order.query.filter(Order.status.in_(['未対応', '未請求'])).count()
        low_stock = Product.query.filter(Product.stock < 5).count()
        
        is_postgres = db.engine.url.drivername.startswith("postgresql")
        if is_postgres:
            monthly_fmt = db.func.to_char(Order.order_date, 'YYYY-MM')
        else:
            monthly_fmt = db.func.strftime('%Y-%m', Order.order_date)

        monthly_data = db.session.query(monthly_fmt.label('m'), db.func.sum(Order.total_price)).group_by('m').order_by(db.desc('m')).limit(6).all()
        m_labels = [r[0] for r in reversed(monthly_data)]
        m_values = [r[1] for r in reversed(monthly_data)]

        seven_days_ago = datetime.utcnow() - timedelta(days=7)
        daily_data = db.session.query(db.func.date(Order.order_date).label('d'), db.func.sum(Order.total_price)).filter(Order.order_date >= seven_days_ago).group_by('d').order_by(db.asc('d')).all()
        d_labels = [str(r[0]) for r in daily_data]
        d_values = [r[1] for r in daily_data]

        status_data = db.session.query(Order.status, db.func.count(Order.id)).group_by(Order.status).all()
        s_labels = [r[0] for r in status_data]
        s_values = [r[1] for r in status_data]

        ranking = db.session.query(Product.name, db.func.sum(Order.total_price).label('total'), db.func.sum(Order.quantity).label('qty'))\
            .join(Order).filter(Order.status != 'キャンセル')\
            .group_by(Product.id, Product.name).order_by(db.desc('total')).limit(5).all()

        return render_template("dashboard.html", 
                               total_sales=total_sales, today_sales=today_sales, uninvoiced=uninvoiced, low_stock=low_stock,
                               m_labels=m_labels, m_values=m_values, d_labels=d_labels, d_values=d_values,
                               s_labels=s_labels, s_values=s_values, ranking=ranking)
    except Exception as e:
        logger.error(f"Dashboard Error: {str(e)}")
        return render_template("dashboard.html", total_sales=0, today_sales=0, uninvoiced=0, low_stock=0, m_labels=[], m_values=[], d_labels=[], d_values=[], s_labels=[], s_values=[], ranking=[])

@app.route("/customers", methods=["GET", "POST"])
@login_required
def customers():
    if request.method == "POST":
        try:
            new_customer = Customer(
                company_name=request.form.get("company_name", "").strip(),
                contact_name=request.form.get("contact_name", "").strip(),
                email=request.form.get("email", "").strip(),
                phone=request.form.get("phone", "").strip(),
                address=request.form.get("address", "").strip()
            )
            if not new_customer.company_name:
                flash("会社名は必須です")
                return redirect(url_for("customers"))
            db.session.add(new_customer)
            db.session.commit()
            flash("顧客を登録しました")
        except Exception as e:
            db.session.rollback()
            flash(f"エラー: {str(e)}")
        return redirect(url_for("customers"))
    q = request.args.get("q", "")
    items = Customer.query.filter(Customer.company_name.contains(q)).all()
    return render_template("customers.html", customers=items, q=q)

@app.route("/customers/<int:id>")
@login_required
def customer_detail(id):
    """顧客詳細ページ (新規)"""
    customer = db.session.get(Customer, id)
    if not customer:
        flash("顧客が見つかりません")
        return redirect(url_for("customers"))
    
    total_sales = db.session.query(db.func.sum(Order.total_price)).filter(Order.customer_id == id, Order.status != 'キャンセル').scalar() or 0
    order_count = Order.query.filter_by(customer_id=id).count()
    order_history = Order.query.filter_by(customer_id=id).order_by(Order.order_date.desc()).all()
    
    return render_template("customer_detail.html", customer=customer, total_sales=total_sales, order_count=order_count, orders=order_history)

@app.route("/customers/delete/<int:id>", methods=["POST"])
@login_required
def delete_customer(id):
    if current_user.role != 'admin':
        flash("管理者権限が必要です")
        return redirect(url_for("customers"))
    customer = db.session.get(Customer, id)
    if customer:
        try:
            db.session.delete(customer)
            db.session.commit()
            flash("顧客を削除しました")
        except:
            db.session.rollback()
            flash("受注データが存在するため削除できません")
    return redirect(url_for("customers"))

@app.route("/products", methods=["GET", "POST"])
@login_required
def products():
    if request.method == "POST":
        try:
            name = request.form.get("name", "").strip()
            price_str = request.form.get("price", "0")
            stock_str = request.form.get("stock", "0")
            if not name:
                flash("商品名は必須です")
                return redirect(url_for("products"))
            new_product = Product(name=name, price=int(price_str), stock=int(stock_str))
            db.session.add(new_product)
            db.session.commit()
            flash("商品を登録しました")
        except Exception as e:
            db.session.rollback()
            flash(f"エラー: {str(e)}")
        return redirect(url_for("products"))
    q = request.args.get("q", "")
    low_stock = request.args.get("low_stock", "")
    min_price = request.args.get("min_price", "")
    max_price = request.args.get("max_price", "")
    query = Product.query
    if q: query = query.filter(Product.name.contains(q))
    if low_stock: query = query.filter(Product.stock < 5)
    if min_price: query = query.filter(Product.price >= int(min_price))
    if max_price: query = query.filter(Product.price <= int(max_price))
    items = query.order_by(Product.stock.asc()).all()
    return render_template("products.html", products=items, q=q, low_stock=low_stock, min_price=min_price, max_price=max_price)

@app.route("/products/edit/<int:id>", methods=["GET", "POST"])
@login_required
def edit_product(id):
    product = db.session.get(Product, id)
    if request.method == "POST":
        try:
            product.name = request.form.get("name", "").strip()
            product.price = int(request.form.get("price", "0"))
            product.stock = int(request.form.get("stock", "0"))
            db.session.commit()
            flash("更新しました")
            return redirect(url_for("products"))
        except:
            db.session.rollback()
            flash("更新に失敗しました")
    return render_template("edit_product.html", product=product)

@app.route("/products/delete/<int:id>", methods=["POST"])
@login_required
def delete_product(id):
    if current_user.role != 'admin':
        flash("管理者権限が必要です")
        return redirect(url_for("products"))
    product = db.session.get(Product, id)
    if product:
        try:
            db.session.delete(product)
            db.session.commit()
            flash("削除しました")
        except:
            db.session.rollback()
            flash("他のデータで使用されているため削除できません")
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
    query = Order.query.join(Product).outerjoin(Customer)
    if q: query = query.filter(Product.name.contains(q))
    if customer_q: query = query.filter(Customer.company_name.contains(customer_q))
    if status: query = query.filter(Order.status == status)
    if date_from: query = query.filter(db.func.date(Order.order_date) >= date_from)
    if date_to: query = query.filter(db.func.date(Order.order_date) <= date_to)
    if uninvoiced: query = query.filter(Order.status.in_(['未対応', '未請求']))
    items = query.order_by(Order.order_date.desc()).all()
    return render_template("orders.html", orders=items, q=q, customer_q=customer_q, status_filter=status, date_from=date_from, date_to=date_to, uninvoiced=uninvoiced)

@app.route("/orders/add", methods=["GET", "POST"])
@login_required
def add_order():
    if request.method == "POST":
        try:
            p_id = request.form.get("product_id")
            c_id = request.form.get("customer_id")
            qty = int(request.form.get("quantity", "1"))
            product = db.session.get(Product, int(p_id))
            if product and product.stock >= qty:
                new_order = Order(
                    product_id=product.id,
                    customer_id=int(c_id) if c_id else None,
                    quantity=qty,
                    total_price=product.price * qty,
                    status="未対応",
                    created_by=current_user.username,
                    order_date=datetime.utcnow()
                )
                product.stock -= qty
                db.session.add(new_order)
                db.session.commit()
                flash("受注を登録しました")
                return redirect(url_for("orders"))
            flash("在庫不足または商品が見つかりません")
        except Exception as e:
            db.session.rollback()
            flash(f"エラー: {str(e)}")
    products = Product.query.order_by(Product.name).all()
    customers = Customer.query.order_by(Customer.company_name).all()
    return render_template("add_order.html", products=products, customers=customers)

@app.route("/orders/update/<int:id>", methods=["POST"])
@login_required
def update_status(id):
    order = db.session.get(Order, id)
    if order:
        order.status = request.form.get("status")
        order.updated_at = datetime.utcnow()
        db.session.commit()
        flash("更新しました")
    return redirect(url_for("orders"))

@app.route("/orders/pdf/<int:id>")
@login_required
def generate_pdf(id):
    order = db.session.get(Order, id)
    if not order: return "NotFound", 404
    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)
    p.setFont("Helvetica", 16)
    p.drawString(100, 800, f"INVOICE (ID: {order.id})")
    p.setFont("Helvetica", 12)
    p.drawString(100, 750, f"Customer: {order.customer.company_name if order.customer else 'N/A'}")
    p.drawString(100, 730, f"Date: {order.order_date.strftime('%Y-%m-%d')}")
    p.line(100, 710, 500, 710)
    p.drawString(100, 680, f"Product: {order.product.name}")
    p.drawString(100, 660, f"Quantity: {order.quantity}")
    p.drawString(100, 640, f"Total: {order.total_price:,} JPY")
    p.showPage()
    p.save()
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name=f"invoice_{id}.pdf", mimetype='application/pdf')

@app.route("/kanban")
@login_required
def kanban():
    """営業案件Kanbanボード"""
    statuses = ["新規", "ヒアリング", "提案中", "契約待ち", "成約", "失注"]
    orders = Order.query.join(Product).outerjoin(Customer).all()
    
    # ステータスごとに分類
    board_data = {status: [] for status in statuses}
    for o in orders:
        if o.status in board_data:
            board_data[o.status].append(o)
        else:
            # 既存のステータス（未対応等）を「新規」にマッピング
            board_data["新規"].append(o)
            
    # 各カラムの集計（件数と合計金額）
    stats = {status: {
        "count": len(board_data[status]),
        "total": sum(o.total_price for o in board_data[status])
    } for status in statuses}

    return render_template("kanban.html", board_data=board_data, statuses=statuses, stats=stats)

@app.route("/api/update_status", methods=["POST"])
@login_required
def api_update_status():
    """非同期でのステータス更新"""
    try:
        data = request.get_json()
        order_id = data.get("order_id")
        new_status = data.get("status")
        
        order = db.session.get(Order, order_id)
        if order:
            order.status = new_status
            order.updated_at = datetime.utcnow()
            db.session.commit()
            return {"success": True, "message": "ステータスを更新しました"}
        return {"success": False, "message": "受注が見つかりません"}, 404
    except Exception as e:
        db.session.rollback()
        return {"success": False, "message": str(e)}, 500

@app.route("/export/csv")
@login_required
def export_csv():
    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(["ID", "日付", "顧客", "商品", "金額", "状態"])
    for o in Order.query.all():
        cw.writerow([o.id, o.order_date, o.customer.company_name if o.customer else 'N/A', o.product.name, o.total_price, o.status])
    resp = make_response(si.getvalue().encode("utf-8-sig"))
    resp.headers["Content-Disposition"] = "attachment; filename=orders.csv"
    resp.headers["Content-type"] = "text/csv"
    return resp

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
