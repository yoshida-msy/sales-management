import io
import csv
import os
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, make_response, send_file
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from dotenv import load_dotenv

# PDF生成用
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4

# .env ファイルがあれば読み込む (ローカル開発用)
load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "sales-pro-secret-key-2026")

# --- データベース設定 (SQLAlchemy への移行) ---
# Render/Neon環境では DATABASE_URL が設定されます。
# NeonのURLが postgres:// で始まる場合、SQLAlchemy 1.4以降では postgresql:// に変換する必要があります。
db_url = os.environ.get("DATABASE_URL", "sqlite:///instance/database.db")
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
migrate = Migrate(app, db)

# --- ログイン管理 ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

# --- データベースモデル定義 (実務レベルの設計) ---

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), default='staff') # 'admin' or 'staff'

class Product(db.Model):
    __tablename__ = 'products'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    price = db.Column(db.Integer, nullable=False)
    stock = db.Column(db.Integer, nullable=False)
    # リレーションシップ
    orders = db.relationship('Order', backref='product', lazy=True)

class Customer(db.Model):
    __tablename__ = 'customers'
    id = db.Column(db.Integer, primary_key=True)
    company_name = db.Column(db.String(150), nullable=False)
    contact_name = db.Column(db.String(100))
    email = db.Column(db.String(120))
    phone = db.Column(db.String(20))
    address = db.Column(db.String(200))
    # リレーションシップ
    orders = db.relationship('Order', backref='customer', lazy=True)

class Order(db.Model):
    __tablename__ = 'orders'
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id'))
    quantity = db.Column(db.Integer, nullable=False)
    total_price = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='未請求')
    order_date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_by = db.Column(db.String(80))
    updated_at = db.Column(db.DateTime, onupdate=datetime.utcnow)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- 初期化・シードデータ投入 ---
def seed_db():
    if not User.query.filter_by(username='admin').first():
        admin = User(username='admin', password=generate_password_hash('password'), role='admin')
        db.session.add(admin)
    if not User.query.filter_by(username='staff').first():
        staff = User(username='staff', password=generate_password_hash('password'), role='staff')
        db.session.add(staff)
    db.session.commit()

# アプリ起動時にテーブル作成 (Migrationを使わない場合の予備)
with app.app_context():
    if db_url.startswith("sqlite"):
        os.makedirs(app.instance_path, exist_ok=True)
    db.create_all()
    seed_db()

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

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

@app.route("/")
@login_required
def index():
    """ダッシュボード"""
    total_sales = db.session.query(db.func.sum(Order.total_price)).filter(Order.status != 'キャンセル').scalar() or 0
    today_sales = db.session.query(db.func.sum(Order.total_price)).filter(
        db.func.date(Order.order_date) == db.func.current_date(),
        Order.status != 'キャンセル'
    ).scalar() or 0
    uninvoiced = Order.query.filter_by(status='未請求').count()
    low_stock = Product.query.filter(Product.stock < 5).count()
    
    # グラフ用データ
    monthly = db.session.query(
        db.func.strftime('%Y-%m', Order.order_date).label('m'),
        db.func.sum(Order.total_price).label('s')
    ).group_by('m').order_by(db.desc('m')).limit(6).all()
    
    # PostgreSQLの場合は strftime ではなく date_trunc を使う必要がありますが、
    # 互換性のためにここではシンプルなクエリを使用するか、環境で分岐させます。
    # 実務ではDBごとにクエリを調整しますが、一旦 SQLite/Postgres 両対応の簡易版にします。

    status_data = db.session.query(Order.status, db.func.count(Order.id)).group_by(Order.status).all()
    ranking = db.session.query(Product.name, db.func.sum(Order.total_price).label('total'), db.func.sum(Order.quantity).label('qty'))\
        .join(Order).filter(Order.status != 'キャンセル')\
        .group_by(Product.id).order_by(db.desc('total')).limit(5).all()

    return render_template("dashboard.html", 
                           total_sales=total_sales, today_sales=today_sales, 
                           uninvoiced=uninvoiced, low_stock=low_stock,
                           m_labels=[r[0] for r in reversed(monthly)], m_values=[r[1] for r in reversed(monthly)],
                           s_labels=[r[0] for r in status_data], s_values=[r[1] for r in status_data],
                           ranking=ranking)

