import io
import csv
import os
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

# .env ファイルがあれば読み込む (ローカル開発用)
load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "sales-pro-secret-key-2026")

# --- データベース設定 ---
# プロジェクトのルートディレクトリを絶対パスで取得
basedir = os.path.abspath(os.path.dirname(__file__))
# instance フォルダのパスを作成
instance_path = os.path.join(basedir, 'instance')

# Renderや本番環境で instance フォルダが存在しない場合に自動生成
if not os.path.exists(instance_path):
    os.makedirs(instance_path)

# Render/Neon環境では DATABASE_URL が設定されます。
# 設定されていない場合は、安全な絶対パスで SQLite を使用します。
default_db = f"sqlite:///{os.path.join(instance_path, 'database.db')}"
db_url = os.environ.get("DATABASE_URL", default_db)

# Render(Neon)の postgres:// を postgresql:// に変換 (SQLAlchemy互換性)
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
    """ダッシュボード (分析画面)"""
    # KPIデータ
    total_sales = db.session.query(db.func.sum(Order.total_price)).filter(Order.status != 'キャンセル').scalar() or 0
    
    # 本日売上 (DBごとに日付取得方法を調整)
    today = datetime.utcnow().date()
    today_sales = db.session.query(db.func.sum(Order.total_price)).filter(
        db.func.date(Order.order_date) == today,
        Order.status != 'キャンセル'
    ).scalar() or 0
    
    uninvoiced = Order.query.filter_by(status='未請求').count()
    low_stock = Product.query.filter(Product.stock < 5).count()
    
    # --- グラフ用データ ---
    
    # DBエンジンの判別
    is_postgres = db.engine.url.drivername.startswith("postgresql")

    # 1. 月別売上推移
    if is_postgres:
        monthly_fmt = db.func.to_char(Order.order_date, 'YYYY-MM')
    else:
        monthly_fmt = db.func.strftime('%Y-%m', Order.order_date)

    monthly = db.session.query(
        monthly_fmt.label('m'),
        db.func.sum(Order.total_price).label('s')
    ).group_by('m').order_by(db.desc('m')).limit(6).all()
    
    # 2. 日別売上グラフ (直近7日間)
    seven_days_ago = datetime.utcnow() - timedelta(days=7)
    daily = db.session.query(
        db.func.date(Order.order_date).label('d'),
        db.func.sum(Order.total_price).label('s')
    ).filter(Order.order_date >= seven_days_ago)\
     .group_by('d').order_by(db.asc('d')).all()
    
    # 3. ステータス分布
    status_data = db.session.query(Order.status, db.func.count(Order.id)).group_by(Order.status).all()
    
    # 4. 商品売上ランキング
    ranking = db.session.query(
        Product.name, 
        db.func.sum(Order.total_price).label('total'), 
        db.func.sum(Order.quantity).label('qty')
    ).join(Order).filter(Order.status != 'キャンセル')\
     .group_by(Product.id, Product.name).order_by(db.desc('total')).limit(5).all()

    return render_template("dashboard.html", 
                           total_sales=total_sales, today_sales=today_sales, 
                           uninvoiced=uninvoiced, low_stock=low_stock,
                           m_labels=[r[0] for r in reversed(monthly)], m_values=[r[1] for r in reversed(monthly)],
                           d_labels=[r[0] for r in daily], d_values=[r[1] for r in daily],
                           s_labels=[r[0] for r in status_data], s_values=[r[1] for r in status_data],
                           ranking=ranking)

@app.route("/customers", methods=["GET", "POST"])
@login_required
def customers():
    """顧客の登録と一覧表示"""
    if request.method == "POST":
        try:
            # フォームデータの取得
            new_customer = Customer(
                company_name=request.form.get("company_name", "").strip(),
                contact_name=request.form.get("contact_name", "").strip(),
                email=request.form.get("email", "").strip(),
                phone=request.form.get("phone", "").strip(),
                address=request.form.get("address", "").strip()
            )
            
            # 必須項目のチェック
            if not new_customer.company_name:
                flash("会社名は必須です")
                return redirect(url_for("customers"))
            
            db.session.add(new_customer)
            db.session.commit()
            flash("顧客を登録しました")
        except Exception as e:
            db.session.rollback()
            flash(f"登録エラーが発生しました: {str(e)}")
        return redirect(url_for("customers"))
    
    q = request.args.get("q", "")
    items = Customer.query.filter(Customer.company_name.contains(q)).all()
    return render_template("customers.html", customers=items, q=q)

