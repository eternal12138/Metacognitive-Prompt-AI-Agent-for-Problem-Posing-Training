# SPDX-License-Identifier: AGPL-3.0-only OR Apache-2.0

from flask import Flask, render_template, request, jsonify, session, redirect, url_for, Response, stream_with_context, abort
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
from flask import make_response
from werkzeug.security import generate_password_hash, check_password_hash
from concurrent.futures import ThreadPoolExecutor
from functools import wraps
import os
import chromadb
import threading
import requests
import json
import io
import csv
import hmac
import re
import secrets
import sys

try:
    import webview
except ImportError:
    webview = None


# ================= 动态路径解析核心魔法 =================
# 区分是直接运行的 Python 还是打包后的 .exe
if getattr(sys, 'frozen', False):
    # 打包后的运行环境：静态文件会被解压到临时目录
    bundle_dir = sys._MEIPASS
    # 数据库和配置文件，必须放在 .exe 所在的真实外部目录，否则重启就会丢失！
    data_dir = os.path.dirname(sys.executable)
else:
    # 正常 Python 运行环境
    bundle_dir = os.path.abspath(os.path.dirname(__file__))
    data_dir = bundle_dir

data_dir = os.path.abspath(os.environ.get("APP_DATA_DIR", data_dir))
os.makedirs(os.path.join(data_dir, "instance"), exist_ok=True)

app = Flask(__name__, 
            template_folder=os.path.join(bundle_dir, 'templates'),
            static_folder=os.path.join(bundle_dir, 'static'))

APP_ENV = os.environ.get("APP_ENV", "development").lower()
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
if APP_ENV == "production" and len(os.environ.get("FLASK_SECRET_KEY", "")) < 32:
    raise RuntimeError("生产环境必须设置至少 32 个字符的 FLASK_SECRET_KEY")

DEFAULT_USER_PASSWORD = os.environ.get("DEFAULT_USER_PASSWORD", "")
if APP_ENV == "production" and len(DEFAULT_USER_PASSWORD) < 10:
    raise RuntimeError("生产环境必须设置至少 10 位的 DEFAULT_USER_PASSWORD")
if not DEFAULT_USER_PASSWORD:
    DEFAULT_USER_PASSWORD = "123456"

app.config.update(
    MAX_CONTENT_LENGTH=2 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "0") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
)

# ================= 核心配置区 =================
# 实验数据保存路径
LOG_FILE = os.path.join(data_dir, 'experiment_data.csv')
USER_DB = os.path.join(data_dir, 'users.csv')  
TASK_FILE = os.path.join(data_dir, 'task_config.json')



app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(data_dir, 'instance', 'user_center.db')
# 业务数据库
app.config['SQLALCHEMY_BINDS'] = {
    'business_db': 'sqlite:///' + os.path.join(data_dir, 'instance', 'chat_data.db')
}
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)



# 问答以及批阅模型配置
API_KEY = os.environ.get("ARK_API_KEY", "")
API_URL = os.environ.get("ARK_API_URL", "https://ark.cn-beijing.volces.com/api/v3/chat/completions")
CHAT_MODEL_NAME = os.environ.get("CHAT_MODEL_NAME", "doubao-seed-2-0-pro-260215")
EVAL_MODEL_NAME = os.environ.get("EVAL_MODEL_NAME", "deepseek-v3-2-251201")

#向量模型配置
EMBEDDING_API_KEY = os.environ.get("EMBEDDING_API_KEY", "")
EMBEDDING_API_URL = os.environ.get("EMBEDDING_API_URL", "https://api.siliconflow.cn/v1/embeddings")
EMBEDDING_MODEL_NAME = os.environ.get("EMBEDDING_MODEL_NAME", "BAAI/bge-m3")



# ================= 向量数据库配置 (ChromaDB) =================
chroma_client = chromadb.PersistentClient(path=os.path.join(data_dir, "instance", "vector_db"))
vector_collection = chroma_client.get_or_create_collection(name="student_memories_m3")




# ================= 2. 定义数据库表模型 (ORM) =================
class User(db.Model):
    """用户表 (没有指定 bind，默认存入 user_center.db)"""
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    real_name = db.Column(db.String(50), nullable=False)
    is_active = db.Column(db.Boolean, default=True) # 账号状态开关（默认 True 为正常，False 为冻结）
    can_manage_users = db.Column(db.Boolean, default=False)# 超管权限开关（True 表示有权管理系统用户，默认为 False）
    class_name = db.Column(db.String(50))  #
    managed_classes = db.Column(db.Text)   

class TaskConfig(db.Model):
    """任务配置表 (指定存入业务数据库)"""
    __bind_key__ = 'business_db'  # AI辅助生成：deepseek-v3-2-251201，2026-04-09，核心魔法：把它分流到 business_db
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False, default="等待老师发布任务...")
    content = db.Column(db.Text, nullable=False, default="请同学们稍安勿躁。")
    scaffold = db.Column(db.Text, default="")
    custom_standard = db.Column(db.Text, default="")

class ChatRecord(db.Model):
    """实验记录表 (指定存入业务数据库)"""
    __bind_key__ = 'business_db'  # AI辅助生成：deepseek-v3-2-251201，2026-04-09，核心魔法：把它分流到 business_db
    id = db.Column(db.Integer, primary_key=True)
    time = db.Column(db.String(50), nullable=False)
    student_name = db.Column(db.String(50))
    student_id = db.Column(db.String(50))
    question = db.Column(db.Text)
    ai_response = db.Column(db.Text)
    teacher_score = db.Column(db.String(10), default="")
    teacher_feedback = db.Column(db.Text, default="")
    task_name = db.Column(db.String(200))
    task_content = db.Column(db.Text)

class StudentMemory(db.Model):
    """长时记忆库：用于存放学生的动态学情摘要"""
    __bind_key__ = 'business_db'
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.String(50))
    task_name = db.Column(db.String(200))
    core_summary = db.Column(db.Text, default="暂无，这是该学生的首次探索。")
    last_summarized_id = db.Column(db.Integer, default=0) # 记录上次总结到了哪一条聊天记录


def _is_password_hash(value):
    return bool(value) and value.startswith(("scrypt:", "pbkdf2:"))


def _current_user():
    username = session.get("username")
    if not username:
        return None
    user = User.query.filter_by(username=username).first()
    if not user or not user.is_active:
        session.clear()
        return None
    return user


def _json_error(message, status=400):
    return jsonify({"status": "error", "message": message}), status


def _safe_csv_cell(value):
    """Prevent spreadsheet software from evaluating exported user text as a formula."""
    text = "" if value is None else str(value)
    if text.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text


def _valid_username(value):
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]{1,50}", value or ""))