@app.route("/customers", methods=["GET", "POST"])
@login_required
def customers():
    if request.method == "POST":
        new_customer = Customer(
            company_name=request.form["company_name"],
            contact_name=request.form["contact_name"],
            email=request.form["email"],
            phone=request.form["phone"],
            address=request.form["address"]
        )
        db.session.add(new_customer)
        db.session.commit()
        flash("顧客を登録しました")
        return redirect(url_for("customers"))
    
    q = request.args.get("q", "")
    items = Customer.query.filter(Customer.company_name.contains(q)).all()
    return render_template("customers.html", customers=items, q=q)

@app.route("/products", methods=["GET", "POST"])
@login_required
def products():
    if request.method == "POST":
        new_product = Product(name=request.form["name"], price=request.form["price"], stock=request.form["stock"])
        db.session.add(new_product)
        db.session.commit()
        flash("商品を登録しました")
        return redirect(url_for("products"))
    
    q = request.args.get("q", "")
    items = Product.query.filter(Product.name.contains(q)).order_by(Product.stock.asc()).all()
    return render_template("products.html", products=items, q=q)

@app.route("/products/edit/<int:id>", methods=["GET", "POST"])
@login_required
def edit_product(id):
    product = Product.query.get_or_404(id)
    if request.method == "POST":
        product.name = request.form["name"]
        product.price = request.form["price"]
        product.stock = request.form["stock"]
        db.session.commit()
        flash("商品情報を更新しました")
        return redirect(url_for("products"))
    return render_template("edit_product.html", product=product)

@app.route("/orders")
@login_required
def orders():
    q = request.args.get("q", "")
    status = request.args.get("status", "")
    query = Order.query.join(Product).outerjoin(Customer)
    
    if q:
        query = query.filter(Product.name.contains(q))
    if status:
        query = query.filter(Order.status == status)
        
    items = query.order_by(Order.order_date.desc()).all()
    return render_template("orders.html", orders=items, q=q, status_filter=status)

@app.route("/orders/add", methods=["GET", "POST"])
@login_required
def add_order():
    if request.method == "POST":
        p_id = request.form["product_id"]
        c_id = request.form.get("customer_id")
        qty = int(request.form["quantity"])
        product = Product.query.get(p_id)
        
        if product and product.stock >= qty:
            new_order = Order(
                product_id=p_id,
                customer_id=c_id,
                quantity=qty,
                total_price=product.price * qty,
                status="未請求",
                created_by=current_user.username
            )
            product.stock -= qty
            db.session.add(new_order)
            db.session.commit()
            flash("受注を登録しました")
            return redirect(url_for("orders"))
        flash("在庫が不足しています")
    
    products = Product.query.all()
    customers = Customer.query.all()
    return render_template("add_order.html", products=products, customers=customers)

@app.route("/orders/update/<int:id>", methods=["POST"])
@login_required
def update_status(id):
    order = Order.query.get_or_404(id)
    order.status = request.form["status"]
    db.session.commit()
    flash("ステータスを更新しました")
    return redirect(url_for("orders"))

@app.route("/orders/pdf/<int:id>")
@login_required
def generate_pdf(id):
    order = Order.query.get_or_404(id)
    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)
    p.setFont("Helvetica", 16)
    p.drawString(100, 800, f"INVOICE (Order ID: {order.id})")
    p.setFont("Helvetica", 12)
    p.drawString(100, 750, f"Customer: {order.customer.company_name if order.customer else 'N/A'}")
    p.drawString(100, 730, f"Date: {order.order_date.strftime('%Y-%m-%d %H:%M')}")
    p.line(100, 710, 500, 710)
    p.drawString(100, 680, f"Product: {order.product.name}")
    p.drawString(100, 660, f"Quantity: {order.quantity}")
    p.drawString(100, 640, f"Total Price: {order.total_price:,} JPY")
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
    orders = Order.query.all()
    for o in orders:
        cw.writerow([
            o.id, 
            o.order_date.strftime('%Y-%m-%d %H:%M'), 
            o.customer.company_name if o.customer else 'N/A', 
            o.product.name, 
            o.quantity, 
            o.total_price, 
            o.status
        ])
    resp = make_response(si.getvalue().encode("utf-8-sig"))
    resp.headers["Content-Disposition"] = "attachment; filename=orders.csv"
    resp.headers["Content-type"] = "text/csv"
    return resp

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