@app.route("/customers/delete/<int:id>", methods=["POST"])
@login_required
def delete_customer(id):
    """顧客を削除する (管理者のみ)"""
    if current_user.role != 'admin':
        flash("管理者権限が必要です")
        return redirect(url_for("customers"))
        
    customer = db.session.get(Customer, id)
    if customer:
        try:
            db.session.delete(customer)
            db.session.commit()
            flash("顧客を削除しました")
        except Exception as e:
            db.session.rollback()
            flash(f"削除エラー: この顧客に関連する受注データがある可能性があります")
    else:
        flash("顧客が見つかりませんでした")
    return redirect(url_for("customers"))

@app.route("/products", methods=["GET", "POST"])
@login_required
def products():
    """商品の登録と一覧表示"""
    if request.method == "POST":
        try:
            # フォーム値を数値に変換 (PostgreSQL対策: 空文字などはエラーになるため)
            name = request.form.get("name", "").strip()
            price_str = request.form.get("price", "0")
            stock_str = request.form.get("stock", "0")
            
            if not name:
                flash("商品名は必須です")
                return redirect(url_for("products"))
            
            new_product = Product(
                name=name,
                price=int(price_str) if price_str else 0,
                stock=int(stock_str) if stock_str else 0
            )
            
            db.session.add(new_product)
            db.session.commit()
            flash("商品を登録しました")
        except ValueError:
            flash("価格と在庫には数値を入力してください")
        except Exception as e:
            db.session.rollback()
            flash(f"エラー: {str(e)}")
        return redirect(url_for("products"))
    
    q = request.args.get("q", "")
    items = Product.query.filter(Product.name.contains(q)).order_by(Product.stock.asc()).all()
    return render_template("products.html", products=items, q=q)

@app.route("/products/edit/<int:id>", methods=["GET", "POST"])
@login_required
def edit_product(id):
    """商品情報の編集"""
    product = db.session.get(Product, id) # SQLAlchemy 2.0 形式の取得
    if not product:
        flash("商品が見つかりません")
        return redirect(url_for("products"))

    if request.method == "POST":
        try:
            product.name = request.form.get("name", "").strip()
            product.price = int(request.form.get("price", "0"))
            product.stock = int(request.form.get("stock", "0"))
            db.session.commit()
            flash("商品情報を更新しました")
            return redirect(url_for("products"))
        except Exception as e:
            db.session.rollback()
            flash(f"更新エラー: {str(e)}")

    return render_template("edit_product.html", product=product)

@app.route("/products/delete/<int:id>", methods=["POST"])
@login_required
def delete_product(id):
    """商品を削除する (管理者のみ)"""
    if current_user.role != 'admin':
        flash("管理者権限が必要です")
        return redirect(url_for("products"))

    product = db.session.get(Product, id)
    if product:
        try:
            db.session.delete(product)
            db.session.commit()
            flash("商品を削除しました")
        except Exception as e:
            db.session.rollback()
            flash(f"削除エラー: 他のデータで使用されている可能性があります")
    else:
        flash("商品が見つかりませんでした")
    return redirect(url_for("products"))

@app.route("/orders")
@login_required
def orders():
    """受注一覧 (検索・絞り込み対応)"""
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
    """新規受注の登録"""
    if request.method == "POST":
        try:
            p_id = request.form.get("product_id")
            c_id = request.form.get("customer_id")
            qty_str = request.form.get("quantity", "1")
            
            # 数値変換とNone対策 (PostgreSQLで空文字をIntegerに入れようとすると500エラーになるため)
            product_id = int(p_id) if p_id else None
            # 顧客が未選択の場合は None (NULL) を入れる
            customer_id = int(c_id) if c_id and c_id != "" else None
            quantity = int(qty_str) if qty_str else 1
            
            product = db.session.get(Product, product_id)
            
            if product and product.stock >= quantity:
                new_order = Order(
                    product_id=product_id,
                    customer_id=customer_id,
                    quantity=quantity,
                    total_price=product.price * quantity,
                    status="未請求",
                    created_by=current_user.username
                )
                product.stock -= quantity # 在庫を減らす
                db.session.add(new_order)
                db.session.commit()
                flash("受注を登録しました")
                return redirect(url_for("orders"))
            else:
                flash("商品が見つからないか、在庫が不足しています")
        except Exception as e:
            db.session.rollback()
            flash(f"受注登録エラー: {str(e)}")
            
    products = Product.query.all()
    customers = Customer.query.all()
    return render_template("add_order.html", products=products, customers=customers)

@app.route("/orders/update/<int:id>", methods=["POST"])
@login_required
def update_status(id):
    """受注ステータスの更新"""
    order = db.session.get(Order, id)
    if not order:
        flash("受注データが見つかりません")
        return redirect(url_for("orders"))
        
    try:
        order.status = request.form.get("status")
        order.updated_at = datetime.utcnow()
        db.session.commit()
        flash("ステータスを更新しました")
    except Exception as e:
        db.session.rollback()
        flash(f"更新エラー: {str(e)}")
        
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