def role_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = _current_user()
            if not user:
                return _json_error("请先登录", 401)
            if user.role not in roles:
                return _json_error("权限不足", 403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = _current_user()
        if not user:
            return _json_error("请先登录", 401)
        if user.role != "teacher" or not user.can_manage_users:
            return _json_error("仅管理员可执行此操作", 403)
        return view(*args, **kwargs)
    return wrapped


def _managed_class_names(user):
    return {
        name.strip()
        for name in (user.managed_classes or "").replace("，", ",").split(",")
        if name.strip()
    }


def _teacher_can_access_student(teacher, student):
    if teacher.can_manage_users:
        return True
    return (
        student is not None
        and student.role == "student"
        and bool(student.class_name)
        and student.class_name in _managed_class_names(teacher)
    )


def _teacher_student_ids(teacher):
    if teacher.can_manage_users:
        return [username for (username,) in db.session.query(User.username).filter(User.role == "student").all()]
    managed_classes = _managed_class_names(teacher)
    if not managed_classes:
        return []
    return [
        username
        for (username,) in db.session.query(User.username).filter(
            User.role == "student",
            User.class_name.in_(managed_classes),
        ).all()
    ]


def _csrf_token():
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


@app.context_processor
def inject_security_context():
    return {"csrf_token": _csrf_token()}


@app.before_request
def validate_csrf():
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        expected = session.get("_csrf_token")
        supplied = request.headers.get("X-CSRF-Token") or request.form.get("_csrf_token")
        if not expected or not supplied or not hmac.compare_digest(expected, supplied):
            abort(400, description="CSRF token validation failed")


@app.after_request
def apply_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; "
        "font-src 'self' https://cdnjs.cloudflare.com data:; "
        "img-src 'self' data:; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    )
    if request.path in {"/chat", "/api/ai_evaluate"}:
        response.headers["Cache-Control"] = "no-store"
    return response


# ================= 3. 数据库初始化与平滑迁移辅助函数 =================
with app.app_context():
    db.create_all()
    
    # 用户数据迁移逻辑（针对 user_center.db）
    if not User.query.first():
        if os.path.exists(USER_DB):
            print("🚀 检测到老版本 users.csv，正在迁移至用户中心(user_center.db)...")
            with open(USER_DB, mode='r', encoding='utf-8-sig') as f:
                import csv
                reader = csv.DictReader(f)
                for row in reader:
                    password_value = row['password']
                    if not _is_password_hash(password_value):
                        password_value = generate_password_hash(password_value)
                    new_user = User(
                        username=row['username'], password=password_value,
                        role=row['role'], real_name=row['real_name']
                    )
                    db.session.add(new_user)
            db.session.commit()
            print("✅ 用户数据迁移成功！")
        else:
            initial_admin_username = os.environ.get("INITIAL_ADMIN_USERNAME", "admin").strip()
            initial_admin_password = os.environ.get("INITIAL_ADMIN_PASSWORD", "")
            if APP_ENV == "production" and (
                not _valid_username(initial_admin_username)
                or len(initial_admin_password) < 12
            ):
                raise RuntimeError(
                    "首次生产部署必须设置合法的 INITIAL_ADMIN_USERNAME 和至少 12 位的 INITIAL_ADMIN_PASSWORD"
                )
            if not initial_admin_password:
                initial_admin_password = "123456"
                print("开发环境已创建默认管理员 admin/123456，请立即修改密码。")
            super_admin = User(
                username=initial_admin_username,
                password=generate_password_hash(initial_admin_password),
                role='teacher',
                real_name='超管',
                can_manage_users=True,
            )
            test_student = User(username='2026001', password=generate_password_hash('123456'), role='student', real_name='张三')
            db.session.add(super_admin)
            db.session.add(test_student)
            db.session.commit()
            
    # 业务数据初始化逻辑（针对 chat_data.db）
    if not db.session.get(TaskConfig, 1):
        db.session.add(TaskConfig(id=1))
        db.session.commit()

    # 从旧版 JSON 配置一次性迁移到数据库，之后统一以数据库为准。
    task_record = db.session.get(TaskConfig, 1)
    if os.path.exists(TASK_FILE) and (
        not task_record.title
        or task_record.title == "等待老师发布任务..."
        or "\ufffd" in task_record.title
    ):
        try:
            with open(TASK_FILE, "r", encoding="utf-8") as task_stream:
                legacy_task = json.load(task_stream)
            task_record.title = str(legacy_task.get("title") or "等待老师发布任务...")[:200]
            task_record.content = str(legacy_task.get("content") or "请同学们稍安勿躁。")
            task_record.scaffold = str(legacy_task.get("scaffold") or "")
            task_record.custom_standard = str(legacy_task.get("custom_standard") or "")
            db.session.commit()
            print("✅ 旧版 task_config.json 已迁移到数据库。")
        except (OSError, ValueError, TypeError) as exc:
            db.session.rollback()
            print(f"⚠️ 任务配置迁移失败，将继续使用数据库默认值: {exc}")

# ================= AI 核心处理模块 =================

# ================= 核心配置区 =================

'''
# Ollama 本地默认服务地址
OLLAMA_API_URL = "http://localhost:11434/api/chat"
MODEL_NAME = "qwen3-vl-lite:latest" 
'''


USER_DB = 'users.csv'
LOG_FILE = 'experiment_data.csv'


# ================= AI 核心处理模块 =================
'''
def call_ai_api(user_input, student_name):
    """
    调用本地 Ollama 模型
    """
    # 1. 结构化的元认知提示词（提示词工程的落地处）
    system_prompt = f"""
    你是一位经验丰富的苏格拉底式导师。当前正在辅导学生：{student_name}。
    【核心任务】：引导学生针对给定的阅读材料或物理现象，提出更深刻、更有探究价值的问题。
    【核心目标】：绝对不直接告诉学生科学原理或正确答案！而是通过诊断学生当前提问的层次，利用循序渐进的引导和认知冲突，帮助他们修补认知漏洞，提升元认知能力与高阶思维。

    【执行工作流 (Chain-of-Thought)】 
    在回复学生之前，你必须在后台默默完成以下三步思考（切记：这三步是你的内部推理，绝对不允许输出给学生！）：
    > 第一步：隐性诊断。分析学生当前的提问处于什么层次（是只关注了表面现象？是缺乏前提条件？还是已经触及了核心机制？）。
    > 第二步：匹配梯度支架 (Pose-EMD)。根据诊断结果，在心里准备一个难度递进的启发策略：
        - 基础激活：引导关注材料中的具体细节或矛盾点。
        - 深入纠偏：引导学生将现象与具体条件结合，例如“如果改变某一个条件，现象还会发生吗？”
        - 认知冲突：举出一个极端的反例，打破学生的固有认知。
    > 第三步：拓展发散 (Pose-SD)。如果学生已经提出了好问题，思考如何引导他进行反思或提出一个相似/完全不同的新问题。

    【最终输出要求（严格遵守）】
    完成内部思考后，你只能向学生输出一段自然、亲切的对话回复！
    1. 每次回复只抛出 1 到 2 个启发式问题，保持互动的“半自由”状态，像真实的聊天一样。
    2. 最小干预原则：绝对不能直接解释现象背后的科学知识。
    3. 动态调节：如果学生输入的内容太短、说“不知道”，或者提问太浅（如只问“为什么”），请给出明确的调节性提示：“你能试着换个角度，或者加上具体的条件来提问吗？”
    4. 语气亲切、鼓励，富有耐心，避免生硬死板的机器人感。
    """

    # 2. 构造符合 Ollama 接口规范的请求体,这里云端调用也是完全一样的，只要把 MODEL_NAME 替换成云端的模型名字就行，提示词
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input}
        ],
        "stream": False,  # 设为 False，让模型思考完一次性返回完整结果
        "options": {
            "temperature": 0.7 # 控制创造力，0.7 比较适合有引导性的教育对话
        }
    }

    try:
        # 3. 发送本地请求
        #本地调用
        response = requests.post(OLLAMA_API_URL, json=payload, timeout=60) 
        response.raise_for_status()
        
        # 4. 解析 Ollama 返回的结果
        result = response.json()
        return result['message']['content']
        
    except requests.exceptions.ConnectionError:
        return "【系统提示】无法连接到本地大脑，请检查电脑上的 Ollama 软件是否已启动并在后台运行。"
    except Exception as e:
        print(f"本地 AI 调用异常: {e}")
        return "AI 思考时遇到了点小问题，请重新发送试试。"
'''

# ================= 4. 工具函数 (数据库化) =================
def verify_login(username, password):
    # 先只用 username 查出这个用户
    user = User.query.filter_by(username=username).first()
    
    if user:
        is_valid_password = False
        
        # 1. 尝试用哈希算法校验 (判断是不是 scrypt 或 pbkdf2 开头的密文)
        if _is_password_hash(user.password):
            is_valid_password = check_password_hash(user.password, password)
        # 2. 兼容老账号：如果失败，尝试明文比对
        elif user.password == password:
            is_valid_password = True
            
        if is_valid_password:
            if not user.is_active: return "FROZEN"
            if not _is_password_hash(user.password):
                user.password = generate_password_hash(password)
                db.session.commit()
            return {
                'username': user.username, 'real_name': user.real_name, 
                'role': user.role, 'can_manage_users': user.can_manage_users 
            }
    return None

def get_current_task():
    """从 SQLite 获取当前任务"""
    task = db.session.get(TaskConfig, 1)
    return {
        "title": task.title, 
        "content": task.content, 
        "scaffold": task.scaffold, 
        "custom_standard": task.custom_standard
    }

def save_current_task(title, content, scaffold="", custom_standard=""):
    """保存新任务到 SQLite"""
    task = db.session.get(TaskConfig, 1)
    task.title = title
    task.content = content
    task.scaffold = scaffold
    task.custom_standard = custom_standard
    db.session.commit()

def compress_memory_background(student_id, task_name, api_key, api_url, model_name):
    """后台静默运行的记忆压缩器（不阻塞前台学生的聊天体验）"""
    with app.app_context():
        # 1. 获取该学生的记忆档案
        memory_profile = StudentMemory.query.filter_by(student_id=student_id, task_name=task_name).first()
        if not memory_profile:
            memory_profile = StudentMemory(student_id=student_id, task_name=task_name)
            db.session.add(memory_profile)
            db.session.commit()

        # 2. 查找还没被总结过的新聊天记录
        unsummarized_records = ChatRecord.query.filter(
            ChatRecord.student_id == student_id,
            ChatRecord.task_name == task_name,
            ChatRecord.id > memory_profile.last_summarized_id
        ).order_by(ChatRecord.id.asc()).all()

        # 3. 如果积累的新对话不足 4 条（即2个回合），先不急着总结，节省系统开销
        if len(unsummarized_records) < 4:
            return

        # 4. 把未总结的对话拼接成文本
        chat_text = "\n".join([f"学生: {r.question}\n导师: {r.ai_response}" for r in unsummarized_records])
        
        # 5. 呼叫大模型进行“记忆降维打击”
        prompt = f"""你是一个教育心理学与学情分析专家。请根据以下最新的师生对话记录，以及之前的学情摘要，提取出学生当前的核心认知状态。
        【之前的学情摘要】：{memory_profile.core_summary}
        
        【最新对话记录】：
        {chat_text}
        
        请输出一段不超过150字的精简摘要，重点包含：1.学生目前理解了什么结论；2.学生还有什么误区或薄弱点。注意：只输出摘要纯文本，不要任何客套话。"""

        try:
            payload = {
                "model": model_name,
                "messages": [{"role": "system", "content": prompt}],
                "temperature": 0.1 # 极低温度，保证总结的客观性
            }
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            response = requests.post(api_url, headers=headers, json=payload, timeout=120)
            new_summary = response.json()['choices'][0]['message']['content'].strip()
            
            # 6. 更新数据库中的长时记忆
            memory_profile.core_summary = new_summary
            memory_profile.last_summarized_id = unsummarized_records[-1].id
            db.session.commit()
            print(f"🧠 [长时记忆引擎] {student_id} 的学情摘要已更新并落盘！")
        except Exception as e:
            print(f"⚠️ 记忆压缩失败: {e}")


def get_text_embedding(text):
    """调用硅基流动 API (BAAI/bge-m3)，将文字转化为 1024 维向量数组"""
    if not EMBEDDING_API_KEY:
        return None
    try:
        headers = {
            "Authorization": f"Bearer {EMBEDDING_API_KEY}", 
            "Content-Type": "application/json"
        }
        payload = {
            "model": EMBEDDING_MODEL_NAME, 
            "input": text,
            "encoding_format": "float"
        }
        
        response = requests.post(EMBEDDING_API_URL, headers=headers, json=payload, timeout=10)
        result_json = response.json()
        
        # AI辅助生成：deepseek-v3-2-251201，2026-04-09 核心防雷：检查返回值
        if 'data' not in result_json:
            print(f"❌ 向量化 API 被拒绝，硅基流动返回：{result_json}")
            return None
            
        return result_json['data'][0]['embedding']
        
    except Exception as e:
        print(f"⚠️ 向量化请求发生网络或代码异常: {e}")
        return None

def save_to_vector_db_background(student_id, task_name, question, response_text, record_id):
    """后台静默线程：将每一轮的高质量对话切片存入向量库"""
    text_chunk = f"学生问：{question}\n导师答：{response_text}"
    vector = get_text_embedding(text_chunk)
    
    if vector:
        vector_collection.add(
            embeddings=[vector],
            documents=[text_chunk],
            metadatas=[{"student_id": student_id, "task_name": task_name}], # 打上标签方便过滤
            ids=[f"chat_{record_id}"] # 使用数据库的主键ID作为唯一标识
        )
        print(f"🗄️ [向量记忆] 对话切片 {record_id} 已成功入库！")


# ================= 根目录重定向 =================
@app.route('/')
def index():
    """当用户访问根目录时，自动跳转到登录页"""
    return redirect(url_for('login'))

# ================= 登录模块 =================
@app.route('/login', methods=['GET', 'POST'])
def login():

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password') # AI辅助生成：deepseek-v3-2-251201，2026-04-25 修复获取密码的字段
        
        # 去数据库比对校验
        user_info = verify_login(username, password)
        
        if user_info == "FROZEN":
            return render_template('login.html', error="🚨 您的账号已被管理员冻结，请联系导师处理！")
        elif user_info:
            session['username'] = user_info['username']
            session['real_name'] = user_info['real_name']
            session['role'] = user_info['role']
            session['can_manage_users'] = user_info['can_manage_users'] # AI辅助生成：deepseek-v3-2-251201，2026-03-27 写入全局会话
            session.permanent = True
            session["_csrf_token"] = secrets.token_urlsafe(32)
            return redirect(url_for('teacher_dashboard' if user_info['role'] == 'teacher' else 'student_chat'))
        else:
            return render_template('login.html', error="账号或密码错误")

    return render_template('login.html')


# ================= 学生交互端 =================
@app.route('/student')
def student_chat():
    current_user = _current_user()
    if not current_user or current_user.role != 'student':
        return redirect(url_for('login'))
        
    current_task = get_current_task()
    student_id = session.get('username')

    records = ChatRecord.query.filter_by(student_id=student_id).all()
    # AI辅助生成：deepseek-v3-2-251201，2026-04-09 无缝伪装：把数据库对象转换成前端熟悉的“9列列表”格式，前端一行代码都不用改！
    my_chat_history = [
        [r.time, r.student_name, r.student_id, r.question, r.ai_response, r.teacher_score, r.teacher_feedback, r.task_name, r.task_content]
        for r in records
    ]
    
    return render_template('student.html',
                           student_name=session['real_name'],
                           current_task=current_task,
                           chat_history=my_chat_history)


# ================= 聊天接口 (多 Agent 路由架构) =================

task_pool = ThreadPoolExecutor(max_workers=8)


def postprocess_record_background(student_id, task_name, question, response_text, record_id):
    with app.app_context():
        try:
            compress_memory_background(student_id, task_name, API_KEY, API_URL, CHAT_MODEL_NAME)
            save_to_vector_db_background(student_id, task_name, question, response_text, record_id)
            print(f"✅ [后台系统] 聊天记录 {record_id} 后处理完毕！")
        except Exception as e:
            print(f"⚠️ [后台系统] 聊天记录 {record_id} 后处理失败: {e}")
            db.session.rollback()

@app.route('/chat', methods=['POST'])
@role_required("student")
def chat():
    current_task = get_current_task()
    data = request.get_json(silent=True) or {}
    user_input = str(data.get('message') or "").strip()
    student_account = session.get('username')
    student_name = session.get('real_name')

    if not user_input:
        return jsonify({"error": "提问内容不能为空"}), 400
    if len(user_input) > 2000:
        return jsonify({"error": "提问内容不能超过 2000 字"}), 400
    if not API_KEY:
        return jsonify({"error": "AI 服务尚未配置，请联系管理员"}), 503

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }

    # ========================================================
    # 🕵️‍♂️ 第一步：启动“教导主任”（Router 路由节点）
    # ========================================================
    router_prompt = """你是一个极其精准的意图识别器。请分析学生的输入。
如果学生在明确询问基础名词解释、物理或科学定义（如：“什么是折射”、“变量是什么意思”、“什么是控制变量法”），请严格输出 JSON: {"intent": "qa"}
如果学生在尝试推理、提出猜测、解释现象、反问为什么，或者表示自己不知道该怎么办，请严格输出 JSON: {"intent": "socratic"}
注意：只输出纯 JSON 字符串，不要带任何 Markdown 标记。"""

    try:
        router_payload = {
            "model": CHAT_MODEL_NAME,
            "messages": [
                {"role": "system", "content": router_prompt},
                {"role": "user", "content": user_input}
            ],
            "temperature": 0.1 
        }
        
        # 意图分类
        r_response = requests.post(API_URL, headers=headers, json=router_payload, timeout=20)
        r_response.raise_for_status()
        intent_text = r_response.json()['choices'][0]['message']['content'].strip()
        
        # AI辅助生成：deepseek-v3-2-251201，2026-04-25，清洗可能带有的 markdown 符号
        cleaned_intent = intent_text.replace("```json", "").replace("```", "")
        intent = json.loads(cleaned_intent).get("intent", "socratic")
        print(f"🚀 [多Agent调度] 识别到学生意图: {intent.upper()} 专家接管。")
    except Exception as e:
        print(f"⚠️ [多Agent调度] 意图识别失败，默认交由苏格拉底导师: {e}")
        intent = "socratic" 

    # ========================================================
    # 🧑‍🏫 第二步：根据意图分配对应的“专家”（切换系统提示词）
    # ========================================================
    if intent == "qa":
        system_prompt = f"""# 角色设定
你是一位温柔、表达清晰的学科助教。你的任务是帮初中生扫清实验探究前的“知识盲区”。
当前辅导学生：{student_name}
当前任务：《{current_task.get('title')}》
材料内容：{current_task.get('content')}

# 核心动作
1. 通俗解答：当学生询问某个术语或概念时，请用初中生能听懂的生活化语言，在 2-3 句话内解释清楚。
2. 完美交接：解释完死知识后，绝对不能替学生做后续的探究推理！你必须在结尾加上一句反问，把话题重新引回当前实验，引导学生回到提出问题上，让学生发现问题、提出问题。（例如：“现在明白什么是变量了吧？那结合咱们的材料，你觉得...”）
3. 格式限制：语气亲切，总字数严格控制在 100 字以内。"""

    else:
        system_prompt = f"""# 角色设定
你是一位精通苏格拉底启发式教学的探究导师，同时精通布鲁姆认知目标分类（Bloom's Taxonomy）与问题复杂度评估。
当前辅导学生：{student_name}
当前任务：《{current_task.get('title')}》
材料内容：{current_task.get('content')}

【核心职责】：通过“提问”提供思维脚手架，引导学生深入探究。
【核心任务】：不能直接回答知识点，必须围绕上述【当前学习材料】引导学生提问。提出更深刻、更有探究价值的问题。
【核心目标】：不直接告诉学生科学原理或正确答案！而是通过诊断学生当前提问的层次，利用循序渐进的引导和认知冲突，帮助他们修补认知漏洞，提供“支架式（Scaffolding）”的升维改进建议，提升元认知能力与高阶思维。不过，涉及到基础知识的层面（比如学生对问题做出了自己的回答而非提出问题）则要先回答的到底正确与否，做出一个评价，再引导学生进行探究。

【执行工作流 (Chain-of-Thought)】 
    在回复学生之前，你必须在后台默默完成以下三步思考（切记：这三步是你的内部推理，绝对不允许输出给学生！）：
    > 第一步：隐性诊断。分析学生当前的提问处于什么层次（是只关注了表面现象？是缺乏前提条件？还是已经触及了核心机制？）。
    > 第二步：匹配梯度支架 (Pose-EMD)。根据诊断结果，在心里准备一个难度递进的启发策略：
        - 基础激活（Find Details）：引导关注材料中的具体细节或矛盾点。
        - 深入纠偏（Add Condition Constraints）：引导学生将现象与具体条件结合，例如“如果改变某一个条件，现象还会发生吗？”
        - 认知冲突（Raise Counterexamples）：举出一个极端的反例，打破学生的固有认知。
    > 第三步：拓展发散 (Pose-SD)。如果学生已经提出了好问题，思考如何引导他进行反思或提出一个相似/完全不同的新问题。

【最终输出要求（严格遵守）】
    完成内部思考后，你只能向学生输出一段自然、亲切的对话回复！
    1. 每次回复只抛出 1 到 2 个启发式问题，保持互动的“半自由”状态，像真实的聊天一样。
    2. 最小干预原则：绝对不能直接解释现象背后的科学知识。
    3. 语气亲切、鼓励，富有耐心，避免生硬死板的机器人感。
    4. 动态调节：如果学生输入的内容太短、说“不知道”，或者提问太浅（如只问“为什么”），请给出一个极其具体的思考小台阶（例如：“你能试着换个角度，或者加上具体的条件来提问吗...”）。
    """

    # ========================================================
    # 🧠 第三步：注入长时记忆 (摘要 + 向量检索 + 短期原声)
    # ========================================================
    
    # 学情摘要
    memory_profile = StudentMemory.query.filter_by(
        student_id=student_account, 
        task_name=current_task.get('title', '等待老师发布任务...')
    ).first()
    summary_text = memory_profile.core_summary if memory_profile else "暂无，这是该学生的首次探索。"

    # 历史细节 
    rag_context = ""
    try:
        question_vector = get_text_embedding(user_input)
        if question_vector:
            search_results = vector_collection.query(
                query_embeddings=[question_vector],
                n_results=2, 
                where={"student_id": student_account} #AI辅助生成：deepseek-v3-2-251201，2026-04-25 必须加上这个过滤，绝对不能串号看到别人的聊天！
            )
            
            if search_results['documents'] and search_results['documents'][0]:
                rag_context = "\n".join(search_results['documents'][0])
    except Exception as e:
        print(f"⚠️ 向量检索未命中或报错: {e}")


    enhanced_system_prompt = system_prompt + f"""
    
【该学生的长期学情画像】：
{summary_text}

【记忆检索系统提供的相关历史对话细节（如果为空则忽略）】：
{rag_context if rag_context else "无相关历史细节"}

请结合以上学情和历史细节，回答学生的最新问题。
"""
    messages = [{"role": "system", "content": enhanced_system_prompt}]

    # 短期原声
    recent_records = ChatRecord.query.filter_by(
        student_id=student_account, 
        task_name=current_task.get('title', '等待老师发布任务...')
    ).order_by(ChatRecord.id.desc()).limit(4).all()
    
    recent_records.reverse()
    
    for r in recent_records:
        if r.question:
            messages.append({"role": "user", "content": r.question})
        if r.ai_response:
            messages.append({"role": "assistant", "content": r.ai_response})
            
    # 把最新的提问加在最后
    messages.append({"role": "user", "content": user_input})

    # ========================================================
    # 🚀 第四步：专家出马，调用火山引擎发起流式对话并保存
    # ========================================================
    task_name = current_task.get('title', '未分类当前任务')
    task_content_str = current_task.get('content', '')
    new_record = ChatRecord(
        time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        student_name=student_name,
        student_id=student_account,
        question=user_input,
        ai_response="",
        task_name=task_name,
        task_content=task_content_str,
    )
    db.session.add(new_record)
    db.session.commit()
    record_id = new_record.id

    def generate_stream():
        full_ai_response = ""
        payload = {
            "model": CHAT_MODEL_NAME,
            "messages": messages,
            "stream": True,
            "temperature": 0.7 
        }
        
        try:
            response = requests.post(API_URL, headers=headers, json=payload, stream=True, timeout=60)
            response.raise_for_status()
            
            for line in response.iter_lines():
                if line:
                    decoded_line = line.decode('utf-8').strip()
                    if decoded_line.startswith("data:"):
                        json_str = decoded_line[5:].strip()
                        if json_str == "[DONE]":
                            break
                        try:
                            chunk_data = json.loads(json_str)
                            choices = chunk_data.get('choices', [])
                            if choices:
                                chunk_text = choices[0].get('delta', {}).get('content', '')
                                if chunk_text:
                                    full_ai_response += chunk_text
                                    # AI辅助生成：deepseek-v3-2-251201，2026-04-25 标准 SSE 封装：破除 Nginx/Flask 缓存屏障
                                    yield f"data: {json.dumps({'text': chunk_text}, ensure_ascii=False)}\n\n"
                        except Exception:
                            continue
                            
        except requests.exceptions.ReadTimeout:
            error_msg = "\n\n⚠️【系统提示】网络似乎有些拥堵，请稍后再试。"
            full_ai_response += error_msg
            yield f"data: {json.dumps({'text': error_msg}, ensure_ascii=False)}\n\n"
        except Exception as e:
            error_msg = f"\n\n⚠️【系统提示】连接断开 ({str(e)})"
            full_ai_response += error_msg
            yield f"data: {json.dumps({'text': error_msg}, ensure_ascii=False)}\n\n"

        finally:
            try:
                record = db.session.get(ChatRecord, record_id)
                if record:
                    record.ai_response = full_ai_response
                    db.session.commit()
                    task_pool.submit(
                        postprocess_record_background,
                        student_account,
                        task_name,
                        user_input,
                        full_ai_response,
                        record_id,
                    )
            except Exception as save_error:
                db.session.rollback()
                print(f"❌ 聊天记录 {record_id} 更新失败: {save_error}")

        yield f"data: {json.dumps({'text': '[DONE_MARKER]'}, ensure_ascii=False)}\n\n"
        
    response = Response(stream_with_context(generate_stream()), mimetype='text/event-stream')
    response.headers["Cache-Control"] = "no-cache, no-store"
    response.headers["X-Accel-Buffering"] = "no"
    return response
    

