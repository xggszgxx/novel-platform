import os
import re
from difflib import SequenceMatcher
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, redirect, url_for, flash, request
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, TextAreaField, SubmitField, SelectField
from wtforms.validators import DataRequired, Length, EqualTo
from werkzeug.security import generate_password_hash, check_password_hash

basedir = os.path.abspath(os.path.dirname(__file__))

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-me")
db_url = os.environ.get("DATABASE_URL")
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = db_url or "sqlite:///" + os.path.join(basedir, "novel.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config['WTF_CSRF_ENABLED'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"

SENSITIVE_WORDS = ["违禁词1", "违禁词2", "测试敏感词"]

# ---------- 模型 ----------
class User(UserMixin, db.Model):
    __tablename__ = "user"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    novels = db.relationship("Novel", backref="author", lazy=True)

class Novel(db.Model):
    __tablename__ = "novel"
    id = db.Column(db.Integer, primary_key=True)
    author_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    title = db.Column(db.String(120), nullable=False)
    intro = db.Column(db.Text, default="")
    status = db.Column(db.String(20), default="pending", nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    # 作品下面有很多卷
    volumes = db.relationship("Volume", backref="novel", lazy=True, cascade="all, delete-orphan")

class Volume(db.Model):
    __tablename__ = "volume"
    id = db.Column(db.Integer, primary_key=True)
    novel_id = db.Column(db.Integer, db.ForeignKey("novel.id"), nullable=False)
    title = db.Column(db.String(120), nullable=False, default="第一卷")
    order_num = db.Column(db.Integer, default=1) # 卷的排序
    # 卷下面有很多章节
    chapters = db.relationship("Chapter", backref="volume", lazy=True, cascade="all, delete-orphan")

class Chapter(db.Model):
    __tablename__ = "chapter"
    id = db.Column(db.Integer, primary_key=True)
    volume_id = db.Column(db.Integer, db.ForeignKey("volume.id"), nullable=False)
    title = db.Column(db.String(120), nullable=False)
    content = db.Column(db.Text, nullable=False)
    word_count = db.Column(db.Integer, default=0)
    status = db.Column(db.String(20), default="pending", nullable=False)
    reject_reason = db.Column(db.Text, default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# ---------- 表单 ----------
class RegisterForm(FlaskForm):
    username = StringField("用户名", validators=[DataRequired(), Length(min=3, max=50)])
    password = PasswordField("密码", validators=[DataRequired(), Length(min=6)])
    confirm = PasswordField("确认密码", validators=[DataRequired(), EqualTo("password", message="两次密码不一致")])
    submit = SubmitField("注册")

class LoginForm(FlaskForm):
    username = StringField("用户名", validators=[DataRequired()])
    password = PasswordField("密码", validators=[DataRequired()])
    submit = SubmitField("登录")

class NovelForm(FlaskForm):
    title = StringField("书名", validators=[DataRequired(), Length(max=120)])
    intro = TextAreaField("简介", validators=[Length(max=500)])
    submit = SubmitField("保存作品")

class ChapterForm(FlaskForm):
    volume_id = SelectField("所属卷", coerce=int, validators=[DataRequired()])
    title = StringField("章节名", validators=[DataRequired(), Length(max=120)])
    content = TextAreaField("正文", validators=[DataRequired()])
    submit = SubmitField("保存章节")

# ---------- 路由 ----------
@app.route("/")
def index():
    novels = Novel.query.filter_by(status="approved").order_by(Novel.created_at.desc()).all()
    return render_template("index.html", novels=novels)

@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated: return redirect(url_for("index"))
    form = RegisterForm()
    if form.validate_on_submit():
        if User.query.filter_by(username=form.username.data).first():
            flash("用户名已存在", "danger")
            return render_template("register.html", form=form)
        user = User(username=form.username.data, password_hash=generate_password_hash(form.password.data))
        db.session.add(user)
        db.session.commit()
        flash("注册成功，请登录", "success")
        return redirect(url_for("login"))
    return render_template("register.html", form=form)

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated: return redirect(url_for("index"))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user and check_password_hash(user.password_hash, form.password.data):
            login_user(user)
            flash("登录成功", "success")
            return redirect(url_for("index"))
        flash("用户名或密码错误", "danger")
    return render_template("login.html", form=form)

@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("已退出登录", "info")
    return redirect(url_for("index"))

@app.route("/my")
@login_required
def my_novels():
    novels = Novel.query.filter_by(author_id=current_user.id).order_by(Novel.created_at.desc()).all()
    return render_template("my_novels.html", novels=novels)

# 创建/修改作品
@app.route("/novel/new", methods=["GET", "POST"])
@app.route("/novel/<int:novel_id>/edit", methods=["GET", "POST"])
@login_required
def edit_novel(novel_id=None):
    if novel_id:
        novel = Novel.query.get_or_404(novel_id)
        if novel.author_id != current_user.id and not current_user.is_admin:
            flash("无权操作", "danger"); return redirect(url_for("index"))
    else:
        novel = None
        
    form = NovelForm()
    if form.validate_on_submit():
        if not novel:
            novel = Novel(author_id=current_user.id, status="pending")
            db.session.add(novel)
        novel.title = form.title.data
        novel.intro = form.intro.data
        db.session.commit()
        
        # 如果是新书，自动建一个默认的“第一卷”
        if not novel.volumes:
            v = Volume(novel_id=novel.id, title="第一卷", order_num=1)
            db.session.add(v)
            db.session.commit()
            
        flash("作品信息已保存", "success")
        return redirect(url_for("manage_novel", novel_id=novel.id))
    elif request.method == "GET" and novel:
        form.title.data = novel.title
        form.intro.data = novel.intro

    return render_template("edit_novel.html", form=form, novel=novel)

# 章节管理台
@app.route("/novel/<int:novel_id>/manage")
@login_required
def manage_novel(novel_id):
    novel = Novel.query.get_or_404(novel_id)
    if novel.author_id != current_user.id and not current_user.is_admin:
        flash("无权访问", "danger"); return redirect(url_for("index"))
    return render_template("manage_novel.html", novel=novel)
# 读者查看书籍目录
@app.route("/novel/<int:novel_id>")
def novel_detail(novel_id):
    novel = Novel.query.get_or_404(novel_id)
    if novel.status != "approved":
        flash("该书暂未过审或不存在", "danger")
        return redirect(url_for("index"))
    return render_template("novel_detail.html", novel=novel)

# 新建/修改卷
@app.route("/novel/<int:novel_id>/volume/new", methods=["POST"])
@login_required
def create_volume(novel_id):
    novel = Novel.query.get_or_404(novel_id)
    if novel.author_id != current_user.id: return redirect(url_for("index"))
    title = request.form.get("volume_title", "新卷")
    order = len(novel.volumes) + 1
    v = Volume(novel_id=novel.id, title=title, order_num=order)
    db.session.add(v)
    db.session.commit()
    flash("新卷已创建", "success")
    return redirect(url_for("manage_novel", novel_id=novel.id))

@app.route("/volume/<int:volume_id>/edit", methods=["POST"])
@login_required
def edit_volume(volume_id):
    v = Volume.query.get_or_404(volume_id)
    if v.novel.author_id != current_user.id: return redirect(url_for("index"))
    title = request.form.get("volume_title")
    if title:
        v.title = title
        db.session.commit()
        flash("卷名已修改", "success")
    return redirect(url_for("manage_novel", novel_id=v.novel_id))

# 新建/修改章节
@app.route("/novel/<int:novel_id>/chapter/new", methods=["GET", "POST"])
@app.route("/chapter/<int:chapter_id>/edit", methods=["GET", "POST"])
@login_required
def edit_chapter(novel_id=None, chapter_id=None):
    if chapter_id:
        chapter = Chapter.query.get_or_404(chapter_id)
        novel = chapter.volume.novel
        if novel.author_id != current_user.id: return redirect(url_for("index"))
    else:
        novel = Novel.query.get_or_404(novel_id)
        if novel.author_id != current_user.id: return redirect(url_for("index"))
        chapter = None

    form = ChapterForm()
    # 动态加载卷列表
    form.volume_id.choices = [(v.id, v.title) for v in novel.volumes]

    if form.validate_on_submit():
        content = form.content.data
        status = "pending"
        reason = ""
        word_count = len(content)
        
        if word_count < 500:
            status = "rejected"
            reason = "自动驳回：正文不足 500 字。"
        else:
            for word in SENSITIVE_WORDS:
                if word in content:
                    status = "rejected"
                    reason = f"自动驳回：包含敏感词「{word}」。"
                    break
                    
        if not chapter:
            chapter = Chapter(status=status, reject_reason=reason)
            db.session.add(chapter)
        
        chapter.volume_id = form.volume_id.data
        chapter.title = form.title.data
        chapter.content = content
        chapter.word_count = word_count
        chapter.status = status
        chapter.reject_reason = reason
        db.session.commit()
        
        flash("章节保存成功，已提交审核", "success")
        return redirect(url_for("manage_novel", novel_id=novel.id))
        
    elif request.method == "GET" and chapter:
        form.volume_id.data = chapter.volume_id
        form.title.data = chapter.title
        form.content.data = chapter.content

    return render_template("edit_chapter.html", form=form, novel=novel, chapter=chapter)

# 阅读页
@app.route("/novel/<int:novel_id>/chapter/<int:chapter_id>")
def read_chapter(novel_id, chapter_id):
    novel = Novel.query.get_or_404(novel_id)
    chapter = Chapter.query.get_or_404(chapter_id)
    if chapter.status != "approved" and not (current_user.is_authenticated and (current_user.is_admin or current_user.id == novel.author_id)):
        flash("该章节暂未通过审核", "danger")
        return redirect(url_for("index"))
    return render_template("read_chapter.html", novel=novel, chapter=chapter)

# ---------- 管理员 ----------
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash("需要管理员权限", "danger"); return redirect(url_for("index"))
        return f(*args, **kwargs)
    return decorated_function

@app.route("/become-admin")
@login_required
def become_admin():
    current_user.is_admin = True
    db.session.commit()
    flash("提权成功，你现在是管理员了！", "success")
    return redirect(url_for("admin_dashboard"))
    
@app.route("/admin")
@login_required
@admin_required
def admin_dashboard():
    novels = Novel.query.filter_by(status="pending").all()
    chapters = Chapter.query.filter_by(status="pending").all()
    return render_template("admin.html", novels=novels, chapters=chapters)

@app.route("/admin/approve_novel/<int:id>", methods=["POST"])
@login_required
@admin_required
def approve_novel(id):
    novel = Novel.query.get_or_404(id)
    novel.status = "approved"
    db.session.commit()
    flash(f"作品《{novel.title}》已上架", "success")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/approve_chapter/<int:id>", methods=["POST"])
@login_required
@admin_required
def approve_chapter(id):
    chapter = Chapter.query.get_or_404(id)
    chapter.status = "approved"
    chapter.volume.novel.status = "approved"
    db.session.commit()
    flash(f"章节《{chapter.title}》已发布", "success")
    return redirect(url_for("admin_dashboard"))
with app.app_context():
    db.create_all()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