# ================= 教师控制台路由 =================
@app.route('/teacher', methods=['GET', 'POST'])
def teacher_dashboard():
    teacher = _current_user()
    if not teacher or teacher.role != 'teacher':
        return redirect(url_for('login'))
    
    # AI辅助生成：deepseek-v3-2-251201，2026-04-01 处理前端“无刷新”提交任务的 POST 请求
    if request.method == 'POST':
        new_title = str(request.form.get('task_title') or "").strip()
        new_content = str(request.form.get('task_content') or "").strip()
        new_scaffold = str(request.form.get('task_scaffold') or "").strip()
        if len(new_title) > 200 or len(new_content) > 20000 or len(new_scaffold) > 10000:
            return _json_error("任务标题、内容或支架超过长度限制")
        
        custom_standard_text = ""
        clear_rubric = request.form.get('clear_rubric') 
        
        if clear_rubric == 'yes':
            pass 
        else:
            uploaded_file = request.files.get('rubric_file')
            if uploaded_file and uploaded_file.filename != '':
                try:
                    custom_standard_text = uploaded_file.read().decode('utf-8')
                    if len(custom_standard_text) > 100000:
                        return _json_error("评价标准文件内容不能超过 10 万字")
                except Exception as e:
                    return jsonify({"status": "error", "message": "文件读取失败"}), 400
            else:
                old_task = get_current_task()
                custom_standard_text = old_task.get('custom_standard', '')

        if new_title and new_content:
            try:
                save_current_task(new_title, new_content, new_scaffold, custom_standard_text)
                return jsonify({"status": "success", "message": "任务及评价标准同步成功！"})
            except Exception as e:
                return jsonify({"status": "error", "message": str(e)}), 500
        
        return jsonify({"status": "error", "message": "标题和内容不能为空"}), 400

    # 获取当前任务与历史数据
    current_task = get_current_task()
    # 数据库查询：获取全班所有实验记录，并按时间倒序排列 (最新在最上)
    allowed_student_ids = _teacher_student_ids(teacher)
    db_records = ChatRecord.query.filter(
        ChatRecord.student_id.in_(allowed_student_ids)
    ).order_by(ChatRecord.id.desc()).all()
    
    # 伪装成前端需要的列表
    records = [
        [r.time, r.student_name, r.student_id, r.question, r.ai_response, r.teacher_score, r.teacher_feedback, r.task_name, r.task_content]
        for r in db_records
    ]
    
    return render_template('teacher_pc.html',
                           teacher_name=session['real_name'],
                           current_task=current_task,
                           records=records,
                           can_manage_users=teacher.can_manage_users)


# ================= 退出登录 =================
@app.route('/logout')
def logout():
    """清除会话，安全退出"""
    session.clear() 
    return redirect(url_for('login'))


# ================= 账号管理模块 API (仅限管理员调用) =================
@app.route('/api/users/list')
@role_required("teacher")
def list_users():
    curr_user = _current_user()

    if curr_user.can_manage_users:
        # 超管看所有人
        users = User.query.all()
    else:
        managed_classes = _managed_class_names(curr_user)
        users = User.query.filter(
            (User.username == curr_user.username)
            | ((User.role == "student") & User.class_name.in_(managed_classes))
        ).all()

    return jsonify([{
        "id": u.id, 
        "username": u.username, 
        # "password": u.password, 
        "role": u.role, 
        "real_name": u.real_name, 
        "is_active": u.is_active, 
        "can_manage_users": u.can_manage_users,
        "class_name": u.class_name or "",
        "managed_classes": u.managed_classes or ""} 
        for u in users])

@app.route('/api/users/add', methods=['POST'])
@admin_required
def add_user():
    """新增账号"""
    data = request.get_json(silent=True) or {}
    username = str(data.get("username") or "").strip()
    real_name = str(data.get("real_name") or "").strip()
    password = str(data.get("password") or "")
    role = str(data.get("role") or "").strip()
    if not _valid_username(username) or not real_name or len(real_name) > 50:
        return _json_error("账号或姓名格式不正确")
    if len(password) < 8 or len(password) > 128:
        return _json_error("密码长度必须为 8–128 位")
    if role not in {"student", "teacher"}:
        return _json_error("用户角色无效")
    # 检查账号是否重复
    if User.query.filter_by(username=username).first():
        return jsonify({"status": "error", "message": "该学号/账号已存在！"}), 400
    
    new_user = User(
        username=username, password=generate_password_hash(password),
        role=role, real_name=real_name
    )
    db.session.add(new_user)
    db.session.commit()
    return jsonify({"status": "success", "message": "账号添加成功"})

@app.route('/api/users/toggle_status', methods=['POST'])
@admin_required
def toggle_user_status():
    """冻结/解冻账号"""
    data = request.get_json(silent=True) or {}
    user = db.session.get(User, data.get('id'))
    if user:
        # AI辅助生成：deepseek-v3-2-251201，2026-04-07 核心修复：越权自我冻结漏洞
        if user.username == session.get('username'):
            return jsonify({"status": "error", "message": "不能冻结自己！"}), 400
        user.is_active = not user.is_active
        db.session.commit()
        return jsonify({"status": "success", "message": "状态已更新"})
    return jsonify({"status": "error", "message": "用户不存在"}), 404

@app.route('/api/users/delete', methods=['POST'])
@admin_required
def delete_user():
    """删除账号及关联的所有实验记录"""
    data = request.get_json(silent=True) or {}
    user = db.session.get(User, data.get('id'))
    
    if user:
        if user.username == session.get('username'):
            return jsonify({"status": "error", "message": "不能删除自己！"}), 400
        
        try:
            deleted_username = user.username
            # AI辅助生成：deepseek-v3-2-251201，2026-04-07 核心修复：跨库级联删除！
            # 在删除用户之前，先去 ChatRecord 业务表里，把 student_id 等于该账号的所有聊天和批阅记录统统删掉
            ChatRecord.query.filter_by(student_id=user.username).delete()
            StudentMemory.query.filter_by(student_id=user.username).delete()
            
            # 删除用户账号本身
            db.session.delete(user)
            
            # 一次性提交所有更改（SQLAlchemy 会自动安全地处理这两个数据库的修改）
            db.session.commit()
            try:
                vector_collection.delete(where={"student_id": deleted_username})
            except Exception as vector_error:
                print(f"⚠️ 用户已删除，但向量记忆清理失败: {vector_error}")
            return jsonify({"status": "success", "message": "账号及其所有实验记录已彻底清除"})
            
        except Exception as e:
            db.session.rollback() # 如果中途报错，立刻回滚，保护数据库安全
            return jsonify({"status": "error", "message": f"删除失败: {str(e)}"}), 500
            
    return jsonify({"status": "error", "message": "用户不存在"}), 404

#修改密码
@app.route('/api/user/change_password', methods=['POST'])
@role_required("teacher", "student")
def change_password():
    data = request.get_json(silent=True) or {}
    new_password = str(data.get('new_password') or "")
    
    if len(new_password) < 8 or len(new_password) > 128:
        return jsonify({"status": "error", "message": "新密码长度必须为 8–128 位"}), 400
        
    user = User.query.filter_by(username=session['username']).first()
    if user:
        user.password = generate_password_hash(new_password)
        db.session.commit()
        return jsonify({"status": "success", "message": "密码修改成功！"})
    return jsonify({"status": "error", "message": "用户不存在"}), 404

#批量新增账号，支持批量新增学生账号和教师账号，默认密码为123456
@app.route('/api/users/batch_add', methods=['POST'])
@admin_required
def batch_add_users():
    data = request.get_json(silent=True) or {}
    user_list = data.get('users', [])
    if not isinstance(user_list, list) or len(user_list) > 500:
        return _json_error("批量用户数据格式不正确")
    
    success_count = 0
    errors = []
    
    for u_data in user_list:
        username = str(u_data.get("username") or "").strip()
        real_name = str(u_data.get("real_name") or "").strip()
        role = str(u_data.get("role") or "student").strip()
        if not _valid_username(username) or not real_name or len(real_name) > 50:
            errors.append("存在账号或姓名格式不正确的数据，已跳过")
            continue
        if role not in {"student", "teacher"}:
            errors.append(f"账号 {username} 的角色无效，已跳过")
            continue
        # 检查是否已存在
        if User.query.filter_by(username=username).first():
            errors.append(f"账号 {username} 已存在，跳过")
            continue

        default_hashed_pwd = generate_password_hash(DEFAULT_USER_PASSWORD)
            
        new_user = User(
            username=username,
            password=default_hashed_pwd,  # 使用上面算好的密文  
            role=role,
            real_name=real_name,
            class_name=str(u_data.get('class_name') or "")[:50],
            managed_classes=str(u_data.get('managed_classes') or "")[:1000],
        )
        db.session.add(new_user)
        success_count += 1
    
    try:
        db.session.commit()
        return jsonify({
            "status": "success", 
            "message": f"成功创建 {success_count} 个账号",
            "errors": errors
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500

#班级划分/分配
@app.route('/api/classes/batch_assign', methods=['POST'])
@admin_required
def batch_assign_classes():
    data = request.get_json(silent=True) or {}
    action = data.get('action') # 'assign_students' 或 'assign_teacher'
    
    if action == 'assign_students':
        # 批量给学生划分班级 {"student_ids": ["2101", "2102"], "class_name": "21级01班"}
        student_ids = data.get('student_ids', [])
        target_class = str(data.get('class_name') or "").strip()
        if (
            not isinstance(student_ids, list)
            or len(student_ids) > 500
            or not target_class
            or len(target_class) > 50
        ):
            return _json_error("班级分配数据格式不正确")
        User.query.filter(
            User.role == "student",
            User.username.in_(student_ids),
        ).update({User.class_name: target_class}, synchronize_session=False)
        
    elif action == 'assign_teacher':
        # 给老师分配管理班级 {"teacher_id": "t01", "managed_classes": "01班,02班"}
        teacher = User.query.filter_by(username=data.get('teacher_id')).first()
        managed_classes = str(data.get('managed_classes') or "").strip()
        if not teacher or teacher.role != "teacher" or len(managed_classes) > 1000:
            return _json_error("教师或班级范围无效")
        teacher.managed_classes = managed_classes
    else:
        return _json_error("未知的班级分配操作")
            
    db.session.commit()
    return jsonify({"status": "success", "message": "班级划分/分配成功"})

@app.route('/api/users/admin_reset_password', methods=['POST'])
@role_required("teacher")
def admin_reset_password():
    data = request.get_json(silent=True) or {}
    user = db.session.get(User, data.get('id'))
    teacher = _current_user()
    new_password = str(data.get("new_password") or "")
    if not user:
        return _json_error("用户不存在", 404)
    if len(new_password) < 8 or len(new_password) > 128:
        return _json_error("密码长度必须为 8–128 位")
    if user.username != teacher.username and not _teacher_can_access_student(teacher, user):
        return _json_error("无权重置该用户密码", 403)

    user.password = generate_password_hash(new_password)
    db.session.commit()
    return jsonify({"status": "success"})


@app.route('/api/users/toggle_admin', methods=['POST'])
@admin_required
def toggle_admin():
    data = request.get_json(silent=True) or {}
    user = db.session.get(User, data.get("id"))
    if not user or user.role != "teacher":
        return _json_error("目标教师不存在", 404)
    if user.username == session.get("username"):
        return _json_error("不能修改自己的管理员权限")
    user.can_manage_users = not user.can_manage_users
    db.session.commit()
    return jsonify({"status": "success", "can_manage_users": user.can_manage_users})


# ================= 新增：保存教师批阅记录的接口 =================
@app.route('/save_evaluation', methods=['POST'])
@role_required("teacher")
def save_evaluation():
    try:
        data = request.get_json(silent=True) or {}
        # AI辅助生成：deepseek-v3-2-251201，2026-04-10 数据库更新操作，极其安全和优雅，绝不发生并发冲突！
        record = ChatRecord.query.filter_by(time=data.get('time'), student_id=data.get('id')).first()
        
        if record:
            teacher = _current_user()
            student = User.query.filter_by(username=record.student_id).first()
            if not _teacher_can_access_student(teacher, student):
                return _json_error("无权批阅该学生记录", 403)
            score = str(data.get("score") or "").strip().upper()
            feedback = str(data.get("feedback") or "").strip()
            if score not in {"A", "B", "C", "D", "E", "F"}:
                return _json_error("评分等级无效")
            if len(feedback) > 4000:
                return _json_error("反馈内容不能超过 4000 字")
            record.teacher_score = score
            record.teacher_feedback = feedback
            db.session.commit() # 提交事务
            return jsonify({"status": "success", "message": "保存成功"})
        else:
            return jsonify({"status": "error", "message": "在数据库中找不到该记录"}), 404
            
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    

# ================= 教师端：导出实验数据接口 =================
# AI辅助生成：deepseek-v3-2-251201，2026-04-01 数据库导出操作，极其安全和优雅，绝不发生并发冲突！
@app.route('/export_data')
def export_data():
    # 1. 权限校验
    teacher = _current_user()
    if not teacher or teacher.role != 'teacher':
        return redirect(url_for('login'))

    try:
        # 2. 从 SQLite 数据库中拉取所有记录：先按学生分类（学号），再按时间正序
        # time 字段格式为 YYYY-MM-DD HH:MM:SS，可直接用于字符串排序
        allowed_student_ids = _teacher_student_ids(teacher)
        records = ChatRecord.query.filter(
            ChatRecord.student_id.in_(allowed_student_ids)
        ).order_by(
            ChatRecord.student_id.asc(),
            ChatRecord.time.asc(),
            ChatRecord.id.asc()  # 同一时间戳下兜底，保证顺序稳定
        ).all()

        # 3. 在内存中创建一个虚拟的文本文件
        si = io.StringIO()
        writer = csv.writer(si)

        # 4. 写入表头
        writer.writerow(['记录时间', '姓名', '学号', '学生提问', 'AI元认知提示', '教师评分', '教师反馈', '所属任务', '任务背景材料'])

        # 5. 遍历数据库对象，写入数据
        for r in records:
            writer.writerow([
                _safe_csv_cell(r.time),
                _safe_csv_cell(r.student_name),
                _safe_csv_cell(r.student_id),
                _safe_csv_cell(r.question),
                _safe_csv_cell(r.ai_response),
                _safe_csv_cell(r.teacher_score),
                _safe_csv_cell(r.teacher_feedback),
                _safe_csv_cell(r.task_name),
                _safe_csv_cell(r.task_content),
            ])

        # 6. 将内存中的文本转化为带有 BOM 的 UTF-8 字节流 (防止 Excel 中文乱码的核心魔法)
        output = make_response(si.getvalue().encode('utf-8-sig'))
        
        # 7. 设置 HTTP 响应头，告诉浏览器“这是一个需要下载的附件”
        output.headers["Content-Disposition"] = "attachment; filename=AI_Tutor_Experiment_Data.csv"
        output.headers["Content-type"] = "text/csv; charset=utf-8-sig"
        
        return output
        
    except Exception as e:
        return f"导出数据失败: {str(e)}", 500


# ================= 真实 AI 智能助教评估接口 =================
@app.route('/api/ai_evaluate', methods=['POST'])
@role_required("teacher", "student")
def ai_evaluate():
    data = request.get_json(silent=True) or {}
    student_question = str(data.get('question') or "").strip()
    if not student_question or len(student_question) > 2000:
        return _json_error("待评价问题不能为空且不能超过 2000 字")
    if not API_KEY:
        return _json_error("AI 服务尚未配置，请联系管理员", 503)

    # 始终使用服务端当前任务，避免客户端伪造评价情境。
    current_task = get_current_task()
    task_title = current_task.get("title", "未知探究任务")
    task_content = current_task.get("content", "无详细背景说明")
    custom_standard = current_task.get('custom_standard', '').strip()

    # Prompt 分流
    if custom_standard:
        # =========================================================
        # RAG 模式：老师上传了自定义标准，强制覆盖默认逻辑
        # =========================================================
        system_prompt = f"""【角色设定】
你是一位精通启发式教学、布鲁姆认知目标分类（Bloom's Taxonomy）与问题复杂度评估的资深学科提问评价导师。你的核心任务是：基于特定情境，对学生提出的问题进行多维度诊断与评级，并在建立安全鼓励的氛围下，提供“支架式（Scaffolding）”的升维改进建议。当前任务已开启【自定义评价标准库】(RAG 模式)。
你必须**完全抛弃**自带的任何默认评价体系（如布鲁姆分类法），严格地按照下方老师提供的【自定义评价标准】对学生提出的问题进行定级和反馈。

【探究情境】
- 标题：{task_title}
- 内容：{task_content}

【教师上传的自定义评价标准 (核心依据)】
{custom_standard}

【评估工作流与输出要求】
1. 仔细阅读并理解上方【自定义评价标准】中的各个等级定义。
2. 对照该标准，对学生的问题进行诊断。指出亮点和局限。
3. 基于该标准，给出一句启发式的改进建议。
4. 无论老师的标准分为几级，请你将其智能映射到 A、B、C、D、E、F 这几个前端可识别的字母分级中（最高级映射为A，最低或无效映射为F）。

【严格的 JSON 输出格式】
绝不能包含任何 Markdown 标记。必须严格输出以下 JSON：
{{
  "score": "A或B或C或D或E或F",
  "analysis": "根据老师上传的标准，解释为什么给这个等级。先肯定闪光点，再指出局限。（100字内）",
  "suggestion": "基于此评级，给学生提供一句具体的改进建议。（100字内）"
}}
"""
    else:
        # =========================================================
        # 默认模式：布鲁姆 5 层级标准
        # =========================================================
        system_prompt = f"""【角色设定】
你是一位精通启发式教学、布鲁姆认知目标分类（Bloom's Taxonomy）与问题复杂度评估的资深学科提问评价导师。你的核心任务是：基于特定情境，对学生提出的问题进行多维度诊断与评级，并在建立安全鼓励的氛围下，提供“支架式（Scaffolding）”的升维改进建议。

【输入信息】
- 探究情境/原题：{task_title} - {task_content}
- 待评估的学生问题：（前端将动态传入）

【评估工作流（内部思考过程）】
请在内心严格按照以下四个模块进行深度审视：

模块一：有效性与可解性筛查（底线防守）
- 情境契合度：判断该问题是否紧扣上述背景材料。
- 可解性判断：基于已知条件，该问题是否可解（Solvable）？如果不可解或偏题，指明缺失了什么条件或逻辑漏洞（若不可解，直接评为 F 级）。

模块二：认知层级评级（布鲁姆1-5级）
- Level 1 记忆：仅要求回忆基本事实或提取表面信息（是什么？对不对？）。
- Level 2 理解：要求用自己的话解释、分类或重述信息（有什么特点？）。
- Level 3 应用：要求将规则或公式应用于当前情境进行求解（为什么？怎么做？）。
- Level 4 分析：要求分解结构，寻找深层因果关系、隐藏条件或模式对比（为什么？联系是什么？）。
- Level 5 评价与创造：包含“如果...那么...”的条件假设，改变了原有情境，或引入新变量进行探究设计。

模块三：质量诊断与肯定（情感与逻辑并重）
- 结构复杂度：分析问题是简单的赋值问题，还是包含条件与关系的多步复杂问题。
- 闪光点肯定：找出学生提问中的亮点（如视角独特、注意到了某个隐藏现象等）给予肯定，以建立安全、鼓励的提问环境。

模块四：升维改进建议（定向支架）
- 绝对不要直接给出更高阶的问题！请根据当前层级，提供1-2个启发式建议，引导学生向更高层级（Level 4/5）修改：
  -> 若为 Level 1/2：引导将封闭式转为开放式（如“能否试着把‘这是不是’改成‘哪些因素会影响’？”）。
  -> 若为 Level 3：引导加入控制变量（如“试着改变情境中的某个条件，重新提问”）。
  -> 若为 Level 4/5：肯定其探究价值，引导思考如何通过实验设计或逻辑推理来验证这个假设。

【严格的 JSON 输出格式】
绝不能包含任何 Markdown 标记。必须严格输出以下 JSON：
评级映射：Level 5->A, Level 4->B, Level 3->C, Level 2->D, Level 1->E, 无效/不可解->F。

{{
  "score": "A或B或C或D或E或F",
  "analysis": "先指出闪光点，再简要说明结构复杂度或局限(比如，关注到了...但缺乏...)。（100字内）",
  "suggestion": "给出一句启发式的升维改进建议（100字内），以及一个具体的示例问题（比如你可以试着这样问老师：‘____（这里由AI生成一个示范问题）____’）"
}}
"""

    try:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}" 
        }
        
        payload = {
            "model": EVAL_MODEL_NAME, 
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"学生提出的问题是：{student_question}"}
            ],
            "temperature": 0.1  
        }
        
        response = requests.post(API_URL, headers=headers, json=payload, timeout=120)
        response.raise_for_status() 
        
        ai_reply_text = response.json()['choices'][0]['message']['content']
        cleaned_text = ai_reply_text.strip().replace("```json", "").replace("```", "")
        
        # AI辅助生成：deepseek-v3-2-251201，2026-04-12 核心修复区：正确解析 JSON 并拼接反馈
        result = json.loads(cleaned_text)

        ai_score = str(result.get("score") or "").strip().upper()
        if ai_score not in {"A", "B", "C", "D", "E", "F"}:
            ai_score = "F"
        analysis = str(result.get("analysis") or "未提供分析")[:1000]
        suggestion = str(result.get("suggestion") or "未提供建议")[:1000]

        # AI辅助生成：deepseek-v3-2-251201，2026-04-12 将分析和建议拼接成一段带有换行和图标的完整反馈，传给前端！ 增加一个 \n，形成真实的物理空行
        final_feedback = f"{analysis}\n\n💡 导师建议：{suggestion}"
        
        return jsonify({
            "score": ai_score,
            "feedback": final_feedback  
        })

    except Exception as e:
        print(f"❌ AI 评估出错: {e}")
        return jsonify({"error": "Evaluation Failed"}), 500



if __name__ == '__main__':
    if webview is None:
        raise RuntimeError("桌面模式需要安装 pywebview；服务器部署请使用 Gunicorn 或 uWSGI 启动 app:app")
    window = webview.create_window('AI 探究思维助手', app, width=1280, height=800)
    webview.start()
